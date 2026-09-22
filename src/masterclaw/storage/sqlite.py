from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from masterclaw.domain.actions import RollRecord
from masterclaw.domain.characters import CharacterState, Condition, PlotItem
from masterclaw.domain.mechanics import (
    CharacterSheet,
    Flag,
    FlagType,
    TemporaryBonus,
    TemporaryBonusType,
    Trait,
)
from masterclaw.domain.models import (
    ChannelState,
    GameLifecycle,
    InboxStatus,
    IncomingAttachment,
    IncomingMessage,
)
from masterclaw.domain.outcomes import CanonicalOutcomePatch
from masterclaw.domain.progression import (
    XP_INTERVAL_SECONDS,
    ActivityUpdate,
    credited_activity_seconds,
)
from masterclaw.domain.state import (
    GameState,
    NarratorRightsLevel,
    PendingInteraction,
    PendingKind,
    PendingStatus,
    ReserveRecoveryMode,
    WorldState,
)
from masterclaw.telemetry import stage_span

_MESSAGEABLE_DISCORD_CHANNEL_KINDS = frozenset(
    {
        "news",
        "news_thread",
        "private_thread",
        "public_thread",
        "text",
        "thread",
    }
)

BASE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS inbox_messages (
    event_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    game_id TEXT,
    ingress_game_id TEXT,
    routing_lifecycle TEXT,
    author_id TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    scene_id TEXT,
    scene_participants_json TEXT NOT NULL DEFAULT '[]',
    guild_id TEXT,
    parent_channel_id TEXT,
    reply_to_event_id TEXT,
    reply_to_author_id TEXT,
    reply_context TEXT,
    attachments_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    transport_attempts INTEGER NOT NULL DEFAULT 0,
    provider_attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    error TEXT,
    handler_result_json TEXT
);
CREATE INDEX IF NOT EXISTS inbox_pending_order
    ON inbox_messages(status, created_at, event_id);
CREATE TABLE IF NOT EXISTS decision_checkpoints (
    event_id TEXT NOT NULL,
    pipeline_key TEXT NOT NULL,
    output_type TEXT NOT NULL,
    game_id TEXT,
    input_fingerprint TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(event_id, pipeline_key)
);
CREATE INDEX IF NOT EXISTS decision_checkpoints_game
    ON decision_checkpoints(game_id, created_at, event_id);
CREATE TABLE IF NOT EXISTS event_operations (
    event_id TEXT PRIMARY KEY,
    operation_type TEXT NOT NULL,
    game_id TEXT,
    channel_id TEXT,
    input_fingerprint TEXT NOT NULL,
    input_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS event_operations_game
    ON event_operations(game_id, created_at, event_id);
CREATE TABLE IF NOT EXISTS channel_bindings (
    channel_id TEXT PRIMARY KEY,
    game_id TEXT,
    lifecycle TEXT,
    inherited_from_channel_id TEXT,
    revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS monitored_channels (
    channel_id TEXT PRIMARY KEY,
    inherited_from_channel_id TEXT,
    enabled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS discord_channels (
    channel_id TEXT PRIMARY KEY,
    guild_id TEXT,
    parent_channel_id TEXT,
    kind TEXT NOT NULL DEFAULT 'unknown',
    observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS games (
    game_id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL,
    lifecycle TEXT NOT NULL,
    progression_enabled INTEGER NOT NULL DEFAULT 0,
    narrator_rights_level TEXT NOT NULL DEFAULT 'minor',
    locale TEXT NOT NULL DEFAULT 'ru',
    narrative_channel_id TEXT,
    revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS game_rules (
    game_id TEXT PRIMARY KEY REFERENCES games(game_id) ON DELETE CASCADE,
    reserve_recovery_mode TEXT NOT NULL DEFAULT 'both'
);
CREATE TABLE IF NOT EXISTS worlds (
    world_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    content_json TEXT NOT NULL DEFAULT '{}',
    revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS world_projects (
    world_id TEXT PRIMARY KEY REFERENCES worlds(world_id) ON DELETE CASCADE,
    active_channel_id TEXT UNIQUE,
    stage TEXT NOT NULL DEFAULT 'collecting',
    brief TEXT NOT NULL,
    settings_json TEXT NOT NULL DEFAULT '{}',
    sources_json TEXT NOT NULL DEFAULT '{}',
    revision INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_world_project_per_channel
    ON world_projects(active_channel_id) WHERE active_channel_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS scenes (
    scene_id TEXT PRIMARY KEY,
    game_id TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    state_json TEXT NOT NULL DEFAULT '{}',
    revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS player_locations (
    game_id TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
    player_id TEXT NOT NULL,
    scene_id TEXT NOT NULL REFERENCES scenes(scene_id),
    revision INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(game_id, player_id)
);
CREATE TABLE IF NOT EXISTS characters (
    character_id TEXT PRIMARY KEY,
    game_id TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
    player_id TEXT NOT NULL,
    biography TEXT NOT NULL,
    sheet_json TEXT NOT NULL,
    conditions_json TEXT NOT NULL DEFAULT '[]',
    plot_items_json TEXT NOT NULL DEFAULT '[]',
    experience_earned INTEGER NOT NULL DEFAULT 0,
    experience_spent INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 0,
    UNIQUE(game_id, player_id)
);
CREATE TABLE IF NOT EXISTS session_activity (
    game_id TEXT PRIMARY KEY REFERENCES games(game_id) ON DELETE CASCADE,
    last_event_at TEXT,
    active_seconds INTEGER NOT NULL DEFAULT 0,
    awarded_intervals INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pending_interactions (
    interaction_id TEXT PRIMARY KEY,
    game_id TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
    player_id TEXT NOT NULL,
    origin_channel_id TEXT,
    scene_id TEXT,
    kind TEXT NOT NULL,
    prompt TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'open',
    revision INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS pending_player_lookup
    ON pending_interactions(game_id, player_id, status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_pending_per_player
    ON pending_interactions(game_id, player_id) WHERE status = 'open';
CREATE TABLE IF NOT EXISTS domain_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    causation_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(causation_id, event_type)
);
CREATE TABLE IF NOT EXISTS rolls (
    roll_id TEXT PRIMARY KEY,
    interaction_id TEXT NOT NULL UNIQUE REFERENCES pending_interactions(interaction_id),
    confirmation_event_id TEXT NOT NULL UNIQUE,
    game_id TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
    character_id TEXT NOT NULL REFERENCES characters(character_id),
    pool_size INTEGER NOT NULL,
    reserve_spent INTEGER NOT NULL,
    help_dice INTEGER NOT NULL DEFAULT 0,
    difficulty INTEGER NOT NULL,
    dice_json TEXT NOT NULL,
    hits INTEGER NOT NULL,
    narrator_rights TEXT NOT NULL,
    reserve_after INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS roll_helpers (
    interaction_id TEXT NOT NULL REFERENCES pending_interactions(interaction_id),
    helper_character_id TEXT NOT NULL REFERENCES characters(character_id),
    helper_player_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(interaction_id, helper_character_id)
);
CREATE TABLE IF NOT EXISTS outbox_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    channel_id TEXT NOT NULL,
    content TEXT NOT NULL,
    embed_json TEXT,
    kind TEXT NOT NULL DEFAULT 'message',
    source_event_id TEXT,
    source_author_id TEXT,
    source_guild_id TEXT,
    discord_nonce TEXT,
    discord_message_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    delivered_at TEXT,
    delivery_claim TEXT,
    claimed_at TEXT,
    next_attempt_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE TABLE IF NOT EXISTS assistant_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_event_id TEXT NOT NULL REFERENCES inbox_messages(event_id) ON DELETE CASCADE,
    channel_id TEXT NOT NULL,
    recipient_author_id TEXT NOT NULL,
    content TEXT NOT NULL,
    ordinal INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_event_id, ordinal)
);
"""

TELEMETRY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT REFERENCES games(game_id) ON DELETE SET NULL,
    trace_id TEXT,
    channel_id TEXT,
    event_id TEXT,
    role TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_fingerprint TEXT NOT NULL,
    response_id TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL,
    cost REAL NOT NULL DEFAULT 0,
    success INTEGER NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS llm_calls_game_time ON llm_calls(game_id, created_at, id);
CREATE TABLE IF NOT EXISTS stage_spans (
    span_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    parent_span_id TEXT,
    game_id TEXT,
    channel_id TEXT,
    event_id TEXT,
    stage TEXT NOT NULL,
    component TEXT NOT NULL,
    operation TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    attributes_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    started_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS stage_spans_time ON stage_spans(started_at, span_id);
CREATE INDEX IF NOT EXISTS stage_spans_stage_time ON stage_spans(stage, started_at);
CREATE INDEX IF NOT EXISTS stage_spans_trace ON stage_spans(trace_id, started_at);
CREATE INDEX IF NOT EXISTS stage_spans_game_time ON stage_spans(game_id, started_at);
CREATE TABLE IF NOT EXISTS lexicon_candidates (
    event_id TEXT PRIMARY KEY,
    scenario TEXT NOT NULL,
    command TEXT NOT NULL,
    normalized_phrase TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS lexicon_candidates_time
    ON lexicon_candidates(created_at, event_id);
"""

SCHEMA_SQL = BASE_SCHEMA_SQL + TELEMETRY_SCHEMA_SQL
SCHEMA_VERSION = 10
OUTBOX_RETRY_DELAYS_SECONDS = (5, 30, 120, 300, 900)
INBOX_CHANNEL_RETRY_DELAYS_SECONDS = (5, 30, 120, 300, 900)
INBOX_PROVIDER_RETRY_DELAYS_SECONDS = (5, 30, 120, 300)
INBOX_PROVIDER_MAX_ATTEMPTS = len(INBOX_PROVIDER_RETRY_DELAYS_SECONDS) + 1


def _discord_nonce(idempotency_key: str) -> str:
    """Return a stable Discord nonce small enough for every supported API version."""
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]


def _compact_interaction_text(value: str, *, limit: int) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _sheet_payload(sheet: CharacterSheet) -> dict[str, object]:
    return {
        "name": sheet.name,
        "traits": [
            {"name": trait.name, "level": trait.level, "aspects": list(trait.aspects)}
            for trait in sheet.traits
        ],
        "flags": [
            {"text": flag.text, "type": flag.type.value, "locked": flag.locked}
            for flag in sheet.flags
        ],
        "reserve_current": sheet.reserve_current,
        "reserve_maximum": sheet.reserve_maximum,
        "temporary_bonuses": [
            {
                "bonus_id": bonus.bonus_id,
                "type": bonus.type.value,
                "trigger": bonus.trigger,
            }
            for bonus in sheet.temporary_bonuses
        ],
    }


class SQLiteStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        return row is not None

    @classmethod
    def _migrate_legacy_world_workspaces(cls, connection: sqlite3.Connection) -> None:
        """Move the legacy channel-keyed rows or fail without deleting their source."""
        if not cls._table_exists(connection, "world_workspaces"):
            return
        rows = connection.execute(
            """SELECT channel_id, world_id, stage, brief, settings_json,
                      sources_json, revision
               FROM world_workspaces ORDER BY channel_id, world_id"""
        ).fetchall()
        for row in rows:
            existing = connection.execute(
                """SELECT world_id, active_channel_id, stage, brief, settings_json,
                          sources_json, revision
                   FROM world_projects WHERE world_id = ?""",
                (row["world_id"],),
            ).fetchone()
            if existing is not None:
                same_project = (
                    existing["active_channel_id"] == row["channel_id"]
                    and existing["stage"] == row["stage"]
                    and existing["brief"] == row["brief"]
                    and existing["settings_json"] == row["settings_json"]
                    and existing["sources_json"] == row["sources_json"]
                    and existing["revision"] == row["revision"]
                )
                if same_project:
                    continue
                raise RuntimeError(
                    "legacy world workspace conflicts with an existing world project: "
                    f"{row['world_id']}"
                )
            channel_owner = connection.execute(
                "SELECT world_id FROM world_projects WHERE active_channel_id = ?",
                (row["channel_id"],),
            ).fetchone()
            if channel_owner is not None:
                raise RuntimeError(
                    "legacy world workspace conflicts with an active channel project: "
                    f"{row['channel_id']}"
                )
            connection.execute(
                """INSERT INTO world_projects
                   (world_id, active_channel_id, stage, brief, settings_json,
                    sources_json, revision)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["world_id"],
                    row["channel_id"],
                    row["stage"],
                    row["brief"],
                    row["settings_json"],
                    row["sources_json"],
                    row["revision"],
                ),
            )
        connection.execute("DROP TABLE world_workspaces")

    def initialize(self) -> None:
        with closing(self.connect()) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS schema_version (
                       version INTEGER PRIMARY KEY,
                       applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                   )"""
            )
            current = int(
                connection.execute(
                    "SELECT COALESCE(MAX(version), 0) AS version FROM schema_version"
                ).fetchone()["version"]
            )
            if current > SCHEMA_VERSION:
                raise RuntimeError(
                    f"unsupported database schema {current}; latest is {SCHEMA_VERSION}"
                )
            connection.commit()
            connection.executescript(SCHEMA_SQL)
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                current = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(version), 0) AS version FROM schema_version"
                    ).fetchone()["version"]
                )
                if current > SCHEMA_VERSION:
                    raise RuntimeError(
                        f"unsupported database schema {current}; latest is {SCHEMA_VERSION}"
                    )
                llm_columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(llm_calls)").fetchall()
                }
                if "prompt_fingerprint" not in llm_columns:
                    connection.execute(
                        """ALTER TABLE llm_calls
                           ADD COLUMN prompt_fingerprint TEXT NOT NULL DEFAULT ''"""
                    )
                for column in ("trace_id", "channel_id", "event_id"):
                    if column not in llm_columns:
                        connection.execute(f"ALTER TABLE llm_calls ADD COLUMN {column} TEXT")
                outbox_columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(outbox_messages)").fetchall()
                }
                for column in ("delivery_claim", "claimed_at", "embed_json", "next_attempt_at"):
                    if column not in outbox_columns:
                        connection.execute(f"ALTER TABLE outbox_messages ADD COLUMN {column} TEXT")
                outbox_additions = {
                    "kind": "TEXT NOT NULL DEFAULT 'message'",
                    "source_event_id": "TEXT",
                    "source_author_id": "TEXT",
                    "source_guild_id": "TEXT",
                    "discord_nonce": "TEXT",
                    "discord_message_id": "TEXT",
                }
                for column, declaration in outbox_additions.items():
                    if column not in outbox_columns:
                        connection.execute(
                            f"ALTER TABLE outbox_messages ADD COLUMN {column} {declaration}"
                        )
                rows_without_nonce = connection.execute(
                    """SELECT id, idempotency_key FROM outbox_messages
                       WHERE discord_nonce IS NULL OR discord_nonce = ''"""
                ).fetchall()
                for row in rows_without_nonce:
                    connection.execute(
                        "UPDATE outbox_messages SET discord_nonce = ? WHERE id = ?",
                        (_discord_nonce(str(row["idempotency_key"])), row["id"]),
                    )
                inbox_columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(inbox_messages)").fetchall()
                }
                inbox_additions = {
                    "game_id": "TEXT",
                    "ingress_game_id": "TEXT",
                    "routing_lifecycle": "TEXT",
                    "scene_id": "TEXT",
                    "scene_participants_json": "TEXT NOT NULL DEFAULT '[]'",
                    "guild_id": "TEXT",
                    "parent_channel_id": "TEXT",
                    "reply_to_event_id": "TEXT",
                    "reply_to_author_id": "TEXT",
                    "reply_context": "TEXT",
                    "attachments_json": "TEXT NOT NULL DEFAULT '[]'",
                    "transport_attempts": "INTEGER NOT NULL DEFAULT 0",
                    "provider_attempts": "INTEGER NOT NULL DEFAULT 0",
                    "next_attempt_at": "TEXT",
                    "handler_result_json": "TEXT",
                }
                for column, declaration in inbox_additions.items():
                    if column not in inbox_columns:
                        connection.execute(
                            f"ALTER TABLE inbox_messages ADD COLUMN {column} {declaration}"
                        )
                decision_columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(decision_checkpoints)"
                    ).fetchall()
                }
                if "input_fingerprint" not in decision_columns:
                    connection.execute(
                        "ALTER TABLE decision_checkpoints ADD COLUMN input_fingerprint TEXT"
                    )
                binding_columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(channel_bindings)").fetchall()
                }
                if "inherited_from_channel_id" not in binding_columns:
                    connection.execute(
                        "ALTER TABLE channel_bindings ADD COLUMN inherited_from_channel_id TEXT"
                    )
                monitored_columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(monitored_channels)"
                    ).fetchall()
                }
                if "inherited_from_channel_id" not in monitored_columns:
                    connection.execute(
                        "ALTER TABLE monitored_channels ADD COLUMN inherited_from_channel_id TEXT"
                    )
                pending_columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(pending_interactions)"
                    ).fetchall()
                }
                if "origin_channel_id" not in pending_columns:
                    connection.execute(
                        "ALTER TABLE pending_interactions ADD COLUMN origin_channel_id TEXT"
                    )
                connection.execute(
                    """CREATE INDEX IF NOT EXISTS inbox_game_time
                       ON inbox_messages(game_id, created_at, event_id)"""
                )
                connection.execute(
                    """CREATE INDEX IF NOT EXISTS domain_events_game_type_id
                       ON domain_events(game_id, event_type, id)"""
                )
                # Preserve known v5 ingress snapshots for audit. For older databases, only queued
                # work may safely inherit the current binding: processed history can span several
                # games in one channel and must remain unscoped rather than leak across games.
                connection.execute(
                    """UPDATE inbox_messages SET ingress_game_id = game_id
                       WHERE ingress_game_id IS NULL AND game_id IS NOT NULL"""
                )
                connection.execute(
                    """UPDATE inbox_messages
                       SET game_id = (
                           SELECT bindings.game_id FROM channel_bindings AS bindings
                           WHERE bindings.channel_id = inbox_messages.channel_id
                       ),
                           ingress_game_id = (
                           SELECT bindings.game_id FROM channel_bindings AS bindings
                           WHERE bindings.channel_id = inbox_messages.channel_id
                       )
                       WHERE game_id IS NULL
                         AND status IN ('pending', 'processing')
                         AND EXISTS (
                           SELECT 1 FROM channel_bindings AS bindings
                           WHERE bindings.channel_id = inbox_messages.channel_id
                       )"""
                )
                if current < 5:
                    # Empty ingress is an internal marker for pre-snapshot queued rows that had no
                    # binding at migration time. A later bind must not reinterpret them as part of
                    # the newly selected game.
                    connection.execute(
                        """UPDATE inbox_messages SET ingress_game_id = ''
                           WHERE game_id IS NULL AND ingress_game_id IS NULL
                             AND status IN ('pending', 'processing')"""
                    )
                if current < 7:
                    # A lifecycle read after a handler-side commit can route a crash replay down
                    # a different scenario. Never guess that historical snapshot for work which
                    # was already attempted. Operators must inspect the committed aggregate before
                    # explicitly requeueing it; untouched rows will stamp lifecycle on first claim.
                    connection.execute(
                        """UPDATE inbox_messages
                           SET status = 'failed',
                               error = 'schema v7: lifecycle snapshot missing after prior attempt; '
                                     || 'inspect canonical game state before explicit requeue'
                           WHERE game_id IS NOT NULL
                             AND status IN ('pending', 'processing')
                             AND (attempts > 0 OR status = 'processing')
                             AND routing_lifecycle IS NULL"""
                    )
                self._migrate_legacy_world_workspaces(connection)
                connection.execute(
                    """INSERT INTO game_rules(game_id, reserve_recovery_mode)
                       SELECT games.game_id, 'both'
                       FROM games
                       LEFT JOIN game_rules ON game_rules.game_id = games.game_id
                       WHERE game_rules.game_id IS NULL"""
                )
                if current < SCHEMA_VERSION:
                    connection.execute(
                        "INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,)
                    )

    def schema_version(self) -> int:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT MAX(version) AS version FROM schema_version"
            ).fetchone()
        if row is None or row["version"] is None:
            raise RuntimeError("database schema is not initialized")
        return int(row["version"])

    def backup(self, target: str | Path) -> Path:
        target_path = Path(target)
        if target_path.resolve() == self.path.resolve():
            raise ValueError("backup target must differ from the live database")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as source:
            with closing(sqlite3.connect(target_path)) as destination:
                source.backup(destination)
        return target_path

    def record_llm_call(
        self,
        *,
        game_id: str | None,
        role: str,
        model: str,
        prompt_fingerprint: str,
        response_id: str | None,
        prompt_tokens: int,
        completion_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
        reasoning_tokens: int,
        latency_ms: int,
        cost: float,
        success: bool,
        error: str | None = None,
        trace_id: str | None = None,
        channel_id: str | None = None,
        event_id: str | None = None,
    ) -> None:
        values = (
            prompt_tokens,
            completion_tokens,
            cache_read_tokens,
            cache_write_tokens,
            reasoning_tokens,
            latency_ms,
        )
        if any(value < 0 for value in values) or cost < 0:
            raise ValueError("LLM metrics cannot be negative")
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO llm_calls
                   (game_id, trace_id, channel_id, event_id, role, model,
                    prompt_fingerprint, response_id, prompt_tokens,
                    completion_tokens, cache_read_tokens, cache_write_tokens,
                    reasoning_tokens, latency_ms, cost, success, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    game_id,
                    trace_id,
                    channel_id,
                    event_id,
                    role,
                    model,
                    prompt_fingerprint,
                    response_id,
                    *values,
                    cost,
                    int(success),
                    error[:2000] if error else None,
                ),
            )

    def record_stage_span(self, **values: object) -> None:
        self.record_stage_spans([values])

    def record_stage_spans(self, records: list[dict[str, object]]) -> None:
        if not records:
            return
        if any(float(record["duration_ms"]) < 0 for record in records):
            raise ValueError("stage duration cannot be negative")
        with self.transaction() as connection:
            connection.executemany(
                """INSERT INTO stage_spans
                   (span_id, trace_id, parent_span_id, game_id, channel_id, event_id,
                    stage, component, operation, status, duration_ms, attributes_json,
                    error, started_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        values["span_id"],
                        values["trace_id"],
                        values.get("parent_span_id"),
                        values.get("game_id"),
                        values.get("channel_id"),
                        values.get("event_id"),
                        values["stage"],
                        values["component"],
                        values["operation"],
                        values["status"],
                        float(values["duration_ms"]),
                        json.dumps(values.get("attributes") or {}, ensure_ascii=False),
                        values.get("error"),
                        values["started_at"],
                    )
                    for values in records
                ],
            )

    def record_lexicon_candidate(
        self,
        *,
        event_id: str,
        scenario: str,
        command: str,
        normalized_phrase: str,
        confidence: float,
    ) -> bool:
        """Persist one replay-safe candidate observation for later manual promotion."""

        event_id = event_id.strip()
        scenario = scenario.strip()
        command = command.strip()
        normalized_phrase = normalized_phrase.strip()
        if not event_id or not scenario or not command or not normalized_phrase:
            raise ValueError("lexicon candidate fields cannot be empty")
        if len(event_id) > 256 or len(scenario) > 64 or len(command) > 64:
            raise ValueError("lexicon candidate identity is too long")
        if len(normalized_phrase) > 512:
            raise ValueError("lexicon candidate phrase is too long")
        if isinstance(confidence, bool) or not 0 <= float(confidence) <= 1:
            raise ValueError("lexicon candidate confidence must be between zero and one")
        normalized_confidence = float(confidence)
        with self.transaction() as connection:
            prior = connection.execute(
                """SELECT scenario, command, normalized_phrase, confidence
                   FROM lexicon_candidates WHERE event_id = ?""",
                (event_id,),
            ).fetchone()
            if prior is not None:
                identity = (
                    str(prior["scenario"]),
                    str(prior["command"]),
                    str(prior["normalized_phrase"]),
                    float(prior["confidence"]),
                )
                expected = (
                    scenario,
                    command,
                    normalized_phrase,
                    normalized_confidence,
                )
                if identity != expected:
                    raise RuntimeError("lexicon candidate replay identity mismatch")
                return False
            connection.execute(
                """INSERT INTO lexicon_candidates
                   (event_id, scenario, command, normalized_phrase, confidence)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    event_id,
                    scenario,
                    command,
                    normalized_phrase,
                    normalized_confidence,
                ),
            )
        return True

    def llm_session_totals(self, game_id: str) -> dict[str, int | float]:
        with closing(self.connect()) as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS calls,
                          COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                          COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                          COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
                          COALESCE(SUM(latency_ms), 0) AS latency_ms,
                          COALESCE(SUM(cost), 0) AS cost
                   FROM llm_calls WHERE game_id = ?""",
                (game_id,),
            ).fetchone()
        return dict(row)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with stage_span("db.transaction", component="sqlite", operation="write_transaction"):
            connection = self.connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    @staticmethod
    def _scene_snapshot(
        connection: sqlite3.Connection,
        *,
        game_id: str | None,
        player_id: str,
    ) -> tuple[str | None, str]:
        if game_id is None:
            return None, "[]"
        location = connection.execute(
            """SELECT scene_id FROM player_locations
               WHERE game_id = ? AND player_id = ?""",
            (game_id, player_id),
        ).fetchone()
        if location is None:
            return None, "[]"
        scene_id = str(location["scene_id"])
        participants = [
            str(row["player_id"])
            for row in connection.execute(
                """SELECT player_id FROM player_locations
                   WHERE game_id = ? AND scene_id = ? ORDER BY player_id""",
                (game_id, scene_id),
            ).fetchall()
        ]
        return scene_id, json.dumps(participants, ensure_ascii=False)

    def enqueue(self, message: IncomingMessage) -> bool:
        attachments = [
            {
                "attachment_id": attachment.attachment_id,
                "filename": attachment.filename,
                "content_type": attachment.content_type,
                "size": attachment.size,
            }
            for attachment in message.attachments
        ]
        with self.transaction() as connection:
            binding = connection.execute(
                "SELECT game_id FROM channel_bindings WHERE channel_id = ?",
                (message.channel_id,),
            ).fetchone()
            game_id = None if binding is None else str(binding["game_id"])
            scene_id, scene_participants_json = self._scene_snapshot(
                connection,
                game_id=game_id,
                player_id=message.author_id,
            )
            cursor = connection.execute(
                """INSERT OR IGNORE INTO inbox_messages
                   (event_id, channel_id, game_id, ingress_game_id, author_id, content,
                    created_at, scene_id, scene_participants_json, guild_id, parent_channel_id,
                    reply_to_event_id, reply_to_author_id, reply_context, attachments_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    message.event_id,
                    message.channel_id,
                    game_id,
                    game_id,
                    message.author_id,
                    message.content,
                    message.created_at.isoformat(),
                    scene_id,
                    scene_participants_json,
                    message.guild_id,
                    message.parent_channel_id,
                    message.reply_to_event_id,
                    message.reply_to_author_id,
                    message.reply_context,
                    json.dumps(attachments, ensure_ascii=False),
                ),
            )
            return cursor.rowcount == 1

    @staticmethod
    def _validate_handler_result_payload(payload: Mapping[str, object]) -> dict[str, object]:
        expected_keys = {
            "text",
            "deliveries",
            "completion_game_id",
            "render_live_status",
        }
        if set(payload) != expected_keys:
            raise ValueError("handler result payload has an unsupported shape")
        text = payload["text"]
        deliveries = payload["deliveries"]
        completion_game_id = payload["completion_game_id"]
        render_live_status = payload["render_live_status"]
        if not isinstance(text, str):
            raise ValueError("handler result text must be a string")
        if not isinstance(deliveries, list):
            raise ValueError("handler result deliveries must be a list")
        normalized_deliveries: list[dict[str, str]] = []
        for delivery in deliveries:
            if not isinstance(delivery, Mapping) or set(delivery) != {
                "channel_id",
                "content",
                "kind",
            }:
                raise ValueError("handler result delivery has an unsupported shape")
            if not all(
                isinstance(delivery[field], str) for field in ("channel_id", "content", "kind")
            ):
                raise ValueError("handler result delivery fields must be strings")
            normalized_deliveries.append(
                {
                    "channel_id": str(delivery["channel_id"]),
                    "content": str(delivery["content"]),
                    "kind": str(delivery["kind"]),
                }
            )
        if completion_game_id is not None and (
            not isinstance(completion_game_id, str) or not completion_game_id
        ):
            raise ValueError("handler result completion game id must be null or non-empty")
        if not isinstance(render_live_status, bool):
            raise ValueError("handler result live-status marker must be boolean")
        return {
            "text": text,
            "deliveries": normalized_deliveries,
            "completion_game_id": completion_game_id,
            "render_live_status": render_live_status,
        }

    def handler_result(self, event_id: str) -> dict[str, object] | None:
        """Return the exact successful handler result saved before inbox completion."""

        if not event_id:
            raise ValueError("handler result event id cannot be empty")
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT handler_result_json FROM inbox_messages WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        if row is None:
            raise ValueError("inbox event not found")
        if row["handler_result_json"] is None:
            return None
        try:
            payload = json.loads(row["handler_result_json"])
            if not isinstance(payload, dict):
                raise ValueError("handler result payload must be an object")
            return self._validate_handler_result_payload(payload)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("stored handler result payload is invalid") from error

    def inbox_provider_attempts(self, event_id: str) -> int:
        if not event_id:
            raise ValueError("inbox event id cannot be empty")
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT provider_attempts FROM inbox_messages WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        if row is None:
            raise ValueError("inbox event not found")
        return int(row["provider_attempts"])

    def provider_retry_exhausted(self, event_id: str) -> bool:
        """Return true while handling the final allowed provider attempt."""

        return self.inbox_provider_attempts(event_id) >= (INBOX_PROVIDER_MAX_ATTEMPTS - 1)

    def checkpoint_handler_result(
        self,
        *,
        event_id: str,
        result: Mapping[str, object],
    ) -> dict[str, object]:
        """First-writer journal between a successful handler and durable completion.

        A retry must use the returned canonical payload.  It may not replace a result
        already produced by an earlier attempt, even if a nondeterministic pipeline would
        now answer differently.
        """

        if not event_id:
            raise ValueError("handler result event id cannot be empty")
        normalized = self._validate_handler_result_payload(result)
        encoded = json.dumps(normalized, ensure_ascii=False, allow_nan=False)
        with self.transaction() as connection:
            row = connection.execute(
                """SELECT status, handler_result_json FROM inbox_messages
                   WHERE event_id = ?""",
                (event_id,),
            ).fetchone()
            if row is None:
                raise ValueError("inbox event not found")
            if row["handler_result_json"] is not None:
                try:
                    prior = json.loads(row["handler_result_json"])
                    if not isinstance(prior, dict):
                        raise ValueError("handler result payload must be an object")
                    return self._validate_handler_result_payload(prior)
                except (TypeError, ValueError, json.JSONDecodeError) as error:
                    raise RuntimeError("stored handler result payload is invalid") from error
            if row["status"] != InboxStatus.PROCESSING.value:
                raise RuntimeError("handler result can only checkpoint a claimed inbox event")
            cursor = connection.execute(
                """UPDATE inbox_messages SET handler_result_json = ?
                   WHERE event_id = ? AND status = 'processing'
                     AND handler_result_json IS NULL""",
                (encoded, event_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("handler result checkpoint lost inbox ownership")
        return normalized

    @staticmethod
    def _validate_decision_checkpoint_identity(
        *,
        event_id: str,
        pipeline_key: str,
        output_type: str,
        input_fingerprint: str | None,
    ) -> None:
        if not event_id:
            raise ValueError("decision checkpoint event id cannot be empty")
        if not pipeline_key or not pipeline_key.strip():
            raise ValueError("decision checkpoint pipeline key cannot be empty")
        if not output_type or not output_type.strip():
            raise ValueError("decision checkpoint output type cannot be empty")
        if input_fingerprint is not None and not input_fingerprint.strip():
            raise ValueError("decision checkpoint input fingerprint cannot be empty")

    @staticmethod
    def _decision_checkpoint_payload(payload: Mapping[str, object]) -> dict[str, object]:
        try:
            encoded = json.dumps(dict(payload), ensure_ascii=False, allow_nan=False)
            decoded = json.loads(encoded)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("decision checkpoint payload must be JSON-compatible") from error
        if not isinstance(decoded, dict):
            raise ValueError("decision checkpoint payload must be an object")
        return decoded

    @staticmethod
    def _read_decision_checkpoint(
        row: sqlite3.Row,
        *,
        output_type: str,
        game_id: str | None,
        input_fingerprint: str | None,
    ) -> dict[str, object]:
        if row["output_type"] != output_type:
            raise RuntimeError("decision checkpoint output type mismatch")
        if row["game_id"] != game_id:
            raise RuntimeError("decision checkpoint game scope mismatch")
        if row["input_fingerprint"] != input_fingerprint:
            raise RuntimeError("decision checkpoint input fingerprint mismatch")
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("stored decision checkpoint payload is invalid") from error
        if not isinstance(payload, dict):
            raise RuntimeError("stored decision checkpoint payload is invalid")
        return payload

    def decision_checkpoint(
        self,
        *,
        event_id: str,
        pipeline_key: str,
        output_type: str,
        game_id: str | None = None,
        input_fingerprint: str | None = None,
    ) -> dict[str, object] | None:
        """Load one typed pipeline output journaled before its first mutation."""

        self._validate_decision_checkpoint_identity(
            event_id=event_id,
            pipeline_key=pipeline_key,
            output_type=output_type,
            input_fingerprint=input_fingerprint,
        )
        with closing(self.connect()) as connection:
            row = connection.execute(
                """SELECT output_type, game_id, input_fingerprint, payload_json
                   FROM decision_checkpoints
                   WHERE event_id = ? AND pipeline_key = ?""",
                (event_id, pipeline_key),
            ).fetchone()
        if row is None:
            return None
        return self._read_decision_checkpoint(
            row,
            output_type=output_type,
            game_id=game_id,
            input_fingerprint=input_fingerprint,
        )

    def checkpoint_decision(
        self,
        *,
        event_id: str,
        pipeline_key: str,
        output_type: str,
        payload: Mapping[str, object],
        game_id: str | None = None,
        input_fingerprint: str | None = None,
    ) -> dict[str, object]:
        """Persist the first valid typed output for an event/pipeline pair."""

        self._validate_decision_checkpoint_identity(
            event_id=event_id,
            pipeline_key=pipeline_key,
            output_type=output_type,
            input_fingerprint=input_fingerprint,
        )
        normalized = self._decision_checkpoint_payload(payload)
        encoded = json.dumps(normalized, ensure_ascii=False, allow_nan=False)
        with self.transaction() as connection:
            row = connection.execute(
                """SELECT output_type, game_id, input_fingerprint, payload_json
                   FROM decision_checkpoints
                   WHERE event_id = ? AND pipeline_key = ?""",
                (event_id, pipeline_key),
            ).fetchone()
            if row is not None:
                return self._read_decision_checkpoint(
                    row,
                    output_type=output_type,
                    game_id=game_id,
                    input_fingerprint=input_fingerprint,
                )
            connection.execute(
                """INSERT INTO decision_checkpoints
                   (event_id, pipeline_key, output_type, game_id,
                    input_fingerprint, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    pipeline_key,
                    output_type,
                    game_id,
                    input_fingerprint,
                    encoded,
                ),
            )
        return normalized

    @staticmethod
    def _json_object(value: Mapping[str, object], *, label: str) -> dict[str, object]:
        try:
            encoded = json.dumps(
                dict(value),
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
            decoded = json.loads(encoded)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(f"{label} must be a JSON-compatible object") from error
        if not isinstance(decoded, dict):
            raise ValueError(f"{label} must be an object")
        return decoded

    @staticmethod
    def _event_operation_fingerprint(input_payload: Mapping[str, object]) -> tuple[str, str]:
        normalized = SQLiteStore._json_object(
            input_payload,
            label="event operation input",
        )
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _read_event_operation_row(row: sqlite3.Row) -> dict[str, object]:
        try:
            input_payload = json.loads(row["input_json"])
            result = json.loads(row["result_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("stored event operation payload is invalid") from error
        if not isinstance(input_payload, dict) or not isinstance(result, dict):
            raise RuntimeError("stored event operation payload is invalid")
        encoded, fingerprint = SQLiteStore._event_operation_fingerprint(input_payload)
        if encoded != row["input_json"] or fingerprint != row["input_fingerprint"]:
            raise RuntimeError("stored event operation fingerprint is invalid")
        return {
            "event_id": str(row["event_id"]),
            "operation_type": str(row["operation_type"]),
            "game_id": None if row["game_id"] is None else str(row["game_id"]),
            "channel_id": None if row["channel_id"] is None else str(row["channel_id"]),
            "input": input_payload,
            "result": result,
            "created_at": str(row["created_at"]),
        }

    def event_operation(self, event_id: str) -> dict[str, object] | None:
        """Load a mutation marker before consulting live routing or aggregate state."""

        if not event_id:
            raise ValueError("event operation id cannot be empty")
        with closing(self.connect()) as connection:
            row = connection.execute(
                """SELECT event_id, operation_type, game_id, channel_id,
                          input_fingerprint, input_json, result_json, created_at
                   FROM event_operations WHERE event_id = ?""",
                (event_id,),
            ).fetchone()
        return None if row is None else self._read_event_operation_row(row)

    @classmethod
    def _matching_event_operation(
        cls,
        connection: sqlite3.Connection,
        *,
        event_id: str,
        operation_type: str,
        game_id: str | None,
        channel_id: str | None,
        input_payload: Mapping[str, object],
    ) -> dict[str, object] | None:
        encoded, fingerprint = cls._event_operation_fingerprint(input_payload)
        row = connection.execute(
            """SELECT event_id, operation_type, game_id, channel_id,
                      input_fingerprint, input_json, result_json, created_at
               FROM event_operations WHERE event_id = ?""",
            (event_id,),
        ).fetchone()
        if row is None:
            return None
        envelope = cls._read_event_operation_row(row)
        if (
            envelope["operation_type"] != operation_type
            or envelope["game_id"] != game_id
            or envelope["channel_id"] != channel_id
            or row["input_json"] != encoded
            or row["input_fingerprint"] != fingerprint
        ):
            raise RuntimeError("event operation identity mismatch")
        result = envelope["result"]
        assert isinstance(result, dict)
        return result

    @classmethod
    def _insert_event_operation(
        cls,
        connection: sqlite3.Connection,
        *,
        event_id: str,
        operation_type: str,
        game_id: str | None,
        channel_id: str | None,
        input_payload: Mapping[str, object],
        result: Mapping[str, object],
    ) -> dict[str, object]:
        if not event_id or not operation_type.strip():
            raise ValueError("event id and operation type are required")
        inbox = connection.execute(
            "SELECT status FROM inbox_messages WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if inbox is None:
            raise ValueError("event operation requires an inbox event")
        if inbox["status"] != InboxStatus.PROCESSING.value:
            raise RuntimeError("event operation requires a claimed inbox event")
        input_json, fingerprint = cls._event_operation_fingerprint(input_payload)
        normalized_result = cls._json_object(result, label="event operation result")
        result_json = json.dumps(
            normalized_result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        connection.execute(
            """INSERT INTO event_operations
               (event_id, operation_type, game_id, channel_id,
                input_fingerprint, input_json, result_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                operation_type,
                game_id,
                channel_id,
                fingerprint,
                input_json,
                result_json,
            ),
        )
        return normalized_result

    @staticmethod
    def _game_operation_result(
        connection: sqlite3.Connection,
        *,
        game_id: str,
    ) -> dict[str, object]:
        row = connection.execute(
            """SELECT games.game_id, games.world_id, games.lifecycle,
                      games.progression_enabled, games.narrator_rights_level,
                      games.locale, games.narrative_channel_id, games.revision,
                      COALESCE(game_rules.reserve_recovery_mode, 'both')
                          AS reserve_recovery_mode
               FROM games
               LEFT JOIN game_rules ON game_rules.game_id = games.game_id
               WHERE games.game_id = ?""",
            (game_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"game does not exist: {game_id}")
        return {
            "game_id": str(row["game_id"]),
            "world_id": str(row["world_id"]),
            "lifecycle": str(row["lifecycle"]),
            "progression_enabled": bool(row["progression_enabled"]),
            "narrator_rights_level": str(row["narrator_rights_level"]),
            "locale": str(row["locale"]),
            "narrative_channel_id": (
                None if row["narrative_channel_id"] is None else str(row["narrative_channel_id"])
            ),
            "reserve_recovery_mode": str(row["reserve_recovery_mode"]),
            "revision": int(row["revision"]),
        }

    @staticmethod
    def _help_operation_result(
        pending: PendingInteraction,
        *,
        helper_player_id: str,
        target_player_id: str,
        locale: str,
    ) -> dict[str, object]:
        return {
            "interaction_id": pending.interaction_id,
            "game_id": pending.game_id,
            "player_id": pending.player_id,
            "scene_id": pending.scene_id,
            "kind": pending.kind.value,
            "prompt": pending.prompt,
            "payload": dict(pending.payload),
            "status": pending.status.value,
            "revision": pending.revision,
            "created_at": (None if pending.created_at is None else pending.created_at.isoformat()),
            "origin_channel_id": pending.origin_channel_id,
            "helper_player_id": helper_player_id,
            "target_player_id": target_player_id,
            "locale": locale,
        }

    @staticmethod
    def _pending_from_help_operation_result(
        result: Mapping[str, object],
    ) -> PendingInteraction:
        try:
            payload = result["payload"]
            if not isinstance(payload, dict):
                raise TypeError("pending payload is not an object")
            created_at_value = result["created_at"]
            created_at = (
                None if created_at_value is None else datetime.fromisoformat(str(created_at_value))
            )
            pending = PendingInteraction(
                interaction_id=str(result["interaction_id"]),
                game_id=str(result["game_id"]),
                player_id=str(result["player_id"]),
                scene_id=None if result["scene_id"] is None else str(result["scene_id"]),
                kind=PendingKind(str(result["kind"])),
                prompt=str(result["prompt"]),
                payload=dict(payload),
                status=PendingStatus(str(result["status"])),
                revision=int(result["revision"]),
                created_at=created_at,
                origin_channel_id=(
                    None
                    if result["origin_channel_id"] is None
                    else str(result["origin_channel_id"])
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("stored offer-help operation result is invalid") from error
        if (
            pending.kind is not PendingKind.POOL_CONFIRMATION
            or pending.status is not PendingStatus.OPEN
            or pending.player_id != result.get("target_player_id")
        ):
            raise RuntimeError("stored offer-help operation result is invalid")
        return pending

    def create_game(self, game: GameState) -> None:
        with self.transaction() as connection:
            world = connection.execute(
                "SELECT 1 FROM worlds WHERE world_id = ?", (game.world_id,)
            ).fetchone()
            if world is None:
                raise ValueError(f"world does not exist: {game.world_id}")
            connection.execute(
                """INSERT INTO games
                   (game_id, world_id, lifecycle, progression_enabled,
                    narrator_rights_level, locale, narrative_channel_id, revision)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    game.game_id,
                    game.world_id,
                    game.lifecycle.value,
                    int(game.progression_enabled),
                    game.narrator_rights_level.value,
                    game.locale,
                    game.narrative_channel_id,
                    game.revision,
                ),
            )
            connection.execute("INSERT INTO session_activity(game_id) VALUES (?)", (game.game_id,))
            connection.execute(
                """INSERT INTO game_rules
                   (game_id, reserve_recovery_mode)
                   VALUES (?, ?)""",
                (
                    game.game_id,
                    game.reserve_recovery_mode.value,
                ),
            )

    def prepare_game_and_bind_if_channel_available(
        self,
        game: GameState,
        *,
        channel_id: str,
    ) -> None:
        """Atomically create/recover a selected game and claim an unbound channel."""
        with self.transaction() as connection:
            workspace = connection.execute(
                """SELECT world_id FROM world_projects
                   WHERE active_channel_id = ?""",
                (channel_id,),
            ).fetchone()
            if workspace is not None:
                raise RuntimeError("channel has an active world workspace")
            binding = connection.execute(
                "SELECT game_id FROM channel_bindings WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()
            if binding is not None and binding["game_id"] != game.game_id:
                raise RuntimeError("channel binding changed during world selection")
            existing = connection.execute(
                "SELECT world_id, lifecycle FROM games WHERE game_id = ?",
                (game.game_id,),
            ).fetchone()
            if existing is None:
                world = connection.execute(
                    "SELECT 1 FROM worlds WHERE world_id = ?",
                    (game.world_id,),
                ).fetchone()
                if world is None:
                    raise ValueError(f"world does not exist: {game.world_id}")
                connection.execute(
                    """INSERT INTO games
                       (game_id, world_id, lifecycle, progression_enabled,
                        narrator_rights_level, locale, narrative_channel_id, revision)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        game.game_id,
                        game.world_id,
                        game.lifecycle.value,
                        int(game.progression_enabled),
                        game.narrator_rights_level.value,
                        game.locale,
                        game.narrative_channel_id,
                        game.revision,
                    ),
                )
                connection.execute(
                    "INSERT INTO session_activity(game_id) VALUES (?)",
                    (game.game_id,),
                )
                connection.execute(
                    """INSERT INTO game_rules(game_id, reserve_recovery_mode)
                       VALUES (?, ?)""",
                    (game.game_id, game.reserve_recovery_mode.value),
                )
                lifecycle = game.lifecycle.value
            else:
                if existing["world_id"] != game.world_id:
                    raise ValueError("existing game id belongs to a different world")
                lifecycle = str(existing["lifecycle"])
            if binding is None:
                connection.execute(
                    """INSERT INTO channel_bindings(channel_id, game_id, lifecycle)
                       VALUES (?, ?, ?)""",
                    (channel_id, game.game_id, lifecycle),
                )
            connection.execute(
                """UPDATE monitored_channels SET inherited_from_channel_id = NULL
                   WHERE channel_id = ? AND inherited_from_channel_id IS NOT NULL""",
                (channel_id,),
            )
            self._rebind_inherited_channels(
                connection,
                parent_channel_id=channel_id,
                game_id=game.game_id,
                lifecycle=lifecycle,
            )

    def create_world(self, world: WorldState) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO worlds(world_id, title, status, revision)
                   VALUES (?, ?, ?, ?)""",
                (world.world_id, world.title, world.status, world.revision),
            )

    def save_world_workspace(
        self,
        *,
        channel_id: str,
        world_id: str,
        stage: str,
        brief: str,
        settings: dict[str, object],
        sources: dict[str, str],
        world: WorldState | None = None,
    ) -> None:
        with self.transaction() as connection:
            binding = connection.execute(
                "SELECT game_id FROM channel_bindings WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()
            if binding is not None:
                raise RuntimeError("channel binding changed during world creation")
            if world is not None:
                if world.world_id != world_id:
                    raise ValueError("workspace world does not match the supplied world")
                existing_world = connection.execute(
                    "SELECT title, status FROM worlds WHERE world_id = ?",
                    (world_id,),
                ).fetchone()
                if existing_world is None:
                    connection.execute(
                        """INSERT INTO worlds(world_id, title, status, revision)
                           VALUES (?, ?, ?, ?)""",
                        (world.world_id, world.title, world.status, world.revision),
                    )
                elif (
                    existing_world["title"] != world.title
                    or existing_world["status"] != world.status
                ):
                    raise RuntimeError("world id already belongs to another draft")
            project = connection.execute(
                """SELECT active_channel_id FROM world_projects
                   WHERE world_id = ?""",
                (world_id,),
            ).fetchone()
            if (
                project is not None
                and project["active_channel_id"] is not None
                and project["active_channel_id"] != channel_id
            ):
                raise RuntimeError("world project is active in another channel")
            connection.execute(
                """INSERT INTO world_projects
                   (world_id, active_channel_id, stage, brief, settings_json, sources_json)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(world_id) DO UPDATE SET
                     active_channel_id = excluded.active_channel_id,
                     stage = excluded.stage,
                     brief = excluded.brief,
                     settings_json = excluded.settings_json,
                     sources_json = excluded.sources_json,
                     revision = world_projects.revision + 1""",
                (
                    world_id,
                    channel_id,
                    stage,
                    brief,
                    json.dumps(settings, ensure_ascii=False),
                    json.dumps(sources, ensure_ascii=False),
                ),
            )
            connection.execute(
                """UPDATE monitored_channels SET inherited_from_channel_id = NULL
                   WHERE channel_id = ? AND inherited_from_channel_id IS NOT NULL""",
                (channel_id,),
            )

    def world_workspace(self, channel_id: str) -> dict[str, object] | None:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """SELECT active_channel_id AS channel_id, world_id, stage, brief,
                          settings_json, sources_json, revision
                   FROM world_projects WHERE active_channel_id = ?
                   ORDER BY world_id LIMIT 2""",
                (channel_id,),
            ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise RuntimeError("channel has multiple active world projects")
        row = rows[0]
        return {
            "channel_id": row["channel_id"],
            "world_id": row["world_id"],
            "stage": row["stage"],
            "brief": row["brief"],
            "settings": json.loads(row["settings_json"]),
            "sources": json.loads(row["sources_json"]),
            "revision": row["revision"],
        }

    def clear_world_workspace(self, channel_id: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "DELETE FROM world_projects WHERE active_channel_id = ?", (channel_id,)
            )

    def pause_world_workspace(self, *, channel_id: str, world_id: str) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE world_projects
                   SET active_channel_id = NULL, revision = revision + 1
                   WHERE active_channel_id = ? AND world_id = ?""",
                (channel_id, world_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("world workspace changed before pause")

    def pause_world_workspace_for_event(
        self,
        *,
        event_id: str,
        channel_id: str,
        world_id: str,
        expected_workspace_revision: int,
    ) -> bool:
        """Pause a draft atomically with a checkpoint visible before live-stage routing."""

        if not event_id:
            raise ValueError("world pause event id cannot be empty")
        causation_id = f"world-pause:{event_id}"
        with self.transaction() as connection:
            committed = self._world_operation_event(
                connection,
                event_type="world_workspace_paused",
                causation_id=causation_id,
            )
            if committed is not None:
                if (
                    committed.get("world_id") != world_id
                    or committed.get("channel_id") != channel_id
                ):
                    raise RuntimeError("world pause event belongs to another workspace")
                return False
            cursor = connection.execute(
                """UPDATE world_projects
                   SET active_channel_id = NULL, revision = revision + 1
                   WHERE active_channel_id = ? AND world_id = ? AND revision = ?""",
                (channel_id, world_id, expected_workspace_revision),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("world workspace changed before pause")
            connection.execute(
                """INSERT INTO domain_events
                   (game_id, event_type, payload_json, causation_id)
                   VALUES (NULL, 'world_workspace_paused', ?, ?)""",
                (
                    json.dumps(
                        {
                            "operation": "pause",
                            "channel_id": channel_id,
                            "world_id": world_id,
                            "workspace_revision": expected_workspace_revision + 1,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    causation_id,
                ),
            )
        return True

    def resume_world_workspace(self, *, channel_id: str, world_id: str) -> dict[str, object]:
        with self.transaction() as connection:
            binding = connection.execute(
                "SELECT game_id FROM channel_bindings WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()
            if binding is not None:
                raise RuntimeError("channel is bound to a game")
            active = connection.execute(
                "SELECT world_id FROM world_projects WHERE active_channel_id = ?",
                (channel_id,),
            ).fetchone()
            if active is not None:
                raise RuntimeError("channel already has an active world project")
            cursor = connection.execute(
                """UPDATE world_projects
                   SET active_channel_id = ?, revision = revision + 1
                   WHERE world_id = ? AND active_channel_id IS NULL""",
                (channel_id, world_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("world project is not available for resume")
            connection.execute(
                """UPDATE monitored_channels SET inherited_from_channel_id = NULL
                   WHERE channel_id = ? AND inherited_from_channel_id IS NOT NULL""",
                (channel_id,),
            )
        workspace = self.world_workspace(channel_id)
        assert workspace is not None
        return workspace

    def resume_world_workspace_for_event(
        self,
        *,
        event_id: str,
        channel_id: str,
        world_id: str,
        expected_workspace_revision: int,
    ) -> bool:
        """Resume a retained draft atomically with an inbox replay checkpoint."""

        if not event_id:
            raise ValueError("world resume event id cannot be empty")
        causation_id = f"world-resume:{event_id}"
        with self.transaction() as connection:
            committed = self._world_operation_event(
                connection,
                event_type="world_workspace_resumed",
                causation_id=causation_id,
            )
            if committed is not None:
                if (
                    committed.get("world_id") != world_id
                    or committed.get("channel_id") != channel_id
                ):
                    raise RuntimeError("world resume event belongs to another workspace")
                return False
            binding = connection.execute(
                "SELECT game_id FROM channel_bindings WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()
            if binding is not None:
                raise RuntimeError("channel is bound to a game")
            active = connection.execute(
                "SELECT world_id FROM world_projects WHERE active_channel_id = ?",
                (channel_id,),
            ).fetchone()
            if active is not None:
                raise RuntimeError("channel already has an active world project")
            cursor = connection.execute(
                """UPDATE world_projects
                   SET active_channel_id = ?, revision = revision + 1
                   WHERE world_id = ? AND active_channel_id IS NULL AND revision = ?""",
                (channel_id, world_id, expected_workspace_revision),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("world project is not available for resume")
            connection.execute(
                """UPDATE monitored_channels SET inherited_from_channel_id = NULL
                   WHERE channel_id = ? AND inherited_from_channel_id IS NOT NULL""",
                (channel_id,),
            )
            connection.execute(
                """INSERT INTO domain_events
                   (game_id, event_type, payload_json, causation_id)
                   VALUES (NULL, 'world_workspace_resumed', ?, ?)""",
                (
                    json.dumps(
                        {
                            "operation": "resume",
                            "channel_id": channel_id,
                            "world_id": world_id,
                            "workspace_revision": expected_workspace_revision + 1,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    causation_id,
                ),
            )
        return True

    def world_project(self, world_id: str) -> dict[str, object] | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                """SELECT active_channel_id AS channel_id, world_id, stage, brief,
                          settings_json, sources_json, revision
                   FROM world_projects WHERE world_id = ?""",
                (world_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "channel_id": row["channel_id"],
            "world_id": row["world_id"],
            "stage": row["stage"],
            "brief": row["brief"],
            "settings": json.loads(row["settings_json"]),
            "sources": json.loads(row["sources_json"]),
            "revision": row["revision"],
        }

    def set_world_status(self, *, world_id: str, status: str, expected_revision: int) -> WorldState:
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE worlds
                   SET status = ?,
                       revision = revision + CASE WHEN status = ? THEN 0 ELSE 1 END
                   WHERE world_id = ? AND revision = ?""",
                (status, status, world_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("world revision conflict")
        updated = self.world_state(world_id)
        assert updated is not None
        return updated

    def set_world_title(self, *, world_id: str, title: str) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE worlds SET title = ? WHERE world_id = ?",
                (title.strip(), world_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("world does not exist")

    def world_state(self, world_id: str) -> WorldState | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT world_id, title, status, revision FROM worlds WHERE world_id = ?",
                (world_id,),
            ).fetchone()
        if row is None:
            return None
        return WorldState(row["world_id"], row["title"], row["status"], row["revision"])

    def list_worlds(self) -> list[WorldState]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """SELECT world_id, title, status, revision FROM worlds
                   ORDER BY CASE status WHEN 'approved' THEN 0 ELSE 1 END, title, world_id"""
            ).fetchall()
        return [
            WorldState(row["world_id"], row["title"], row["status"], row["revision"])
            for row in rows
        ]

    def update_world_content(
        self,
        *,
        world_id: str,
        expected_revision: int,
        content: dict[str, object],
        status: str = "draft",
    ) -> WorldState:
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE worlds
                   SET content_json = ?, status = ?, revision = revision + 1
                   WHERE world_id = ? AND revision = ?""",
                (json.dumps(content, ensure_ascii=False), status, world_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("world revision conflict")
            connection.execute(
                """INSERT INTO domain_events
                   (game_id, event_type, payload_json, causation_id)
                   VALUES (NULL, 'world_generated', ?, ?)""",
                (
                    json.dumps({"world_id": world_id, "revision": expected_revision + 1}),
                    f"world-generation:{world_id}:{expected_revision}",
                ),
            )
        updated = self.world_state(world_id)
        assert updated is not None
        return updated

    @staticmethod
    def _world_operation_event(
        connection: sqlite3.Connection,
        *,
        event_type: str,
        causation_id: str,
    ) -> dict[str, object] | None:
        row = connection.execute(
            """SELECT payload_json FROM domain_events
               WHERE event_type = ? AND causation_id = ?""",
            (event_type, causation_id),
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict):
            raise RuntimeError("world operation event payload is invalid")
        return payload

    def world_generation_for_event(self, event_id: str) -> dict[str, object] | None:
        """Return the committed generation checkpoint for an inbox event, if any."""

        with closing(self.connect()) as connection:
            return self._world_operation_event(
                connection,
                event_type="world_generation_committed",
                causation_id=f"world-generation:{event_id}",
            )

    def world_operation_for_event(self, event_id: str) -> dict[str, object] | None:
        """Find any committed world-workspace transition before routing on live stage."""

        operations = {
            "world_generation_committed": ("generation", f"world-generation:{event_id}"),
            "world_revision_applied": ("revision", f"world-revision:{event_id}"),
            "world_approval_committed": ("approval", f"world-approval:{event_id}"),
            "world_workspace_paused": ("pause", f"world-pause:{event_id}"),
            "world_workspace_resumed": ("resume", f"world-resume:{event_id}"),
        }
        matches: list[dict[str, object]] = []
        with closing(self.connect()) as connection:
            for event_type, (operation, causation_id) in operations.items():
                payload = self._world_operation_event(
                    connection,
                    event_type=event_type,
                    causation_id=causation_id,
                )
                if payload is not None:
                    result = dict(payload)
                    result.setdefault("operation", operation)
                    matches.append(result)
        if not matches:
            return None
        if len(matches) != 1:
            raise RuntimeError("one inbox event committed multiple world operations")
        return matches[0]

    def commit_world_generation(
        self,
        *,
        event_id: str,
        channel_id: str,
        world_id: str,
        expected_world_revision: int,
        expected_workspace_revision: int,
        content: dict[str, object],
        brief: str,
        settings: dict[str, object],
        sources: dict[str, str],
    ) -> bool:
        """Atomically persist generated content, the review workspace, and its replay marker.

        ``False`` is an exact-event replay.  The handler can query the same checkpoint before
        invoking the generator, which prevents a worker crash after commit from paying for a
        second model call.
        """

        if not event_id:
            raise ValueError("world generation event id cannot be empty")
        causation_id = f"world-generation:{event_id}"
        content_json = json.dumps(content, ensure_ascii=False, sort_keys=True)
        settings_json = json.dumps(settings, ensure_ascii=False, sort_keys=True)
        sources_json = json.dumps(sources, ensure_ascii=False, sort_keys=True)
        with self.transaction() as connection:
            committed = self._world_operation_event(
                connection,
                event_type="world_generation_committed",
                causation_id=causation_id,
            )
            if committed is not None:
                if (
                    committed.get("world_id") != world_id
                    or committed.get("channel_id") != channel_id
                ):
                    raise RuntimeError("world generation event belongs to another workspace")
                return False
            if (
                connection.execute(
                    "SELECT 1 FROM channel_bindings WHERE channel_id = ?",
                    (channel_id,),
                ).fetchone()
                is not None
            ):
                raise RuntimeError("channel binding changed during world generation")
            world = connection.execute(
                "SELECT revision FROM worlds WHERE world_id = ?",
                (world_id,),
            ).fetchone()
            project = connection.execute(
                """SELECT revision FROM world_projects
                   WHERE world_id = ? AND active_channel_id = ?""",
                (world_id, channel_id),
            ).fetchone()
            if world is None or int(world["revision"]) != expected_world_revision:
                raise RuntimeError("world revision conflict")
            if project is None or int(project["revision"]) != expected_workspace_revision:
                raise RuntimeError("world workspace revision conflict")
            world_update = connection.execute(
                """UPDATE worlds
                   SET content_json = ?, status = 'draft', revision = revision + 1
                   WHERE world_id = ? AND revision = ?""",
                (content_json, world_id, expected_world_revision),
            )
            project_update = connection.execute(
                """UPDATE world_projects
                   SET stage = 'review', brief = ?, settings_json = ?, sources_json = ?,
                       revision = revision + 1
                   WHERE world_id = ? AND active_channel_id = ? AND revision = ?""",
                (
                    brief,
                    settings_json,
                    sources_json,
                    world_id,
                    channel_id,
                    expected_workspace_revision,
                ),
            )
            if world_update.rowcount != 1 or project_update.rowcount != 1:
                raise RuntimeError("world generation lost workspace ownership")
            event_payload = {
                "operation": "generation",
                "channel_id": channel_id,
                "world_id": world_id,
                "world_revision": expected_world_revision + 1,
                "workspace_revision": expected_workspace_revision + 1,
            }
            connection.execute(
                """INSERT INTO domain_events
                   (game_id, event_type, payload_json, causation_id)
                   VALUES (NULL, 'world_generation_committed', ?, ?)""",
                (
                    json.dumps(event_payload, ensure_ascii=False, sort_keys=True),
                    causation_id,
                ),
            )
        return True

    def world_revision_for_event(self, event_id: str) -> dict[str, object] | None:
        """Return the committed input-revision checkpoint for an inbox event, if any."""

        with closing(self.connect()) as connection:
            return self._world_operation_event(
                connection,
                event_type="world_revision_applied",
                causation_id=f"world-revision:{event_id}",
            )

    def apply_world_revision(
        self,
        *,
        event_id: str,
        channel_id: str,
        world_id: str,
        expected_world_revision: int,
        expected_workspace_revision: int,
        title: str,
        brief: str,
        settings: dict[str, object],
        sources: dict[str, str],
    ) -> bool:
        """Atomically apply one intake revision and checkpoint its inbox causation."""

        title = title.strip()
        if not event_id:
            raise ValueError("world revision event id cannot be empty")
        if not title:
            raise ValueError("world title cannot be empty")
        causation_id = f"world-revision:{event_id}"
        settings_json = json.dumps(settings, ensure_ascii=False, sort_keys=True)
        sources_json = json.dumps(sources, ensure_ascii=False, sort_keys=True)
        with self.transaction() as connection:
            committed = self._world_operation_event(
                connection,
                event_type="world_revision_applied",
                causation_id=causation_id,
            )
            if committed is not None:
                if (
                    committed.get("world_id") != world_id
                    or committed.get("channel_id") != channel_id
                ):
                    raise RuntimeError("world revision event belongs to another workspace")
                return False
            if (
                connection.execute(
                    "SELECT 1 FROM channel_bindings WHERE channel_id = ?",
                    (channel_id,),
                ).fetchone()
                is not None
            ):
                raise RuntimeError("channel binding changed during world revision")
            world = connection.execute(
                "SELECT title, revision FROM worlds WHERE world_id = ?",
                (world_id,),
            ).fetchone()
            project = connection.execute(
                """SELECT stage, brief, settings_json, sources_json, revision
                   FROM world_projects
                   WHERE world_id = ? AND active_channel_id = ?""",
                (world_id, channel_id),
            ).fetchone()
            if world is None or int(world["revision"]) != expected_world_revision:
                raise RuntimeError("world revision conflict")
            if project is None or int(project["revision"]) != expected_workspace_revision:
                raise RuntimeError("world workspace revision conflict")
            world_changed = str(world["title"]) != title
            project_changed = (
                str(project["stage"]) != "collecting"
                or str(project["brief"]) != brief
                or json.loads(project["settings_json"]) != settings
                or json.loads(project["sources_json"]) != sources
            )
            world_update = connection.execute(
                """UPDATE worlds
                   SET title = ?, revision = revision + ?
                   WHERE world_id = ? AND revision = ?""",
                (title, int(world_changed), world_id, expected_world_revision),
            )
            project_update = connection.execute(
                """UPDATE world_projects
                   SET stage = 'collecting', brief = ?, settings_json = ?,
                       sources_json = ?, revision = revision + ?
                   WHERE world_id = ? AND active_channel_id = ? AND revision = ?""",
                (
                    brief,
                    settings_json,
                    sources_json,
                    int(project_changed),
                    world_id,
                    channel_id,
                    expected_workspace_revision,
                ),
            )
            if world_update.rowcount != 1 or project_update.rowcount != 1:
                raise RuntimeError("world revision lost workspace ownership")
            event_payload = {
                "operation": "revision",
                "channel_id": channel_id,
                "world_id": world_id,
                "world_revision": expected_world_revision + int(world_changed),
                "workspace_revision": expected_workspace_revision + int(project_changed),
            }
            connection.execute(
                """INSERT INTO domain_events
                   (game_id, event_type, payload_json, causation_id)
                   VALUES (NULL, 'world_revision_applied', ?, ?)""",
                (
                    json.dumps(event_payload, ensure_ascii=False, sort_keys=True),
                    causation_id,
                ),
            )
        return True

    def approve_world_and_clear_workspace(
        self,
        *,
        event_id: str,
        channel_id: str,
        world_id: str,
        expected_world_revision: int,
        expected_workspace_revision: int,
    ) -> WorldState:
        """Approve a generated world and remove its active project in one transaction."""

        if not event_id:
            raise ValueError("world approval event id cannot be empty")
        causation_id = f"world-approval:{event_id}"
        with self.transaction() as connection:
            committed = self._world_operation_event(
                connection,
                event_type="world_approval_committed",
                causation_id=causation_id,
            )
            if committed is not None:
                if (
                    committed.get("world_id") != world_id
                    or committed.get("channel_id") != channel_id
                ):
                    raise RuntimeError("world approval event belongs to another workspace")
                current = connection.execute(
                    """SELECT title, status, revision FROM worlds
                       WHERE world_id = ?""",
                    (world_id,),
                ).fetchone()
                if current is None or str(current["status"]) != "approved":
                    raise RuntimeError("committed world approval no longer exists")
                return WorldState(
                    world_id=world_id,
                    title=str(current["title"]),
                    status="approved",
                    revision=int(current["revision"]),
                )
            world = connection.execute(
                """SELECT title, status, content_json, revision
                   FROM worlds WHERE world_id = ?""",
                (world_id,),
            ).fetchone()
            project = connection.execute(
                """SELECT stage, revision FROM world_projects
                   WHERE world_id = ? AND active_channel_id = ?""",
                (world_id, channel_id),
            ).fetchone()
            if world is None or int(world["revision"]) != expected_world_revision:
                raise RuntimeError("world revision conflict")
            if (
                project is None
                or int(project["revision"]) != expected_workspace_revision
                or str(project["stage"]) != "review"
            ):
                raise RuntimeError("world workspace revision conflict")
            content = json.loads(world["content_json"])
            if not isinstance(content, dict) or not content.get("locations"):
                raise ValueError("world must be generated before approval")
            status_changed = str(world["status"]) != "approved"
            world_update = connection.execute(
                """UPDATE worlds
                   SET status = 'approved', revision = revision + ?
                   WHERE world_id = ? AND revision = ?""",
                (int(status_changed), world_id, expected_world_revision),
            )
            project_delete = connection.execute(
                """DELETE FROM world_projects
                   WHERE world_id = ? AND active_channel_id = ? AND revision = ?""",
                (world_id, channel_id, expected_workspace_revision),
            )
            if world_update.rowcount != 1 or project_delete.rowcount != 1:
                raise RuntimeError("world approval lost workspace ownership")
            approved = WorldState(
                world_id=world_id,
                title=str(world["title"]),
                status="approved",
                revision=expected_world_revision + int(status_changed),
            )
            connection.execute(
                """INSERT INTO domain_events
                   (game_id, event_type, payload_json, causation_id)
                   VALUES (NULL, 'world_approval_committed', ?, ?)""",
                (
                    json.dumps(
                        {
                            "operation": "approval",
                            "channel_id": channel_id,
                            "world_id": world_id,
                            "world_revision": approved.revision,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    causation_id,
                ),
            )
            return approved

    def world_content(self, world_id: str) -> dict[str, object] | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT content_json FROM worlds WHERE world_id = ?",
                (world_id,),
            ).fetchone()
        return None if row is None else json.loads(row["content_json"])

    def set_narrative_channel(
        self, *, game_id: str, channel_id: str, expected_revision: int
    ) -> None:
        with self.transaction() as connection:
            target = connection.execute(
                "SELECT guild_id, kind FROM discord_channels WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()
            source_channels = connection.execute(
                """SELECT registry.guild_id
                   FROM channel_bindings AS binding
                   JOIN discord_channels AS registry
                     ON registry.channel_id = binding.channel_id
                   WHERE binding.game_id = ?""",
                (game_id,),
            ).fetchall()
            if source_channels:
                if target is None:
                    raise ValueError("narrative channel is unknown to this Discord connection")
                source_guilds = {row["guild_id"] for row in source_channels}
                if None in source_guilds or target["guild_id"] is None:
                    raise ValueError("narrative delivery is not supported in direct messages")
                if len(source_guilds) != 1 or target["guild_id"] not in source_guilds:
                    raise ValueError("narrative channel must be in the same Discord server")
            if (
                target is not None
                and str(target["kind"]).casefold() not in _MESSAGEABLE_DISCORD_CHANNEL_KINDS
            ):
                raise ValueError("narrative channel is not messageable")
            target_binding = connection.execute(
                "SELECT game_id FROM channel_bindings WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()
            if target_binding is not None and target_binding["game_id"] != game_id:
                raise ValueError("narrative channel is bound to another game")
            narrative_owner = connection.execute(
                """SELECT game_id FROM games
                   WHERE narrative_channel_id = ? AND game_id != ? LIMIT 1""",
                (channel_id, game_id),
            ).fetchone()
            if narrative_owner is not None:
                raise ValueError("narrative channel is already assigned to another game")
            cursor = connection.execute(
                """UPDATE games
                   SET narrative_channel_id = ?,
                       revision = revision
                           + CASE WHEN narrative_channel_id = ? THEN 0 ELSE 1 END
                   WHERE game_id = ? AND revision = ?""",
                (channel_id, channel_id, game_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("game revision conflict")

    def set_progression_enabled(
        self, *, game_id: str, enabled: bool, expected_revision: int
    ) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE games
                   SET progression_enabled = ?,
                       revision = revision
                           + CASE WHEN progression_enabled = ? THEN 0 ELSE 1 END
                   WHERE game_id = ? AND revision = ? AND lifecycle = 'preparing'""",
                (int(enabled), int(enabled), game_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    "progression setting can change only during preparation at current revision"
                )

    def set_narrator_rights_level(
        self,
        *,
        game_id: str,
        level: NarratorRightsLevel,
        expected_revision: int,
    ) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE games
                   SET narrator_rights_level = ?,
                       revision = revision
                           + CASE WHEN narrator_rights_level = ? THEN 0 ELSE 1 END
                   WHERE game_id = ? AND revision = ?""",
                (level.value, level.value, game_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("game revision conflict")

    def set_reserve_recovery_mode(
        self,
        *,
        game_id: str,
        mode: ReserveRecoveryMode,
        expected_revision: int,
    ) -> None:
        with self.transaction() as connection:
            current_rule = connection.execute(
                """SELECT reserve_recovery_mode FROM game_rules
                   WHERE game_id = ?""",
                (game_id,),
            ).fetchone()
            changed = (
                current_rule is None or str(current_rule["reserve_recovery_mode"]) != mode.value
            )
            cursor = connection.execute(
                """UPDATE games
                   SET revision = revision + ?
                   WHERE game_id = ? AND revision = ? AND lifecycle = 'preparing'""",
                (int(changed), game_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    "reserve recovery can change only during preparation at current revision"
                )
            connection.execute(
                """INSERT INTO game_rules(game_id, reserve_recovery_mode)
                   VALUES (?, ?)
                   ON CONFLICT(game_id) DO UPDATE SET
                     reserve_recovery_mode = excluded.reserve_recovery_mode""",
                (game_id, mode.value),
            )

    def configure_game_for_event(
        self,
        *,
        event_id: str,
        game_id: str,
        channel_id: str,
        setting: str,
        value: object,
        expected_revision: int,
    ) -> dict[str, object]:
        """Apply one deterministic setting and its replay marker atomically."""

        setting = setting.strip()
        if setting == "progression":
            if not isinstance(value, bool):
                raise ValueError("progression value must be boolean")
            normalized_value: object = value
        elif setting == "narrator_rights":
            normalized_value = value.value if isinstance(value, NarratorRightsLevel) else str(value)
            NarratorRightsLevel(str(normalized_value))
        elif setting == "reserve_recovery":
            normalized_value = value.value if isinstance(value, ReserveRecoveryMode) else str(value)
            ReserveRecoveryMode(str(normalized_value))
        elif setting == "narrative_channel":
            normalized_value = str(value).strip()
            if not normalized_value:
                raise ValueError("narrative channel id cannot be empty")
        else:
            raise ValueError(f"unsupported game setting: {setting}")
        operation_type = f"configure_game:{setting}"
        operation_input = {"setting": setting, "value": normalized_value}
        with self.transaction() as connection:
            prior = self._matching_event_operation(
                connection,
                event_id=event_id,
                operation_type=operation_type,
                game_id=game_id,
                channel_id=channel_id,
                input_payload=operation_input,
            )
            if prior is not None:
                return prior
            if setting == "progression":
                cursor = connection.execute(
                    """UPDATE games
                       SET progression_enabled = ?,
                           revision = revision
                               + CASE WHEN progression_enabled = ? THEN 0 ELSE 1 END
                       WHERE game_id = ? AND revision = ?
                         AND lifecycle = 'preparing'""",
                    (
                        int(bool(normalized_value)),
                        int(bool(normalized_value)),
                        game_id,
                        expected_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(
                        "progression setting can change only during preparation at current revision"
                    )
            elif setting == "narrator_rights":
                cursor = connection.execute(
                    """UPDATE games
                       SET narrator_rights_level = ?,
                           revision = revision
                               + CASE WHEN narrator_rights_level = ? THEN 0 ELSE 1 END
                       WHERE game_id = ? AND revision = ?""",
                    (
                        normalized_value,
                        normalized_value,
                        game_id,
                        expected_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("game revision conflict")
            elif setting == "reserve_recovery":
                current_rule = connection.execute(
                    """SELECT reserve_recovery_mode FROM game_rules
                       WHERE game_id = ?""",
                    (game_id,),
                ).fetchone()
                changed = (
                    current_rule is None
                    or str(current_rule["reserve_recovery_mode"]) != normalized_value
                )
                cursor = connection.execute(
                    """UPDATE games
                       SET revision = revision + ?
                       WHERE game_id = ? AND revision = ?
                         AND lifecycle = 'preparing'""",
                    (int(changed), game_id, expected_revision),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(
                        "reserve recovery can change only during preparation at current revision"
                    )
                connection.execute(
                    """INSERT INTO game_rules(game_id, reserve_recovery_mode)
                       VALUES (?, ?)
                       ON CONFLICT(game_id) DO UPDATE SET
                           reserve_recovery_mode = excluded.reserve_recovery_mode""",
                    (game_id, normalized_value),
                )
            else:
                target_channel = str(normalized_value)
                target = connection.execute(
                    "SELECT guild_id, kind FROM discord_channels WHERE channel_id = ?",
                    (target_channel,),
                ).fetchone()
                source_channels = connection.execute(
                    """SELECT registry.guild_id
                       FROM channel_bindings AS binding
                       JOIN discord_channels AS registry
                         ON registry.channel_id = binding.channel_id
                       WHERE binding.game_id = ?""",
                    (game_id,),
                ).fetchall()
                if source_channels:
                    if target is None:
                        raise ValueError("narrative channel is unknown to this Discord connection")
                    source_guilds = {row["guild_id"] for row in source_channels}
                    if None in source_guilds or target["guild_id"] is None:
                        raise ValueError("narrative delivery is not supported in direct messages")
                    if len(source_guilds) != 1 or target["guild_id"] not in source_guilds:
                        raise ValueError("narrative channel must be in the same Discord server")
                if (
                    target is not None
                    and str(target["kind"]).casefold() not in _MESSAGEABLE_DISCORD_CHANNEL_KINDS
                ):
                    raise ValueError("narrative channel is not messageable")
                target_binding = connection.execute(
                    "SELECT game_id FROM channel_bindings WHERE channel_id = ?",
                    (target_channel,),
                ).fetchone()
                if target_binding is not None and target_binding["game_id"] != game_id:
                    raise ValueError("narrative channel is bound to another game")
                narrative_owner = connection.execute(
                    """SELECT game_id FROM games
                       WHERE narrative_channel_id = ? AND game_id != ? LIMIT 1""",
                    (target_channel, game_id),
                ).fetchone()
                if narrative_owner is not None:
                    raise ValueError("narrative channel is already assigned to another game")
                cursor = connection.execute(
                    """UPDATE games
                       SET narrative_channel_id = ?,
                           revision = revision
                               + CASE WHEN narrative_channel_id = ? THEN 0 ELSE 1 END
                       WHERE game_id = ? AND revision = ?""",
                    (
                        target_channel,
                        target_channel,
                        game_id,
                        expected_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("game revision conflict")
            result = self._game_operation_result(connection, game_id=game_id)
            return self._insert_event_operation(
                connection,
                event_id=event_id,
                operation_type=operation_type,
                game_id=game_id,
                channel_id=channel_id,
                input_payload=operation_input,
                result=result,
            )

    def scene_count(self, game_id: str) -> int:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM scenes WHERE game_id = ?", (game_id,)
            ).fetchone()
        return int(row["count"])

    def scene_catalog(self, game_id: str) -> list[dict[str, object]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """SELECT scene_id, title, state_json, revision
                   FROM scenes WHERE game_id = ? ORDER BY rowid""",
                (game_id,),
            ).fetchall()
        return [
            {
                "scene_id": row["scene_id"],
                "title": row["title"],
                "description": str(json.loads(row["state_json"]).get("description") or ""),
                "revision": row["revision"],
            }
            for row in rows
        ]

    def create_scene(
        self,
        *,
        scene_id: str,
        game_id: str,
        title: str,
        state: dict[str, object] | None = None,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO scenes(scene_id, game_id, title, state_json)
                   VALUES (?, ?, ?, ?)""",
                (scene_id, game_id, title, json.dumps(state or {}, ensure_ascii=False)),
            )

    def create_scene_if_absent(
        self,
        *,
        scene_id: str,
        game_id: str,
        title: str,
        state: dict[str, object] | None = None,
    ) -> bool:
        """Create a deterministic scene once, or verify an exact crash replay."""
        expected_state = state or {}
        with self.transaction() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO scenes(scene_id, game_id, title, state_json)
                   VALUES (?, ?, ?, ?)""",
                (
                    scene_id,
                    game_id,
                    title,
                    json.dumps(expected_state, ensure_ascii=False),
                ),
            )
            stored = connection.execute(
                """SELECT game_id, title, state_json FROM scenes
                   WHERE scene_id = ?""",
                (scene_id,),
            ).fetchone()
            if stored is None:
                raise RuntimeError("scene create-or-verify lost its row")
            if (
                stored["game_id"] != game_id
                or stored["title"] != title
                or json.loads(stored["state_json"]) != expected_state
            ):
                raise RuntimeError("scene id already belongs to a different scene")
            return cursor.rowcount == 1

    def first_scene_id(self, game_id: str) -> str | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                """SELECT scene_id FROM scenes WHERE game_id = ?
                   ORDER BY rowid LIMIT 1""",
                (game_id,),
            ).fetchone()
        return None if row is None else str(row["scene_id"])

    def place_player(
        self,
        *,
        game_id: str,
        player_id: str,
        scene_id: str,
        expected_revision: int | None = None,
    ) -> None:
        with self.transaction() as connection:
            scene = connection.execute(
                "SELECT game_id FROM scenes WHERE scene_id = ?", (scene_id,)
            ).fetchone()
            if scene is None or scene["game_id"] != game_id:
                raise ValueError("scene does not belong to game")
            current = connection.execute(
                """SELECT revision FROM player_locations
                   WHERE game_id = ? AND player_id = ?""",
                (game_id, player_id),
            ).fetchone()
            if current is None:
                if expected_revision not in {None, 0}:
                    raise RuntimeError("player location revision conflict")
                connection.execute(
                    """INSERT INTO player_locations(game_id, player_id, scene_id)
                       VALUES (?, ?, ?)""",
                    (game_id, player_id, scene_id),
                )
                return
            if expected_revision is not None and current["revision"] != expected_revision:
                raise RuntimeError("player location revision conflict")
            connection.execute(
                """UPDATE player_locations
                   SET scene_id = ?, revision = revision + 1
                   WHERE game_id = ? AND player_id = ?""",
                (scene_id, game_id, player_id),
            )

    def scene_projection(self, *, game_id: str, player_id: str) -> dict[str, object] | None:
        with closing(self.connect()) as connection:
            location = connection.execute(
                """SELECT scene_id, revision FROM player_locations
                   WHERE game_id = ? AND player_id = ?""",
                (game_id, player_id),
            ).fetchone()
            if location is None:
                return None
            scene = connection.execute(
                """SELECT title, state_json, revision FROM scenes
                   WHERE scene_id = ? AND game_id = ?""",
                (location["scene_id"], game_id),
            ).fetchone()
            participants = connection.execute(
                """SELECT player_id FROM player_locations
                   WHERE game_id = ? AND scene_id = ? ORDER BY player_id""",
                (game_id, location["scene_id"]),
            ).fetchall()
        if scene is None:
            raise RuntimeError("player location references a missing scene")
        return {
            "scene_id": location["scene_id"],
            "title": scene["title"],
            "state": json.loads(scene["state_json"]),
            "scene_revision": scene["revision"],
            "location_revision": location["revision"],
            "participants": [row["player_id"] for row in participants],
        }

    def scene_by_id(self, *, game_id: str, scene_id: str) -> dict[str, object] | None:
        with closing(self.connect()) as connection:
            scene = connection.execute(
                """SELECT title, state_json, revision FROM scenes
                   WHERE scene_id = ? AND game_id = ?""",
                (scene_id, game_id),
            ).fetchone()
            participants = connection.execute(
                """SELECT player_id FROM player_locations
                   WHERE game_id = ? AND scene_id = ? ORDER BY player_id""",
                (game_id, scene_id),
            ).fetchall()
        if scene is None:
            return None
        return {
            "scene_id": scene_id,
            "title": scene["title"],
            "state": json.loads(scene["state_json"]),
            "scene_revision": scene["revision"],
            "participants": [row["player_id"] for row in participants],
        }

    def latest_active_scene_id(self, game_id: str) -> str | None:
        for event in reversed(self.recent_domain_events(game_id=game_id, limit=30)):
            scene_id = event["payload"].get("scene_id")
            if isinstance(scene_id, str):
                return scene_id
        return self.first_scene_id(game_id)

    def has_scene_patch(self, causation_id: str) -> bool:
        with closing(self.connect()) as connection:
            return (
                connection.execute(
                    """SELECT 1 FROM domain_events
                   WHERE causation_id = ? AND event_type = 'scene_patched'""",
                    (causation_id,),
                ).fetchone()
                is not None
            )

    def apply_scene_patch(
        self,
        *,
        game_id: str,
        scene_id: str,
        expected_revision: int,
        causation_id: str,
        summary: str,
        add_facts: list[str],
        remove_facts: list[str],
    ) -> None:
        with self.transaction() as connection:
            if (
                connection.execute(
                    """SELECT 1 FROM domain_events
                   WHERE causation_id = ? AND event_type = 'scene_patched'""",
                    (causation_id,),
                ).fetchone()
                is not None
            ):
                return
            row = connection.execute(
                """SELECT state_json, revision FROM scenes
                   WHERE scene_id = ? AND game_id = ?""",
                (scene_id, game_id),
            ).fetchone()
            if row is None or row["revision"] != expected_revision:
                raise RuntimeError("scene revision conflict")
            state = json.loads(row["state_json"])
            facts = list(state.get("facts") or [])
            missing = set(remove_facts) - set(facts)
            if missing:
                raise ValueError(f"cannot remove unknown scene facts: {sorted(missing)}")
            facts = [fact for fact in facts if fact not in set(remove_facts)]
            for fact in add_facts:
                if fact not in facts:
                    facts.append(fact)
            state["facts"] = facts
            cursor = connection.execute(
                """UPDATE scenes SET state_json = ?, revision = revision + 1
                   WHERE scene_id = ? AND game_id = ? AND revision = ?""",
                (json.dumps(state, ensure_ascii=False), scene_id, game_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("scene revision conflict")
            connection.execute(
                """INSERT INTO domain_events
                   (game_id, event_type, payload_json, causation_id)
                   VALUES (?, 'scene_patched', ?, ?)""",
                (
                    game_id,
                    json.dumps(
                        {
                            "scene_id": scene_id,
                            "summary": summary,
                            "add_facts": add_facts,
                            "remove_facts": remove_facts,
                            "scene_revision": expected_revision + 1,
                        },
                        ensure_ascii=False,
                    ),
                    causation_id,
                ),
            )

    def apply_outcome_patch(
        self,
        *,
        game_id: str,
        scene_id: str,
        expected_scene_revision: int,
        actor_character_id: str,
        expected_actor_revision: int,
        expected_actor_location_revision: int,
        expected_scene_participants: tuple[str, ...],
        causation_id: str,
        patch: CanonicalOutcomePatch,
        secret_reveals: Mapping[str, str] | None = None,
        effect_metadata: Mapping[str, object] | None = None,
    ) -> None:
        """Apply one validated actor/current-scene outcome atomically and idempotently."""
        if expected_actor_location_revision < 0:
            raise ValueError("expected actor location revision cannot be negative")
        normalized_participants = tuple(str(item).strip() for item in expected_scene_participants)
        if (
            not normalized_participants
            or any(not participant for participant in normalized_participants)
            or normalized_participants != tuple(sorted(set(normalized_participants)))
        ):
            raise ValueError("expected scene participants must be sorted unique nonempty ids")
        with self.transaction() as connection:
            if (
                connection.execute(
                    """SELECT 1 FROM domain_events
                       WHERE causation_id = ? AND event_type = 'scene_patched'""",
                    (causation_id,),
                ).fetchone()
                is not None
            ):
                return
            scene_row = connection.execute(
                """SELECT state_json, revision FROM scenes
                   WHERE scene_id = ? AND game_id = ?""",
                (scene_id, game_id),
            ).fetchone()
            if scene_row is None or scene_row["revision"] != expected_scene_revision:
                raise RuntimeError("scene revision conflict")
            actor_row = connection.execute(
                """SELECT player_id, sheet_json, conditions_json, plot_items_json, revision
                   FROM characters WHERE character_id = ? AND game_id = ?""",
                (actor_character_id, game_id),
            ).fetchone()
            if actor_row is None or actor_row["revision"] != expected_actor_revision:
                raise RuntimeError("actor character revision conflict")

            scene_state = json.loads(scene_row["state_json"])
            facts = list(scene_state.get("facts") or [])
            missing_facts = set(patch.remove_facts) - set(facts)
            if missing_facts:
                raise ValueError(f"cannot remove unknown scene facts: {sorted(missing_facts)}")
            facts = [fact for fact in facts if fact not in set(patch.remove_facts)]
            for fact in patch.add_facts:
                if fact not in facts:
                    facts.append(fact)
            scene_state["facts"] = facts

            raw_npcs = list(scene_state.get("npcs") or [])
            if any(not isinstance(item, dict) for item in raw_npcs):
                raise ValueError("scene NPC state must be an object list")
            npcs_by_id = {
                str(item.get("id") or item.get("npc_id")): dict(item)
                for item in raw_npcs
                if item.get("id") or item.get("npc_id")
            }
            missing_npcs = set(patch.remove_scene_npc_ids) - set(npcs_by_id)
            if missing_npcs:
                raise ValueError(f"cannot remove unknown scene NPCs: {sorted(missing_npcs)}")
            for npc_id in patch.remove_scene_npc_ids:
                npcs_by_id.pop(npc_id)
            for npc in patch.upsert_scene_npcs:
                npcs_by_id[npc.npc_id] = {
                    "id": npc.npc_id,
                    "name": npc.name,
                    "state": npc.state,
                }
            scene_state["npcs"] = list(npcs_by_id.values())

            threads = list(scene_state.get("threads") or [])
            missing_threads = set(patch.close_threads) - set(threads)
            if missing_threads:
                raise ValueError(f"cannot close unknown scene threads: {sorted(missing_threads)}")
            threads = [thread for thread in threads if thread not in set(patch.close_threads)]
            for thread in patch.open_threads:
                if thread not in threads:
                    threads.append(thread)
            scene_state["threads"] = threads

            conditions = list(json.loads(actor_row["conditions_json"]))
            condition_names = {str(item["text"]) for item in conditions}
            missing_conditions = set(patch.remove_actor_conditions) - condition_names
            if missing_conditions:
                raise ValueError(
                    f"cannot remove unknown actor conditions: {sorted(missing_conditions)}"
                )
            duplicate_conditions = {
                condition.text
                for condition in patch.add_actor_conditions
                if condition.text in condition_names
            }
            if duplicate_conditions:
                raise ValueError(
                    f"cannot add existing actor conditions: {sorted(duplicate_conditions)}"
                )
            conditions = [
                item
                for item in conditions
                if str(item["text"]) not in set(patch.remove_actor_conditions)
            ]
            conditions.extend(
                {"text": condition.text, "source": condition.source}
                for condition in patch.add_actor_conditions
            )

            plot_items = list(json.loads(actor_row["plot_items_json"]))
            plot_item_names = {str(item["name"]) for item in plot_items}
            missing_items = set(patch.remove_actor_plot_items) - plot_item_names
            if missing_items:
                raise ValueError(f"cannot remove unknown actor plot items: {sorted(missing_items)}")
            duplicate_items = {
                item.name for item in patch.add_actor_plot_items if item.name in plot_item_names
            }
            if duplicate_items:
                raise ValueError(f"cannot add existing actor plot items: {sorted(duplicate_items)}")
            plot_items = [
                item
                for item in plot_items
                if str(item["name"]) not in set(patch.remove_actor_plot_items)
            ]
            plot_items.extend(
                {"name": item.name, "description": item.description}
                for item in patch.add_actor_plot_items
            )

            sheet = json.loads(actor_row["sheet_json"])
            bonuses = list(sheet.get("temporary_bonuses", []))
            if patch.grant_temporary_bonus is not None:
                bonus_ids = {str(item["bonus_id"]) for item in bonuses}
                if patch.grant_temporary_bonus.bonus_id in bonus_ids:
                    raise ValueError("temporary bonus id already exists")
                bonuses.append(
                    {
                        "bonus_id": patch.grant_temporary_bonus.bonus_id,
                        "type": patch.grant_temporary_bonus.type.value,
                        "trigger": patch.grant_temporary_bonus.trigger,
                    }
                )
            sheet["temporary_bonuses"] = bonuses

            target_scene_id = patch.move_actor_to_scene_id
            location_row = connection.execute(
                """SELECT scene_id, revision FROM player_locations
                   WHERE game_id = ? AND player_id = ?""",
                (game_id, actor_row["player_id"]),
            ).fetchone()
            if location_row is None or location_row["scene_id"] != scene_id:
                raise RuntimeError("actor is no longer in the patched scene")
            if int(location_row["revision"]) != expected_actor_location_revision:
                raise RuntimeError("actor location revision conflict")
            if str(actor_row["player_id"]) not in normalized_participants:
                raise ValueError("expected scene participants must include the actor")
            current_participants = tuple(
                str(row["player_id"])
                for row in connection.execute(
                    """SELECT player_id FROM player_locations
                       WHERE game_id = ? AND scene_id = ? ORDER BY player_id""",
                    (game_id, scene_id),
                ).fetchall()
            )
            if current_participants != normalized_participants:
                raise RuntimeError("scene participants changed")
            if target_scene_id is not None:
                target = connection.execute(
                    "SELECT 1 FROM scenes WHERE game_id = ? AND scene_id = ?",
                    (game_id, target_scene_id),
                ).fetchone()
                if target is None:
                    raise ValueError(f"target scene does not exist: {target_scene_id}")

            resulting_scene_revision = expected_scene_revision
            if patch.changes_scene_state:
                scene_update = connection.execute(
                    """UPDATE scenes SET state_json = ?, revision = revision + 1
                       WHERE scene_id = ? AND game_id = ? AND revision = ?""",
                    (
                        json.dumps(scene_state, ensure_ascii=False),
                        scene_id,
                        game_id,
                        expected_scene_revision,
                    ),
                )
                if scene_update.rowcount != 1:
                    raise RuntimeError("scene revision conflict")
                resulting_scene_revision += 1

            resulting_actor_revision = expected_actor_revision
            if patch.changes_actor:
                actor_update = connection.execute(
                    """UPDATE characters
                       SET sheet_json = ?, conditions_json = ?, plot_items_json = ?,
                           revision = revision + 1
                       WHERE character_id = ? AND game_id = ? AND revision = ?""",
                    (
                        json.dumps(sheet, ensure_ascii=False),
                        json.dumps(conditions, ensure_ascii=False),
                        json.dumps(plot_items, ensure_ascii=False),
                        actor_character_id,
                        game_id,
                        expected_actor_revision,
                    ),
                )
                if actor_update.rowcount != 1:
                    raise RuntimeError("actor character revision conflict")
                resulting_actor_revision += 1

            if target_scene_id is not None and target_scene_id != scene_id:
                location_update = connection.execute(
                    """UPDATE player_locations
                       SET scene_id = ?, revision = revision + 1
                       WHERE game_id = ? AND player_id = ?
                         AND scene_id = ? AND revision = ?""",
                    (
                        target_scene_id,
                        game_id,
                        actor_row["player_id"],
                        scene_id,
                        location_row["revision"],
                    ),
                )
                if location_update.rowcount != 1:
                    raise RuntimeError("actor location revision conflict")

            post_scene_id = (
                target_scene_id
                if target_scene_id is not None and target_scene_id != scene_id
                else scene_id
            )
            if post_scene_id == scene_id:
                post_scene_revision = resulting_scene_revision
            else:
                post_scene = connection.execute(
                    """SELECT revision FROM scenes
                       WHERE scene_id = ? AND game_id = ?""",
                    (post_scene_id, game_id),
                ).fetchone()
                if post_scene is None:
                    raise RuntimeError("outcome target scene disappeared")
                post_scene_revision = int(post_scene["revision"])
            post_participants = connection.execute(
                """SELECT player_id FROM player_locations
                   WHERE game_id = ? AND scene_id = ? ORDER BY player_id""",
                (game_id, post_scene_id),
            ).fetchall()
            post_effect_fiction = {
                "game_id": game_id,
                "player_id": str(actor_row["player_id"]),
                "character_id": actor_character_id,
                "character_revision": resulting_actor_revision,
                "scene_id": post_scene_id,
                "scene_revision": post_scene_revision,
                "location_revision": int(location_row["revision"])
                + (1 if post_scene_id != scene_id else 0),
                "participants": [
                    str(participant["player_id"]) for participant in post_participants
                ],
            }

            connection.execute(
                """INSERT INTO domain_events
                   (game_id, event_type, payload_json, causation_id)
                   VALUES (?, 'scene_patched', ?, ?)""",
                (
                    game_id,
                    json.dumps(
                        {
                            "scene_id": scene_id,
                            "actor_character_id": actor_character_id,
                            "summary": patch.summary,
                            "add_facts": list(patch.add_facts),
                            "remove_facts": list(patch.remove_facts),
                            "add_actor_conditions": [
                                condition.text for condition in patch.add_actor_conditions
                            ],
                            "remove_actor_conditions": list(patch.remove_actor_conditions),
                            "add_actor_plot_items": [
                                item.name for item in patch.add_actor_plot_items
                            ],
                            "remove_actor_plot_items": list(patch.remove_actor_plot_items),
                            "move_actor_to_scene_id": target_scene_id,
                            "upsert_scene_npc_ids": [npc.npc_id for npc in patch.upsert_scene_npcs],
                            "remove_scene_npc_ids": list(patch.remove_scene_npc_ids),
                            "open_threads": list(patch.open_threads),
                            "close_threads": list(patch.close_threads),
                            "granted_temporary_bonus_id": (
                                None
                                if patch.grant_temporary_bonus is None
                                else patch.grant_temporary_bonus.bonus_id
                            ),
                            "scene_revision": resulting_scene_revision,
                            "actor_revision": resulting_actor_revision,
                            "post_effect_fiction": post_effect_fiction,
                            "effect_metadata": (
                                None if effect_metadata is None else dict(effect_metadata)
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    causation_id,
                ),
            )
            if secret_reveals:
                reveals = [
                    {"secret_id": secret_id.strip(), "text": text.strip()}
                    for secret_id, text in secret_reveals.items()
                ]
                if any(not item["secret_id"] or not item["text"] for item in reveals):
                    raise ValueError("revealed secret id and text cannot be empty")
                connection.execute(
                    """INSERT INTO domain_events
                       (game_id, event_type, payload_json, causation_id)
                       VALUES (?, 'secret_revealed', ?, ?)""",
                    (
                        game_id,
                        json.dumps(reveals, ensure_ascii=False),
                        causation_id,
                    ),
                )

    def create_character(self, character: CharacterState) -> None:
        payload = _sheet_payload(character.sheet)
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO characters
                   (character_id, game_id, player_id, biography, sheet_json,
                    conditions_json, plot_items_json,
                    experience_earned, experience_spent, revision)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    character.character_id,
                    character.game_id,
                    character.player_id,
                    character.biography,
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(
                        [
                            {"text": condition.text, "source": condition.source}
                            for condition in character.conditions
                        ],
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        [
                            {"name": item.name, "description": item.description}
                            for item in character.plot_items
                        ],
                        ensure_ascii=False,
                    ),
                    character.experience_earned,
                    character.experience_spent,
                    character.revision,
                ),
            )

    def character_for_player(self, *, game_id: str, player_id: str) -> CharacterState | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                """SELECT * FROM characters WHERE game_id = ? AND player_id = ?""",
                (game_id, player_id),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["sheet_json"])
        sheet = CharacterSheet(
            name=payload["name"],
            traits=tuple(
                Trait(item["name"], item["level"], tuple(item["aspects"]))
                for item in payload["traits"]
            ),
            flags=tuple(
                Flag(item["text"], FlagType(item["type"]), item["locked"])
                for item in payload["flags"]
            ),
            reserve_current=payload["reserve_current"],
            reserve_maximum=payload["reserve_maximum"],
            temporary_bonuses=tuple(
                TemporaryBonus(
                    item["bonus_id"],
                    TemporaryBonusType(item["type"]),
                    item["trigger"],
                )
                for item in payload.get("temporary_bonuses", [])
            ),
        )
        return CharacterState(
            character_id=row["character_id"],
            game_id=row["game_id"],
            player_id=row["player_id"],
            biography=row["biography"],
            sheet=sheet,
            conditions=tuple(
                Condition(item["text"], item["source"])
                for item in json.loads(row["conditions_json"])
            ),
            plot_items=tuple(
                PlotItem(item["name"], item.get("description", ""))
                for item in json.loads(row["plot_items_json"])
            ),
            experience_earned=row["experience_earned"],
            experience_spent=row["experience_spent"],
            revision=row["revision"],
        )

    def character_owner(self, character_id: str) -> dict[str, str] | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                """SELECT game_id, player_id FROM characters
                   WHERE character_id = ?""",
                (character_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "game_id": str(row["game_id"]),
            "player_id": str(row["player_id"]),
        }

    def character_count(self, game_id: str) -> int:
        with closing(self.connect()) as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM characters WHERE game_id = ?",
                    (game_id,),
                ).fetchone()["count"]
            )

    def reserve_projection(self, game_id: str) -> list[dict[str, object]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """SELECT character_id, player_id, sheet_json
                   FROM characters WHERE game_id = ? ORDER BY character_id""",
                (game_id,),
            ).fetchall()
        result: list[dict[str, object]] = []
        for row in rows:
            sheet = json.loads(row["sheet_json"])
            result.append(
                {
                    "character_id": row["character_id"],
                    "player_id": row["player_id"],
                    "name": sheet["name"],
                    "reserve_current": sheet["reserve_current"],
                    "reserve_maximum": sheet["reserve_maximum"],
                }
            )
        return result

    def character_roster(self, game_id: str) -> list[dict[str, object]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """SELECT character.player_id, character.biography, character.sheet_json,
                          character.conditions_json, location.scene_id
                   FROM characters AS character
                   LEFT JOIN player_locations AS location
                     ON location.game_id = character.game_id
                    AND location.player_id = character.player_id
                   WHERE character.game_id = ? ORDER BY character.rowid""",
                (game_id,),
            ).fetchall()
        roster: list[dict[str, object]] = []
        for row in rows:
            sheet = json.loads(row["sheet_json"])
            roster.append(
                {
                    "player_id": row["player_id"],
                    "name": sheet["name"],
                    "biography": row["biography"],
                    "reserve_current": sheet["reserve_current"],
                    "reserve_maximum": sheet["reserve_maximum"],
                    "conditions": [item["text"] for item in json.loads(row["conditions_json"])],
                    "scene_id": row["scene_id"],
                    "ready": row["scene_id"] is not None,
                }
            )
        return roster

    def unplaced_character_count(self, game_id: str) -> int:
        with closing(self.connect()) as connection:
            return int(
                connection.execute(
                    """SELECT COUNT(*) AS count FROM characters AS character
                   LEFT JOIN player_locations AS location
                     ON location.game_id = character.game_id
                    AND location.player_id = character.player_id
                   WHERE character.game_id = ? AND location.player_id IS NULL""",
                    (game_id,),
                ).fetchone()["count"]
            )

    def update_character_progression(
        self,
        *,
        character_id: str,
        expected_revision: int,
        sheet: CharacterSheet,
        xp_cost: int,
        description: str,
        causation_id: str | None = None,
    ) -> CharacterState:
        if xp_cost <= 0:
            raise ValueError("xp_cost must be positive")
        event_causation_id = causation_id or f"advancement:{character_id}:{expected_revision}"
        with self.transaction() as connection:
            existing = connection.execute(
                """SELECT game_id, payload_json FROM domain_events
                   WHERE causation_id = ? AND event_type = 'character_advanced'""",
                (event_causation_id,),
            ).fetchone()
            if existing is not None:
                payload = json.loads(existing["payload_json"])
                if payload.get("character_id") != character_id:
                    raise ValueError("advancement replay targets a different character")
                replayed = connection.execute(
                    "SELECT game_id, player_id FROM characters WHERE character_id = ?",
                    (character_id,),
                ).fetchone()
                if replayed is None or replayed["game_id"] != existing["game_id"]:
                    raise RuntimeError("advanced character no longer exists")
                result_game_id = str(replayed["game_id"])
                result_player_id = str(replayed["player_id"])
            else:
                row = connection.execute(
                    """SELECT game_id, player_id, biography, experience_earned,
                              experience_spent, revision
                       FROM characters WHERE character_id = ?""",
                    (character_id,),
                ).fetchone()
                if row is None or row["revision"] != expected_revision:
                    raise RuntimeError("character revision conflict")
                if row["experience_earned"] - row["experience_spent"] < xp_cost:
                    raise ValueError("not enough experience")
                cursor = connection.execute(
                    """UPDATE characters
                       SET sheet_json = ?, experience_spent = experience_spent + ?,
                           revision = revision + 1
                       WHERE character_id = ? AND revision = ?""",
                    (
                        json.dumps(_sheet_payload(sheet), ensure_ascii=False),
                        xp_cost,
                        character_id,
                        expected_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("character revision conflict")
                connection.execute(
                    """INSERT INTO domain_events
                       (game_id, event_type, payload_json, causation_id)
                       VALUES (?, 'character_advanced', ?, ?)""",
                    (
                        row["game_id"],
                        json.dumps(
                            {
                                "character_id": character_id,
                                "xp_cost": xp_cost,
                                "description": description,
                            },
                            ensure_ascii=False,
                        ),
                        event_causation_id,
                    ),
                )
                result_game_id = str(row["game_id"])
                result_player_id = str(row["player_id"])
        updated = self.character_for_player(
            game_id=result_game_id,
            player_id=result_player_id,
        )
        assert updated is not None
        return updated

    def character_for_advancement_causation(
        self,
        *,
        causation_id: str,
        game_id: str,
        player_id: str,
    ) -> CharacterState | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                """SELECT payload_json FROM domain_events
                   WHERE game_id = ? AND causation_id = ?
                     AND event_type = 'character_advanced'""",
                (game_id, causation_id),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        character = self.character_for_player(game_id=game_id, player_id=player_id)
        if character is None or character.character_id != payload.get("character_id"):
            raise RuntimeError("advancement replay does not match the current character")
        return character

    @staticmethod
    def _activity_update_from_event(
        row: sqlite3.Row,
        *,
        game_id: str,
        occurred_at: str,
    ) -> tuple[ActivityUpdate, tuple[str, ...]]:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("activity replay marker is invalid") from error
        if not isinstance(payload, dict):
            raise RuntimeError("activity replay marker is invalid")
        marker_game_id = payload.get("game_id")
        marker_occurred_at = payload.get("occurred_at")
        if (
            not isinstance(row["game_id"], str)
            or not row["game_id"]
            or not isinstance(marker_game_id, str)
            or not marker_game_id
            or not isinstance(marker_occurred_at, str)
            or not marker_occurred_at
        ):
            raise RuntimeError("activity replay marker is invalid")
        if (
            row["game_id"] != game_id
            or marker_game_id != game_id
            or marker_occurred_at != occurred_at
        ):
            raise RuntimeError("activity replay identity mismatch")
        credited = payload.get("credited_seconds")
        total = payload.get("total_active_seconds")
        xp_each = payload.get("xp_awarded_each")
        raw_character_ids = payload.get("affected_character_ids")
        if (
            isinstance(credited, bool)
            or not isinstance(credited, int)
            or credited < 0
            or isinstance(total, bool)
            or not isinstance(total, int)
            or total < credited
            or isinstance(xp_each, bool)
            or not isinstance(xp_each, int)
            or xp_each < 0
            or not isinstance(raw_character_ids, list)
            or any(not isinstance(item, str) or not item for item in raw_character_ids)
        ):
            raise RuntimeError("activity replay marker is invalid")
        character_ids = tuple(raw_character_ids)
        if character_ids != tuple(sorted(set(character_ids))) or bool(character_ids) != bool(
            xp_each
        ):
            raise RuntimeError("activity replay marker is invalid")
        return ActivityUpdate(credited, total, xp_each), character_ids

    def record_activity(
        self,
        *,
        game_id: str,
        occurred_at,
        causation_id: str | None = None,
    ) -> ActivityUpdate:
        """Record activity and automatically award newly completed XP intervals."""
        from datetime import datetime

        if occurred_at.tzinfo is None:
            raise ValueError("activity timestamp must be timezone-aware")
        normalized_causation_id = None if causation_id is None else causation_id.strip()
        if causation_id is not None and not normalized_causation_id:
            raise ValueError("activity causation id cannot be empty")
        occurred_at_iso = occurred_at.astimezone(UTC).isoformat()
        stored_occurred_at_iso = occurred_at.isoformat()
        with self.transaction() as connection:
            if normalized_causation_id is not None:
                prior = connection.execute(
                    """SELECT game_id, payload_json FROM domain_events
                       WHERE event_type = 'activity_recorded' AND causation_id = ?""",
                    (normalized_causation_id,),
                ).fetchone()
                if prior is not None:
                    update, _ = self._activity_update_from_event(
                        prior,
                        game_id=game_id,
                        occurred_at=occurred_at_iso,
                    )
                    return update
            row = connection.execute(
                """SELECT activity.last_event_at, activity.active_seconds,
                           activity.awarded_intervals, game.progression_enabled
                   FROM session_activity AS activity
                   JOIN games AS game ON game.game_id = activity.game_id
                   WHERE activity.game_id = ?""",
                (game_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"game does not exist: {game_id}")
            previous = (
                datetime.fromisoformat(row["last_event_at"]) if row["last_event_at"] else None
            )
            credited = credited_activity_seconds(previous, occurred_at)
            total = row["active_seconds"]
            if previous is None or occurred_at > previous:
                total += credited
                connection.execute(
                    """UPDATE session_activity
                       SET last_event_at = ?, active_seconds = active_seconds + ?,
                           revision = revision + 1
                       WHERE game_id = ?""",
                    (stored_occurred_at_iso, credited, game_id),
                )
            xp_each = 0
            affected_character_ids: tuple[str, ...] = ()
            completed = total // XP_INTERVAL_SECONDS
            if bool(row["progression_enabled"]) and completed > row["awarded_intervals"]:
                character_rows = connection.execute(
                    """SELECT character_id FROM characters
                       WHERE game_id = ? ORDER BY character_id""",
                    (game_id,),
                ).fetchall()
                affected_character_ids = tuple(
                    str(character["character_id"]) for character in character_rows
                )
                character_count = len(affected_character_ids)
                if character_count > 0:
                    xp_each = completed - row["awarded_intervals"]
                    connection.execute(
                        """UPDATE characters
                           SET experience_earned = experience_earned + ?,
                               revision = revision + 1
                           WHERE game_id = ?""",
                        (xp_each, game_id),
                    )
                    connection.execute(
                        """UPDATE session_activity
                           SET awarded_intervals = ?, revision = revision + 1
                           WHERE game_id = ?""",
                        (completed, game_id),
                    )
                    connection.execute(
                        """INSERT INTO domain_events
                           (game_id, event_type, payload_json, causation_id)
                           VALUES (?, 'experience_awarded', ?, ?)""",
                        (
                            game_id,
                            json.dumps(
                                {
                                    "xp_each": xp_each,
                                    "character_count": character_count,
                                    "completed_intervals": completed,
                                }
                            ),
                            f"automatic-xp:{game_id}:{completed}",
                        ),
                    )
                else:
                    affected_character_ids = ()
            update = ActivityUpdate(credited, total, xp_each)
            if normalized_causation_id is not None:
                connection.execute(
                    """INSERT INTO domain_events
                       (game_id, event_type, payload_json, causation_id)
                       VALUES (?, 'activity_recorded', ?, ?)""",
                    (
                        game_id,
                        json.dumps(
                            {
                                "game_id": game_id,
                                "occurred_at": occurred_at_iso,
                                "credited_seconds": update.credited_seconds,
                                "total_active_seconds": update.total_active_seconds,
                                "xp_awarded_each": update.xp_awarded_each,
                                "affected_character_ids": list(affected_character_ids),
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        normalized_causation_id,
                    ),
                )
            return update

    def activity_record_for_causation(
        self,
        *,
        causation_id: str,
        game_id: str,
        occurred_at,
    ) -> tuple[ActivityUpdate, tuple[str, ...]] | None:
        """Return and validate the exact activity mutation attributed to one cause."""

        if occurred_at.tzinfo is None:
            raise ValueError("activity timestamp must be timezone-aware")
        normalized_causation_id = causation_id.strip()
        if not normalized_causation_id:
            raise ValueError("activity causation id cannot be empty")
        with closing(self.connect()) as connection:
            row = connection.execute(
                """SELECT game_id, payload_json FROM domain_events
                   WHERE event_type = 'activity_recorded' AND causation_id = ?""",
                (normalized_causation_id,),
            ).fetchone()
        if row is None:
            return None
        return self._activity_update_from_event(
            row,
            game_id=game_id,
            occurred_at=occurred_at.astimezone(UTC).isoformat(),
        )

    def start_activity_clock(self, *, game_id: str, started_at) -> None:
        if started_at.tzinfo is None:
            raise ValueError("session start timestamp must be timezone-aware")
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE session_activity
                   SET last_event_at = ?, revision = revision + 1
                   WHERE game_id = ? AND last_event_at IS NULL""",
                (started_at.isoformat(), game_id),
            )
            if cursor.rowcount == 0:
                row = connection.execute(
                    "SELECT 1 FROM session_activity WHERE game_id = ?",
                    (game_id,),
                ).fetchone()
                if row is None:
                    raise ValueError(f"game does not exist: {game_id}")

    def pause_activity_clock(self, *, game_id: str) -> None:
        """Idempotently stop accruing time without changing accumulated activity."""
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE session_activity
                   SET last_event_at = NULL, revision = revision + 1
                   WHERE game_id = ? AND last_event_at IS NOT NULL""",
                (game_id,),
            )
            if cursor.rowcount == 0:
                row = connection.execute(
                    "SELECT 1 FROM session_activity WHERE game_id = ?",
                    (game_id,),
                ).fetchone()
                if row is None:
                    raise ValueError(f"game does not exist: {game_id}")

    def activity_state(self, game_id: str) -> dict[str, int | str | None]:
        with closing(self.connect()) as connection:
            row = connection.execute(
                """SELECT last_event_at, active_seconds, awarded_intervals, revision
                   FROM session_activity WHERE game_id = ?""",
                (game_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"game does not exist: {game_id}")
        return dict(row)

    @classmethod
    def _queue_pending_cancellation_notice(
        cls,
        connection: sqlite3.Connection,
        *,
        interaction_id: str,
        game_id: str,
        player_id: str,
        origin_channel_id: str | None,
        payload: Mapping[str, object],
        notice_key_prefix: str = "pending-cancelled",
        translation_key: str = "stale_route_pending_cancelled",
    ) -> None:
        if origin_channel_id is None:
            return
        prompt_source_event_id = payload.get("prompt_source_event_id")
        root_source_event_id = payload.get("root_source_event_id")
        source_event_ids = tuple(
            dict.fromkeys(
                str(value)
                for value in (prompt_source_event_id, root_source_event_id)
                if value is not None
            )
        )
        if source_event_ids:
            placeholders = ",".join("?" for _ in source_event_ids)
            connection.execute(
                f"""UPDATE outbox_messages
                   SET attempts = 5, error = 'PendingInteractionCancelled',
                       next_attempt_at = NULL
                   WHERE channel_id = ? AND source_event_id IN ({placeholders})
                     AND kind = 'response'
                     AND delivered_at IS NULL AND delivery_claim IS NULL
                     AND attempts < 5""",
                (origin_channel_id, *source_event_ids),
            )
        source_event_id = root_source_event_id or prompt_source_event_id
        game = connection.execute(
            "SELECT locale FROM games WHERE game_id = ?",
            (game_id,),
        ).fetchone()
        locale = "ru" if game is None else str(game["locale"])
        source = (
            None
            if source_event_id is None
            else connection.execute(
                "SELECT guild_id FROM inbox_messages WHERE event_id = ?",
                (str(source_event_id),),
            ).fetchone()
        )
        from masterclaw.app.i18n import tr

        key = f"{notice_key_prefix}:{interaction_id}"
        connection.execute(
            """INSERT OR IGNORE INTO outbox_messages
               (idempotency_key, channel_id, content, kind,
                source_event_id, source_author_id, source_guild_id, discord_nonce)
               VALUES (?, ?, ?, 'system_notice', ?, ?, ?, ?)""",
            (
                key,
                origin_channel_id,
                tr(locale, translation_key),
                None if source_event_id is None else str(source_event_id),
                player_id,
                None if source is None else source["guild_id"],
                _discord_nonce(key),
            ),
        )

    @classmethod
    def _cancel_open_interactions(
        cls,
        connection: sqlite3.Connection,
        *,
        game_id: str,
        reason: str,
        origin_channel_id: str | None = None,
        notice_key_prefix: str = "pending-cancelled",
        translation_key: str = "stale_route_pending_cancelled",
    ) -> None:
        origin_clause = " AND origin_channel_id = ?" if origin_channel_id is not None else ""
        parameters: tuple[object, ...] = (game_id,)
        if origin_channel_id is not None:
            parameters += (origin_channel_id,)
        rows = connection.execute(
            f"""SELECT interaction_id, player_id, origin_channel_id, kind, payload_json
                FROM pending_interactions
                WHERE game_id = ? AND status = 'open'{origin_clause}""",
            parameters,
        ).fetchall()
        for pending in rows:
            if PendingKind(pending["kind"]) is PendingKind.POOL_CONFIRMATION:
                cls._refund_roll_helpers(
                    connection,
                    interaction_id=str(pending["interaction_id"]),
                )
            payload = json.loads(pending["payload_json"])
            payload["closed_reason"] = reason
            cursor = connection.execute(
                """UPDATE pending_interactions
                   SET status = 'cancelled', payload_json = ?,
                       revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                   WHERE interaction_id = ? AND status = 'open'""",
                (
                    json.dumps(payload, ensure_ascii=False),
                    pending["interaction_id"],
                ),
            )
            if cursor.rowcount == 1:
                cls._queue_pending_cancellation_notice(
                    connection,
                    interaction_id=str(pending["interaction_id"]),
                    game_id=game_id,
                    player_id=str(pending["player_id"]),
                    origin_channel_id=(
                        None
                        if pending["origin_channel_id"] is None
                        else str(pending["origin_channel_id"])
                    ),
                    payload=payload,
                    notice_key_prefix=notice_key_prefix,
                    translation_key=translation_key,
                )

    @classmethod
    def _clean_up_detached_channel(
        cls,
        connection: sqlite3.Connection,
        *,
        channel_id: str,
        game_id: str,
    ) -> None:
        """Close work that can no longer receive a continuation after a detach."""
        connection.execute(
            """UPDATE games
               SET narrative_channel_id = NULL, revision = revision + 1
               WHERE game_id = ? AND narrative_channel_id = ?""",
            (game_id, channel_id),
        )
        cls._cancel_open_interactions(
            connection,
            game_id=game_id,
            origin_channel_id=channel_id,
            reason="origin_channel_unbound",
        )
        remaining = connection.execute(
            "SELECT 1 FROM channel_bindings WHERE game_id = ? LIMIT 1",
            (game_id,),
        ).fetchone()
        if remaining is not None:
            return
        cls._cancel_open_interactions(
            connection,
            game_id=game_id,
            reason="last_channel_unbound",
        )
        connection.execute(
            """UPDATE session_activity
               SET last_event_at = NULL, revision = revision + 1
               WHERE game_id = ? AND last_event_at IS NOT NULL""",
            (game_id,),
        )

    @classmethod
    def _cancel_source_pending_if_origin_unbound(
        cls,
        connection: sqlite3.Connection,
        *,
        game_id: str,
        player_id: str,
        origin_channel_id: str,
        source_event_id: str,
        queue_notice: bool = True,
    ) -> bool:
        binding = connection.execute(
            "SELECT game_id FROM channel_bindings WHERE channel_id = ?",
            (origin_channel_id,),
        ).fetchone()
        if binding is not None and binding["game_id"] == game_id:
            return False
        remaining = connection.execute(
            "SELECT 1 FROM channel_bindings WHERE game_id = ? LIMIT 1",
            (game_id,),
        ).fetchone()
        if remaining is None:
            connection.execute(
                """UPDATE session_activity
                   SET last_event_at = NULL, revision = revision + 1
                   WHERE game_id = ? AND last_event_at IS NOT NULL""",
                (game_id,),
            )
        rows = connection.execute(
            """SELECT interaction_id, kind, payload_json, status
               FROM pending_interactions
               WHERE game_id = ? AND player_id = ? AND origin_channel_id = ?
                 AND status IN ('open', 'cancelled')""",
            (game_id, player_id, origin_channel_id),
        ).fetchall()
        prior_match = False
        cancelled_now = False
        for pending in rows:
            payload = json.loads(pending["payload_json"])
            if source_event_id not in {
                payload.get("prompt_source_event_id"),
                payload.get("root_source_event_id"),
            }:
                continue
            if pending["status"] == PendingStatus.CANCELLED.value:
                cancelled_for_detach = payload.get("closed_reason") in {
                    "origin_channel_unbound",
                    "origin_channel_detached_before_completion",
                }
                prior_match = prior_match or cancelled_for_detach
                if cancelled_for_detach:
                    if payload.get("closed_by_event_id") is None:
                        payload["closed_by_event_id"] = source_event_id
                        connection.execute(
                            """UPDATE pending_interactions
                               SET payload_json = ?, updated_at = CURRENT_TIMESTAMP
                               WHERE interaction_id = ? AND status = 'cancelled'""",
                            (
                                json.dumps(payload, ensure_ascii=False),
                                pending["interaction_id"],
                            ),
                        )
                    if queue_notice:
                        cls._queue_pending_cancellation_notice(
                            connection,
                            interaction_id=str(pending["interaction_id"]),
                            game_id=game_id,
                            player_id=player_id,
                            origin_channel_id=origin_channel_id,
                            payload=payload,
                        )
                continue
            if PendingKind(pending["kind"]) is PendingKind.POOL_CONFIRMATION:
                cls._refund_roll_helpers(
                    connection,
                    interaction_id=str(pending["interaction_id"]),
                )
            payload["closed_reason"] = "origin_channel_detached_before_completion"
            payload["closed_by_event_id"] = source_event_id
            cursor = connection.execute(
                """UPDATE pending_interactions
                   SET status = 'cancelled', payload_json = ?,
                       revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                   WHERE interaction_id = ? AND status = 'open'""",
                (
                    json.dumps(payload, ensure_ascii=False),
                    pending["interaction_id"],
                ),
            )
            if cursor.rowcount == 1:
                cancelled_now = True
                if queue_notice:
                    cls._queue_pending_cancellation_notice(
                        connection,
                        interaction_id=str(pending["interaction_id"]),
                        game_id=game_id,
                        player_id=player_id,
                        origin_channel_id=origin_channel_id,
                        payload=payload,
                    )
        return prior_match or cancelled_now

    def cancel_source_pending_if_origin_unbound(
        self,
        *,
        game_id: str,
        player_id: str,
        origin_channel_id: str,
        source_event_id: str,
    ) -> bool:
        """Cancel only the decision created by a frozen turn after its channel detached."""
        with self.transaction() as connection:
            return self._cancel_source_pending_if_origin_unbound(
                connection,
                game_id=game_id,
                player_id=player_id,
                origin_channel_id=origin_channel_id,
                source_event_id=source_event_id,
            )

    @classmethod
    def _remove_inherited_bindings(
        cls,
        connection: sqlite3.Connection,
        *,
        parent_channel_id: str,
    ) -> None:
        rows = connection.execute(
            """SELECT channel_id, game_id FROM channel_bindings
               WHERE inherited_from_channel_id = ?
               ORDER BY channel_id""",
            (parent_channel_id,),
        ).fetchall()
        for row in rows:
            deleted = connection.execute(
                """DELETE FROM channel_bindings
                   WHERE channel_id = ? AND inherited_from_channel_id = ?""",
                (row["channel_id"], parent_channel_id),
            )
            if deleted.rowcount == 1:
                cls._clean_up_detached_channel(
                    connection,
                    channel_id=str(row["channel_id"]),
                    game_id=str(row["game_id"]),
                )

    @classmethod
    def _rebind_inherited_channels(
        cls,
        connection: sqlite3.Connection,
        *,
        parent_channel_id: str,
        game_id: str,
        lifecycle: str,
    ) -> None:
        rows = connection.execute(
            """SELECT channel_id, game_id FROM channel_bindings
               WHERE inherited_from_channel_id = ?
               ORDER BY channel_id""",
            (parent_channel_id,),
        ).fetchall()
        for row in rows:
            workspace = connection.execute(
                """SELECT 1 FROM world_projects
                   WHERE active_channel_id = ?""",
                (row["channel_id"],),
            ).fetchone()
            if workspace is not None:
                raise RuntimeError("inherited channel has an active world workspace")
            updated = connection.execute(
                """UPDATE channel_bindings
                   SET game_id = ?, lifecycle = ?, revision = revision + 1
                   WHERE channel_id = ? AND inherited_from_channel_id = ?""",
                (
                    game_id,
                    lifecycle,
                    row["channel_id"],
                    parent_channel_id,
                ),
            )
            if updated.rowcount == 1 and row["game_id"] != game_id:
                cls._clean_up_detached_channel(
                    connection,
                    channel_id=str(row["channel_id"]),
                    game_id=str(row["game_id"]),
                )
        missing = connection.execute(
            """SELECT monitored.channel_id
               FROM monitored_channels AS monitored
               LEFT JOIN channel_bindings AS binding
                 ON binding.channel_id = monitored.channel_id
               WHERE monitored.inherited_from_channel_id = ?
                 AND binding.channel_id IS NULL
               ORDER BY monitored.channel_id""",
            (parent_channel_id,),
        ).fetchall()
        for row in missing:
            workspace = connection.execute(
                """SELECT 1 FROM world_projects WHERE active_channel_id = ?""",
                (row["channel_id"],),
            ).fetchone()
            if workspace is not None:
                raise RuntimeError("inherited channel has an active world workspace")
            connection.execute(
                """INSERT INTO channel_bindings
                   (channel_id, game_id, lifecycle, inherited_from_channel_id)
                   VALUES (?, ?, ?, ?)""",
                (
                    row["channel_id"],
                    game_id,
                    lifecycle,
                    parent_channel_id,
                ),
            )

    def bind_channel(self, *, channel_id: str, game_id: str) -> None:
        with self.transaction() as connection:
            game = connection.execute(
                "SELECT lifecycle FROM games WHERE game_id = ?", (game_id,)
            ).fetchone()
            if game is None:
                raise ValueError(f"game does not exist: {game_id}")
            workspace = connection.execute(
                """SELECT world_id FROM world_projects
                   WHERE active_channel_id = ?""",
                (channel_id,),
            ).fetchone()
            if workspace is not None:
                raise RuntimeError("channel has an active world workspace")
            previous = connection.execute(
                "SELECT game_id FROM channel_bindings WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()
            connection.execute(
                """INSERT INTO channel_bindings(channel_id, game_id, lifecycle)
                   VALUES (?, ?, ?)
                   ON CONFLICT(channel_id) DO UPDATE SET
                      game_id = excluded.game_id,
                      lifecycle = excluded.lifecycle,
                      inherited_from_channel_id = NULL,
                      revision = channel_bindings.revision + 1""",
                (channel_id, game_id, game["lifecycle"]),
            )
            connection.execute(
                """UPDATE monitored_channels SET inherited_from_channel_id = NULL
                   WHERE channel_id = ? AND inherited_from_channel_id IS NOT NULL""",
                (channel_id,),
            )
            if previous is not None and previous["game_id"] != game_id:
                self._clean_up_detached_channel(
                    connection,
                    channel_id=channel_id,
                    game_id=str(previous["game_id"]),
                )
            self._rebind_inherited_channels(
                connection,
                parent_channel_id=channel_id,
                game_id=game_id,
                lifecycle=str(game["lifecycle"]),
            )

    def unbind_channel(self, *, channel_id: str, expected_game_id: str) -> None:
        """Detach the observed binding and release it as narrative output atomically."""
        with self.transaction() as connection:
            binding = connection.execute(
                "SELECT game_id FROM channel_bindings WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()
            if binding is None:
                # A handler may have committed the detach and then lost its outbox
                # completion transaction. Retrying that frozen command must therefore
                # observe the already-detached state as success.
                return
            if binding["game_id"] != expected_game_id:
                raise RuntimeError("channel binding changed before unbind")
            cursor = connection.execute(
                "DELETE FROM channel_bindings WHERE channel_id = ? AND game_id = ?",
                (channel_id, expected_game_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("channel binding changed before unbind")
            # Preserve FIFO semantics for messages already received after this command: they now
            # belong to the unbound session. The command itself is processing and stays in game.
            connection.execute(
                """UPDATE inbox_messages
                   SET game_id = NULL, routing_lifecycle = NULL,
                       scene_id = NULL, scene_participants_json = '[]'
                   WHERE channel_id = ? AND game_id = ? AND status = 'pending'""",
                (channel_id, expected_game_id),
            )
            self._clean_up_detached_channel(
                connection,
                channel_id=channel_id,
                game_id=expected_game_id,
            )
            self._remove_inherited_bindings(
                connection,
                parent_channel_id=channel_id,
            )

    def unbind_channel_for_event(
        self,
        *,
        event_id: str,
        operation_type: str,
        channel_id: str,
        game_id: str,
    ) -> dict[str, object]:
        """Detach a channel and journal the command in the same transaction."""

        if operation_type not in {"unbind_game", "new_session"}:
            raise ValueError(f"unsupported unbind operation: {operation_type}")
        operation_input = {"game_id": game_id}
        with self.transaction() as connection:
            prior = self._matching_event_operation(
                connection,
                event_id=event_id,
                operation_type=operation_type,
                game_id=game_id,
                channel_id=channel_id,
                input_payload=operation_input,
            )
            if prior is not None:
                return prior
            binding = connection.execute(
                "SELECT game_id FROM channel_bindings WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()
            if binding is None or binding["game_id"] != game_id:
                raise RuntimeError("channel binding changed before unbind")
            cursor = connection.execute(
                "DELETE FROM channel_bindings WHERE channel_id = ? AND game_id = ?",
                (channel_id, game_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("channel binding changed before unbind")
            connection.execute(
                """UPDATE inbox_messages
                   SET game_id = NULL, routing_lifecycle = NULL,
                       scene_id = NULL, scene_participants_json = '[]'
                   WHERE channel_id = ? AND game_id = ? AND status = 'pending'""",
                (channel_id, game_id),
            )
            self._clean_up_detached_channel(
                connection,
                channel_id=channel_id,
                game_id=game_id,
            )
            self._remove_inherited_bindings(
                connection,
                parent_channel_id=channel_id,
            )
            result = {
                "channel_id": channel_id,
                "game_id": game_id,
                "detached": True,
            }
            return self._insert_event_operation(
                connection,
                event_id=event_id,
                operation_type=operation_type,
                game_id=game_id,
                channel_id=channel_id,
                input_payload=operation_input,
                result=result,
            )

    def record_discord_channel(
        self,
        *,
        channel_id: str,
        guild_id: str | None,
        parent_channel_id: str | None,
        kind: str,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO discord_channels
                   (channel_id, guild_id, parent_channel_id, kind, observed_at)
                   VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(channel_id) DO UPDATE SET
                     guild_id = excluded.guild_id,
                     parent_channel_id = excluded.parent_channel_id,
                     kind = excluded.kind,
                     observed_at = CURRENT_TIMESTAMP""",
                (channel_id, guild_id, parent_channel_id, kind),
            )

    def discord_channel(self, channel_id: str) -> dict[str, object] | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                """SELECT channel_id, guild_id, parent_channel_id, kind, observed_at
                   FROM discord_channels WHERE channel_id = ?""",
                (channel_id,),
            ).fetchone()
        return None if row is None else dict(row)

    def inherit_thread_context(
        self, *, channel_id: str, parent_channel_id: str, guild_id: str | None
    ) -> bool:
        """Reconcile an enabled parent's monitoring and game binding atomically."""
        with self.transaction() as connection:
            parent_registry = connection.execute(
                "SELECT guild_id FROM discord_channels WHERE channel_id = ?",
                (parent_channel_id,),
            ).fetchone()
            if (
                parent_registry is not None
                and parent_registry["guild_id"] is not None
                and parent_registry["guild_id"] != guild_id
            ):
                raise ValueError("thread and parent channel belong to different Discord servers")
            monitored = connection.execute(
                """SELECT inherited_from_channel_id FROM monitored_channels
                   WHERE channel_id = ?""",
                (parent_channel_id,),
            ).fetchone()
            if monitored is None:
                existing = connection.execute(
                    """SELECT game_id, inherited_from_channel_id
                       FROM channel_bindings WHERE channel_id = ?""",
                    (channel_id,),
                ).fetchone()
                connection.execute(
                    """DELETE FROM monitored_channels
                       WHERE channel_id = ? AND inherited_from_channel_id = ?""",
                    (channel_id, parent_channel_id),
                )
                deleted = connection.execute(
                    """DELETE FROM channel_bindings
                       WHERE channel_id = ? AND inherited_from_channel_id = ?""",
                    (channel_id, parent_channel_id),
                )
                if deleted.rowcount == 1 and existing is not None:
                    self._clean_up_detached_channel(
                        connection,
                        channel_id=channel_id,
                        game_id=str(existing["game_id"]),
                    )
                return False
            existing = connection.execute(
                """SELECT game_id, lifecycle, inherited_from_channel_id
                   FROM channel_bindings
                   WHERE channel_id = ?""",
                (channel_id,),
            ).fetchone()
            parent_binding = connection.execute(
                """SELECT game_id, lifecycle FROM channel_bindings
                   WHERE channel_id = ?""",
                (parent_channel_id,),
            ).fetchone()
            child_monitoring = connection.execute(
                """SELECT inherited_from_channel_id FROM monitored_channels
                   WHERE channel_id = ?""",
                (channel_id,),
            ).fetchone()
            if (
                existing is None
                and child_monitoring is not None
                and child_monitoring["inherited_from_channel_id"] is None
            ):
                return True
            workspace = connection.execute(
                """SELECT 1 FROM world_projects WHERE active_channel_id = ?""",
                (channel_id,),
            ).fetchone()
            if (
                parent_binding is not None
                and workspace is not None
                and (existing is None or existing["inherited_from_channel_id"] is not None)
            ):
                raise ValueError("thread has an active world workspace")
            if child_monitoring is None:
                connection.execute(
                    """INSERT INTO monitored_channels
                       (channel_id, inherited_from_channel_id) VALUES (?, ?)""",
                    (channel_id, parent_channel_id),
                )
            elif child_monitoring["inherited_from_channel_id"] is not None:
                connection.execute(
                    """UPDATE monitored_channels SET inherited_from_channel_id = ?
                       WHERE channel_id = ? AND inherited_from_channel_id IS NOT NULL""",
                    (parent_channel_id, channel_id),
                )
            if parent_binding is None:
                if (
                    existing is not None
                    and existing["inherited_from_channel_id"] == parent_channel_id
                ):
                    deleted = connection.execute(
                        """DELETE FROM channel_bindings
                           WHERE channel_id = ? AND inherited_from_channel_id = ?""",
                        (channel_id, parent_channel_id),
                    )
                    if deleted.rowcount == 1:
                        self._clean_up_detached_channel(
                            connection,
                            channel_id=channel_id,
                            game_id=str(existing["game_id"]),
                        )
            elif existing is None:
                connection.execute(
                    """INSERT INTO channel_bindings
                       (channel_id, game_id, lifecycle, inherited_from_channel_id)
                       VALUES (?, ?, ?, ?)""",
                    (
                        channel_id,
                        parent_binding["game_id"],
                        parent_binding["lifecycle"],
                        parent_channel_id,
                    ),
                )
            elif existing["inherited_from_channel_id"] is not None and (
                existing["game_id"] != parent_binding["game_id"]
                or existing["lifecycle"] != parent_binding["lifecycle"]
                or existing["inherited_from_channel_id"] != parent_channel_id
            ):
                updated = connection.execute(
                    """UPDATE channel_bindings
                       SET game_id = ?, lifecycle = ?, inherited_from_channel_id = ?,
                           revision = revision + 1
                       WHERE channel_id = ? AND inherited_from_channel_id IS NOT NULL""",
                    (
                        parent_binding["game_id"],
                        parent_binding["lifecycle"],
                        parent_channel_id,
                        channel_id,
                    ),
                )
                if updated.rowcount == 1 and existing["game_id"] != parent_binding["game_id"]:
                    self._clean_up_detached_channel(
                        connection,
                        channel_id=channel_id,
                        game_id=str(existing["game_id"]),
                    )
            return True

    def enable_channel_monitoring(self, channel_id: str) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute(
                """INSERT INTO monitored_channels(channel_id, inherited_from_channel_id)
                   VALUES (?, NULL)
                   ON CONFLICT(channel_id) DO UPDATE SET inherited_from_channel_id = NULL
                   WHERE monitored_channels.inherited_from_channel_id IS NOT NULL""",
                (channel_id,),
            )
            return cursor.rowcount == 1

    def disable_channel_monitoring(self, channel_id: str) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM monitored_channels WHERE channel_id = ?",
                (channel_id,),
            )
            if cursor.rowcount == 1:
                connection.execute(
                    """DELETE FROM monitored_channels
                       WHERE inherited_from_channel_id = ?""",
                    (channel_id,),
                )
                self._remove_inherited_bindings(
                    connection,
                    parent_channel_id=channel_id,
                )
            return cursor.rowcount == 1

    def channel_monitoring_enabled(self, channel_id: str) -> bool:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM monitored_channels WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()
        return row is not None

    def channel_state(self, channel_id: str) -> ChannelState:
        with closing(self.connect()) as connection:
            row = connection.execute(
                """SELECT binding.channel_id, binding.game_id,
                          COALESCE(game.lifecycle, binding.lifecycle) AS lifecycle
                   FROM channel_bindings AS binding
                   LEFT JOIN games AS game ON game.game_id = binding.game_id
                   WHERE binding.channel_id = ?""",
                (channel_id,),
            ).fetchone()
        if row is None:
            return ChannelState(channel_id)
        lifecycle = GameLifecycle(row["lifecycle"]) if row["lifecycle"] else None
        return ChannelState(row["channel_id"], row["game_id"], lifecycle)

    def update_game_lifecycle(
        self,
        *,
        game_id: str,
        expected_revision: int,
        lifecycle: GameLifecycle,
    ) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE games SET lifecycle = ?, revision = revision + 1
                   WHERE game_id = ? AND revision = ?""",
                (lifecycle.value, game_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("game revision conflict")

    def transition_game_for_event(
        self,
        *,
        event_id: str,
        operation_type: str,
        game_id: str,
        channel_id: str,
        expected_revision: int,
        occurred_at: datetime | None = None,
    ) -> dict[str, object]:
        """Commit a lifecycle/activity transition with its event replay marker."""

        targets = {
            "start_game": GameLifecycle.ACTIVE,
            "pause_game": GameLifecycle.PAUSED,
            "resume_game": GameLifecycle.ACTIVE,
            "finish_game": GameLifecycle.FINISHED,
        }
        if operation_type not in targets:
            raise ValueError(f"unsupported lifecycle operation: {operation_type}")
        target = targets[operation_type]
        occurred_at_value = None if occurred_at is None else occurred_at.isoformat()
        operation_input = {
            "target_lifecycle": target.value,
            "occurred_at": occurred_at_value,
        }
        with self.transaction() as connection:
            prior = self._matching_event_operation(
                connection,
                event_id=event_id,
                operation_type=operation_type,
                game_id=game_id,
                channel_id=channel_id,
                input_payload=operation_input,
            )
            if prior is not None:
                return prior
            current = connection.execute(
                "SELECT lifecycle, revision FROM games WHERE game_id = ?",
                (game_id,),
            ).fetchone()
            if current is None:
                raise ValueError(f"game does not exist: {game_id}")
            lifecycle = GameLifecycle(str(current["lifecycle"]))
            allowed = {
                "start_game": {GameLifecycle.PREPARING, GameLifecycle.ACTIVE},
                "pause_game": {GameLifecycle.ACTIVE, GameLifecycle.PAUSED},
                "resume_game": {GameLifecycle.PAUSED, GameLifecycle.ACTIVE},
                "finish_game": {
                    GameLifecycle.ACTIVE,
                    GameLifecycle.PAUSED,
                    GameLifecycle.FINISHED,
                },
            }[operation_type]
            if lifecycle not in allowed:
                raise ValueError(f"game cannot {operation_type} from lifecycle {lifecycle.value}")
            if int(current["revision"]) != expected_revision:
                raise RuntimeError("game revision conflict")
            if lifecycle is not target:
                cursor = connection.execute(
                    """UPDATE games SET lifecycle = ?, revision = revision + 1
                       WHERE game_id = ? AND revision = ? AND lifecycle = ?""",
                    (
                        target.value,
                        game_id,
                        expected_revision,
                        lifecycle.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("game revision conflict")
            if operation_type in {"start_game", "resume_game"}:
                if occurred_at is None:
                    raise ValueError("activity start timestamp is required")
                connection.execute(
                    """UPDATE session_activity
                       SET last_event_at = ?, revision = revision + 1
                       WHERE game_id = ? AND last_event_at IS NULL""",
                    (occurred_at_value, game_id),
                )
            else:
                connection.execute(
                    """UPDATE session_activity
                       SET last_event_at = NULL, revision = revision + 1
                       WHERE game_id = ? AND last_event_at IS NOT NULL""",
                    (game_id,),
                )
            if operation_type == "finish_game":
                self._cancel_open_interactions(
                    connection,
                    game_id=game_id,
                    reason="game_finished",
                    notice_key_prefix="game-finished-pending-cancelled",
                    translation_key="game_finished_pending_cancelled",
                )
            result = self._game_operation_result(connection, game_id=game_id)
            return self._insert_event_operation(
                connection,
                event_id=event_id,
                operation_type=operation_type,
                game_id=game_id,
                channel_id=channel_id,
                input_payload=operation_input,
                result=result,
            )

    def finish_game_and_cleanup(
        self,
        *,
        game_id: str,
        expected_revision: int,
    ) -> GameState:
        """Finish a game, stop its clock, and cancel/refund every pending step atomically."""

        with self.transaction() as connection:
            current = connection.execute(
                "SELECT lifecycle, revision FROM games WHERE game_id = ?",
                (game_id,),
            ).fetchone()
            if current is None:
                raise ValueError(f"game does not exist: {game_id}")
            lifecycle = GameLifecycle(str(current["lifecycle"]))
            if lifecycle is not GameLifecycle.FINISHED:
                if lifecycle not in {GameLifecycle.ACTIVE, GameLifecycle.PAUSED}:
                    raise ValueError(f"game cannot finish from lifecycle {lifecycle.value}")
                cursor = connection.execute(
                    """UPDATE games SET lifecycle = 'finished', revision = revision + 1
                       WHERE game_id = ? AND revision = ?
                         AND lifecycle IN ('active', 'paused')""",
                    (game_id, expected_revision),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("game revision conflict")
            # This cleanup is deliberately repeated even for an already-finished aggregate:
            # upgrades or an older non-atomic implementation may have left partial rows behind.
            connection.execute(
                """UPDATE session_activity
                   SET last_event_at = NULL, revision = revision + 1
                   WHERE game_id = ? AND last_event_at IS NOT NULL""",
                (game_id,),
            )
            self._cancel_open_interactions(
                connection,
                game_id=game_id,
                reason="game_finished",
                notice_key_prefix="game-finished-pending-cancelled",
                translation_key="game_finished_pending_cancelled",
            )
        updated = self.game_state(game_id)
        assert updated is not None
        return updated

    def game_state(self, game_id: str) -> GameState | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                """SELECT games.*, COALESCE(game_rules.reserve_recovery_mode, 'both')
                              AS reserve_recovery_mode
                   FROM games
                   LEFT JOIN game_rules ON game_rules.game_id = games.game_id
                   WHERE games.game_id = ?""",
                (game_id,),
            ).fetchone()
        if row is None:
            return None
        return GameState(
            game_id=row["game_id"],
            world_id=row["world_id"],
            lifecycle=GameLifecycle(row["lifecycle"]),
            progression_enabled=bool(row["progression_enabled"]),
            narrator_rights_level=NarratorRightsLevel(row["narrator_rights_level"]),
            locale=row["locale"],
            narrative_channel_id=row["narrative_channel_id"],
            reserve_recovery_mode=ReserveRecoveryMode(row["reserve_recovery_mode"]),
            revision=row["revision"],
        )

    @staticmethod
    def _reserve_recovery_decision_event(
        connection: sqlite3.Connection,
        *,
        causation_id: str,
    ) -> dict[str, object] | None:
        row = connection.execute(
            """SELECT game_id, payload_json FROM domain_events
               WHERE event_type = 'reserve_recovery_decision'
                 AND causation_id = ?""",
            (f"reserve-decision:{causation_id}",),
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict) or payload.get("game_id") != row["game_id"]:
            raise RuntimeError("reserve recovery checkpoint is invalid")
        return payload

    def reserve_recovery_decision(self, causation_id: str) -> dict[str, object] | None:
        """Return the canonical recovery targets chosen for one resolved consequence."""

        with closing(self.connect()) as connection:
            return self._reserve_recovery_decision_event(
                connection,
                causation_id=causation_id,
            )

    def checkpoint_reserve_recovery_decision(
        self,
        *,
        game_id: str,
        causation_id: str,
        safe_rest_completed: bool,
        safe_rest_reason: str | None,
        awards: tuple[tuple[str, str], ...],
    ) -> dict[str, object]:
        """Persist first-writer-wins recovery targets before applying idempotent awards."""

        payload, _ = self.checkpoint_reserve_recovery_decision_with_status(
            game_id=game_id,
            causation_id=causation_id,
            safe_rest_completed=safe_rest_completed,
            safe_rest_reason=safe_rest_reason,
            awards=awards,
        )
        return payload

    def checkpoint_reserve_recovery_decision_with_status(
        self,
        *,
        game_id: str,
        causation_id: str,
        safe_rest_completed: bool,
        safe_rest_reason: str | None,
        awards: tuple[tuple[str, str], ...],
    ) -> tuple[dict[str, object], bool]:
        """Return atomic insert ownership; the legacy persisted payload remains unchanged."""

        if not causation_id:
            raise ValueError("reserve recovery causation id cannot be empty")
        normalized_rest_reason = (
            None if safe_rest_reason is None else safe_rest_reason.strip() or None
        )
        if safe_rest_completed and normalized_rest_reason is None:
            raise ValueError("completed safe rest requires a reason")
        normalized_awards = tuple(
            (player_id.strip(), reason.strip()) for player_id, reason in awards
        )
        if any(not player_id or not reason for player_id, reason in normalized_awards):
            raise ValueError("reserve award player and reason cannot be empty")
        player_ids = [player_id for player_id, _ in normalized_awards]
        if len(set(player_ids)) != len(player_ids):
            raise ValueError("reserve recovery cannot award one player twice")
        event_causation_id = f"reserve-decision:{causation_id}"
        with self.transaction() as connection:
            prior = self._reserve_recovery_decision_event(
                connection,
                causation_id=causation_id,
            )
            if prior is not None:
                if prior.get("game_id") != game_id:
                    raise RuntimeError("reserve recovery decision belongs to another game")
                return prior, False
            game = connection.execute(
                "SELECT 1 FROM games WHERE game_id = ?",
                (game_id,),
            ).fetchone()
            if game is None:
                raise ValueError(f"game does not exist: {game_id}")
            if player_ids:
                placeholders = ",".join("?" for _ in player_ids)
                rows = connection.execute(
                    f"""SELECT player_id FROM characters
                        WHERE game_id = ? AND player_id IN ({placeholders})""",
                    (game_id, *player_ids),
                ).fetchall()
                known_players = {str(row["player_id"]) for row in rows}
                missing_players = set(player_ids) - known_players
                if missing_players:
                    raise ValueError(
                        "reserve recovery target has no character: "
                        + ", ".join(sorted(missing_players))
                    )
            payload: dict[str, object] = {
                "game_id": game_id,
                "safe_rest_completed": bool(safe_rest_completed),
                "safe_rest_reason": normalized_rest_reason,
                "awards": [
                    {"player_id": player_id, "reason": reason}
                    for player_id, reason in normalized_awards
                ],
            }
            connection.execute(
                """INSERT INTO domain_events
                   (game_id, event_type, payload_json, causation_id)
                   VALUES (?, 'reserve_recovery_decision', ?, ?)""",
                (
                    game_id,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    event_causation_id,
                ),
            )
            return payload, True

    def restore_reserve_for_safe_rest(self, *, game_id: str, reason: str, causation_id: str) -> int:
        """Restore every character to their maximum reserve in one audited transaction."""
        changed = 0
        with self.transaction() as connection:
            prior = connection.execute(
                """SELECT payload_json FROM domain_events
                   WHERE causation_id = ? AND event_type = 'reserve_safe_rest'""",
                (causation_id,),
            ).fetchone()
            if prior is not None:
                return len(json.loads(prior["payload_json"])["restored"])
            rows = connection.execute(
                "SELECT character_id, sheet_json FROM characters WHERE game_id = ?",
                (game_id,),
            ).fetchall()
            restored: list[dict[str, object]] = []
            for row in rows:
                sheet = json.loads(row["sheet_json"])
                before = int(sheet["reserve_current"])
                after = int(sheet["reserve_maximum"])
                if before == after:
                    continue
                sheet["reserve_current"] = after
                connection.execute(
                    """UPDATE characters SET sheet_json = ?, revision = revision + 1
                       WHERE character_id = ?""",
                    (json.dumps(sheet, ensure_ascii=False), row["character_id"]),
                )
                changed += 1
                restored.append(
                    {"character_id": row["character_id"], "before": before, "after": after}
                )
            connection.execute(
                """INSERT INTO domain_events
                   (game_id, event_type, payload_json, causation_id)
                   VALUES (?, 'reserve_safe_rest', ?, ?)""",
                (
                    game_id,
                    json.dumps({"reason": reason, "restored": restored}, ensure_ascii=False),
                    causation_id,
                ),
            )
        return changed

    def award_reserve_die(
        self,
        *,
        game_id: str,
        player_id: str,
        reason: str,
        causation_id: str,
    ) -> tuple[int, int]:
        """Restore one reserve die to one character, capped by that character's maximum."""
        with self.transaction() as connection:
            prior = connection.execute(
                """SELECT payload_json FROM domain_events
                   WHERE causation_id = ? AND event_type = 'reserve_roleplay_award'""",
                (causation_id,),
            ).fetchone()
            if prior is not None:
                payload = json.loads(prior["payload_json"])
                return int(payload["before"]), int(payload["after"])
            row = connection.execute(
                """SELECT character_id, sheet_json FROM characters
                   WHERE game_id = ? AND player_id = ?""",
                (game_id, player_id),
            ).fetchone()
            if row is None:
                raise ValueError("target player has no character")
            sheet = json.loads(row["sheet_json"])
            before = int(sheet["reserve_current"])
            after = min(int(sheet["reserve_maximum"]), before + 1)
            if after != before:
                sheet["reserve_current"] = after
                connection.execute(
                    """UPDATE characters SET sheet_json = ?, revision = revision + 1
                       WHERE character_id = ?""",
                    (json.dumps(sheet, ensure_ascii=False), row["character_id"]),
                )
            connection.execute(
                """INSERT INTO domain_events
                   (game_id, event_type, payload_json, causation_id)
                   VALUES (?, 'reserve_roleplay_award', ?, ?)""",
                (
                    game_id,
                    json.dumps(
                        {
                            "player_id": player_id,
                            "character_id": row["character_id"],
                            "reason": reason,
                            "before": before,
                            "after": after,
                        },
                        ensure_ascii=False,
                    ),
                    causation_id,
                ),
            )
        return before, after

    def recent_domain_events(self, *, game_id: str, limit: int) -> list[dict[str, object]]:
        if limit <= 0:
            return []
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """SELECT event_type, payload_json, causation_id, created_at
                   FROM domain_events WHERE game_id = ?
                   ORDER BY id DESC LIMIT ?""",
                (game_id, limit),
            ).fetchall()
        return [
            {
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
                "causation_id": row["causation_id"],
                "created_at": row["created_at"],
            }
            for row in reversed(rows)
        ]

    def domain_event_for_causation(
        self,
        *,
        event_type: str,
        causation_id: str,
    ) -> dict[str, object] | None:
        """Return one exact canonical event envelope for effect-first replay."""

        if not event_type.strip() or not causation_id.strip():
            raise ValueError("event_type and causation_id are required")
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """SELECT game_id, event_type, payload_json, causation_id, created_at
                   FROM domain_events
                   WHERE event_type = ? AND causation_id = ?
                   LIMIT 2""",
                (event_type, causation_id),
            ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise RuntimeError("canonical domain event is not unique")
        payload = json.loads(rows[0]["payload_json"])
        if not isinstance(payload, dict):
            raise RuntimeError("canonical domain event payload must be an object")
        return {
            "game_id": rows[0]["game_id"],
            "event_type": str(rows[0]["event_type"]),
            "payload": payload,
            "causation_id": str(rows[0]["causation_id"]),
            "created_at": str(rows[0]["created_at"]),
        }

    def revealed_secret_ids(self, game_id: str) -> set[str]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """SELECT payload_json FROM domain_events
                   WHERE game_id = ? AND event_type = 'secret_revealed'
                   ORDER BY id""",
                (game_id,),
            ).fetchall()
        revealed: set[str] = set()
        for row in rows:
            payload = json.loads(row["payload_json"])
            items = payload.get("reveals", []) if isinstance(payload, dict) else payload
            if not isinstance(items, list):
                continue
            revealed.update(
                str(item["secret_id"])
                for item in items
                if isinstance(item, dict) and item.get("secret_id")
            )
        return revealed

    def record_interaction_event(
        self,
        *,
        game_id: str,
        scene_id: str | None,
        actor_role: str,
        kind: str,
        text: str,
        causation_id: str,
        player_id: str | None = None,
        summary: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> bool:
        """Persist one compact, canonical interaction for later GM context."""
        actor_role = actor_role.strip()
        kind = kind.strip()
        causation_id = causation_id.strip()
        compact_text = _compact_interaction_text(text, limit=4000)
        compact_summary = (
            None if summary is None else _compact_interaction_text(summary, limit=1000)
        )
        if not actor_role or not kind or not causation_id or not compact_text:
            raise ValueError("interaction role, kind, text, and causation_id are required")

        with self.transaction() as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM games WHERE game_id = ?",
                    (game_id,),
                ).fetchone()
                is None
            ):
                raise ValueError("interaction game does not exist")
            if scene_id is not None and (
                connection.execute(
                    "SELECT 1 FROM scenes WHERE scene_id = ? AND game_id = ?",
                    (scene_id, game_id),
                ).fetchone()
                is None
            ):
                raise ValueError("interaction scene does not belong to game")

            player: dict[str, object] | None = None
            if player_id is not None:
                player = {"player_id": player_id}
                character = connection.execute(
                    """SELECT character_id, sheet_json FROM characters
                       WHERE game_id = ? AND player_id = ?""",
                    (game_id, player_id),
                ).fetchone()
                if character is not None:
                    sheet = json.loads(character["sheet_json"])
                    player.update(
                        {
                            "character_id": character["character_id"],
                            "character_name": sheet["name"],
                        }
                    )

            payload: dict[str, object] = {
                "kind": kind,
                "scene_id": scene_id,
                "actor": {"role": actor_role},
                "text": compact_text,
            }
            if player is not None:
                payload["player"] = player
            if compact_summary:
                payload["summary"] = compact_summary
            if metadata:
                payload["metadata"] = metadata

            cursor = connection.execute(
                """INSERT OR IGNORE INTO domain_events
                   (game_id, event_type, payload_json, causation_id)
                   VALUES (?, 'interaction_recorded', ?, ?)""",
                (
                    game_id,
                    json.dumps(payload, ensure_ascii=False),
                    causation_id,
                ),
            )
        return cursor.rowcount == 1

    def recent_chat_messages(
        self,
        *,
        limit: int,
        game_id: str | None = None,
        channel_id: str | None = None,
        player_id: str | None = None,
    ) -> list[dict[str, object]]:
        if limit <= 0 or (game_id is None and channel_id is None):
            return []

        def scope(alias: str) -> tuple[str, tuple[object, ...]]:
            if game_id is not None and player_id is not None:
                channel_clause = f"AND {alias}.channel_id = ?" if channel_id is not None else ""
                where = f"""{alias}.game_id = ? {channel_clause}
                AND (
                    {alias}.author_id = ?
                    OR EXISTS (
                        SELECT 1 FROM json_each({alias}.scene_participants_json) AS audience
                        WHERE CAST(audience.value AS TEXT) = ?
                    )
                )"""
                values: tuple[object, ...] = (game_id,)
                if channel_id is not None:
                    values += (channel_id,)
                return where, values + (player_id, player_id)
            if game_id is not None and channel_id is not None:
                return f"{alias}.game_id = ? AND {alias}.channel_id = ?", (
                    game_id,
                    channel_id,
                )
            if channel_id is not None:
                # With no explicit game, use the channel's current binding. `IS` also matches
                # NULL while unbound, so a new world-selection session cannot see prior games.
                return (
                    f"""{alias}.channel_id = ? AND {alias}.game_id IS (
                        SELECT current.game_id FROM channel_bindings AS current
                        WHERE current.channel_id = ?
                    )""",
                    (channel_id, channel_id),
                )
            return f"{alias}.game_id = ?", (game_id,)

        user_where, user_params = scope("messages")
        assistant_where, assistant_params = scope("source")
        with closing(self.connect()) as connection:
            rows = connection.execute(
                f"""WITH conversation AS (
                    SELECT messages.event_id AS event_id,
                           messages.event_id AS source_event_id,
                           messages.channel_id AS channel_id,
                           messages.author_id AS author_id,
                           messages.content AS content,
                           messages.created_at AS created_at,
                           'user' AS role,
                           NULL AS recipient_author_id,
                           messages.reply_to_event_id AS reply_to_event_id,
                           messages.reply_to_author_id AS reply_to_author_id,
                           messages.reply_context AS reply_context,
                           messages.attachments_json AS attachments_json,
                           0 AS turn_order,
                           0 AS ordinal
                    FROM inbox_messages AS messages
                    LEFT JOIN channel_bindings AS bindings
                      ON bindings.channel_id = messages.channel_id
                    WHERE {user_where} AND messages.status = 'processed'
                    UNION ALL
                    SELECT 'assistant:' || responses.source_event_id || ':' || responses.ordinal,
                           responses.source_event_id,
                           responses.channel_id,
                           'assistant',
                           responses.content,
                           source.created_at,
                           'assistant',
                           responses.recipient_author_id,
                           NULL,
                           NULL,
                           NULL,
                           '[]',
                           1,
                           responses.ordinal
                    FROM assistant_responses AS responses
                    JOIN inbox_messages AS source
                      ON source.event_id = responses.source_event_id
                    LEFT JOIN channel_bindings AS source_bindings
                      ON source_bindings.channel_id = source.channel_id
                    WHERE {assistant_where} AND source.status = 'processed'
                )
                SELECT event_id, source_event_id, channel_id, author_id, content,
                       created_at, role, recipient_author_id, reply_to_event_id,
                       reply_to_author_id, reply_context, attachments_json
                FROM conversation
                ORDER BY created_at DESC, source_event_id DESC,
                         turn_order DESC, ordinal DESC
                LIMIT ?""",
                (*user_params, *assistant_params, limit),
            ).fetchall()
        history: list[dict[str, object]] = []
        for row in reversed(rows):
            item = dict(row)
            item["attachments"] = json.loads(str(item.pop("attachments_json") or "[]"))
            history.append(item)
        return history

    @staticmethod
    def _incoming_message_from_row(
        row: sqlite3.Row,
        *,
        has_routing_snapshot: bool = False,
    ) -> IncomingMessage:
        raw_attachments = json.loads(row["attachments_json"] or "[]")
        attachments = tuple(
            IncomingAttachment(
                attachment_id=str(item.get("attachment_id", "")),
                filename=str(item.get("filename", "attachment")),
                content_type=(
                    str(item["content_type"]) if item.get("content_type") is not None else None
                ),
                size=int(item["size"]) if item.get("size") is not None else None,
            )
            for item in raw_attachments
            if isinstance(item, dict)
        )
        return IncomingMessage(
            event_id=row["event_id"],
            channel_id=row["channel_id"],
            author_id=row["author_id"],
            content=row["content"],
            created_at=datetime.fromisoformat(row["created_at"]),
            guild_id=row["guild_id"],
            parent_channel_id=row["parent_channel_id"],
            reply_to_event_id=row["reply_to_event_id"],
            reply_to_author_id=row["reply_to_author_id"],
            reply_context=row["reply_context"],
            attachments=attachments,
            routing_game_id=(str(row["game_id"]) if row["game_id"] is not None else None)
            if has_routing_snapshot
            else None,
            routing_lifecycle=(
                GameLifecycle(str(row["routing_lifecycle"]))
                if has_routing_snapshot and row["routing_lifecycle"] is not None
                else None
            ),
            routing_scene_id=(
                str(row["scene_id"])
                if has_routing_snapshot and row["scene_id"] is not None
                else None
            ),
            has_routing_snapshot=has_routing_snapshot,
        )

    def pending(self, *, channel_id: str, limit: int = 50) -> list[IncomingMessage]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """SELECT event_id, channel_id, author_id, content, created_at,
                          guild_id, parent_channel_id, reply_to_event_id,
                          reply_to_author_id, reply_context, attachments_json
                   FROM inbox_messages
                   WHERE channel_id = ? AND status = 'pending'
                   ORDER BY created_at, event_id LIMIT ?""",
                (channel_id, limit),
            ).fetchall()
        return [self._incoming_message_from_row(row) for row in rows]

    def pending_inbox_channels(self) -> list[str]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """WITH channel_heads AS (
                       SELECT channel_id, next_attempt_at,
                              ROW_NUMBER() OVER (
                                  PARTITION BY channel_id
                                  ORDER BY created_at, event_id
                              ) AS position
                       FROM inbox_messages
                       WHERE status = 'pending'
                   )
                   SELECT channel_id FROM channel_heads
                   WHERE position = 1
                     AND (
                       next_attempt_at IS NULL
                       OR next_attempt_at <= CURRENT_TIMESTAMP
                     )
                   ORDER BY channel_id"""
            ).fetchall()
        return [str(row["channel_id"]) for row in rows]

    def pending_inbox_schedule(self) -> list[tuple[str, int]]:
        """Return every pending channel and the durable delay until its next attempt."""
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """WITH channel_heads AS (
                       SELECT channel_id, next_attempt_at,
                              ROW_NUMBER() OVER (
                                  PARTITION BY channel_id
                                  ORDER BY created_at, event_id
                              ) AS position
                       FROM inbox_messages
                       WHERE status = 'pending'
                   )
                   SELECT channel_id,
                          CASE
                            WHEN next_attempt_at IS NULL
                              OR next_attempt_at <= CURRENT_TIMESTAMP
                            THEN CURRENT_TIMESTAMP
                            ELSE next_attempt_at
                          END AS ready_at
                   FROM channel_heads
                   WHERE position = 1
                   ORDER BY channel_id"""
            ).fetchall()
        now = datetime.now(UTC)
        schedule: list[tuple[str, int]] = []
        for row in rows:
            ready_at = datetime.fromisoformat(str(row["ready_at"]))
            if ready_at.tzinfo is None:
                ready_at = ready_at.replace(tzinfo=UTC)
            remaining = max(0.0, (ready_at - now).total_seconds())
            schedule.append((str(row["channel_id"]), int(remaining + 0.999)))
        return schedule

    def defer_pending_channel(self, *, channel_id: str, error: str) -> int | None:
        """Back off a Discord channel cache miss and eventually dead-letter its inbox."""
        with self.transaction() as connection:
            row = connection.execute(
                """SELECT MAX(transport_attempts) AS attempts
                   FROM inbox_messages WHERE channel_id = ? AND status = 'pending'""",
                (channel_id,),
            ).fetchone()
            if row is None or row["attempts"] is None:
                return None
            attempts = int(row["attempts"]) + 1
            if attempts >= len(INBOX_CHANNEL_RETRY_DELAYS_SECONDS):
                connection.execute(
                    """UPDATE inbox_messages
                       SET status = 'failed', transport_attempts = ?, error = ?,
                           next_attempt_at = NULL
                       WHERE channel_id = ? AND status = 'pending'""",
                    (attempts, error[:2000], channel_id),
                )
                return None
            delay = INBOX_CHANNEL_RETRY_DELAYS_SECONDS[attempts - 1]
            connection.execute(
                """UPDATE inbox_messages
                   SET transport_attempts = transport_attempts + 1, error = ?,
                       next_attempt_at = datetime('now', ?)
                   WHERE channel_id = ? AND status = 'pending'""",
                (error[:2000], f"+{delay} seconds", channel_id),
            )
            return delay

    def mark_channel_available(self, channel_id: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                """UPDATE inbox_messages
                   SET transport_attempts = 0, next_attempt_at = NULL,
                       error = NULL
                   WHERE channel_id = ? AND status = 'pending'
                     AND error IN (
                       'DiscordChannelUnavailable',
                       'DiscordChannelHTTPError'
                     )""",
                (channel_id,),
            )

    def processing_inbox(self, *, channel_id: str) -> list[dict[str, object]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """SELECT event_id, content FROM inbox_messages
                   WHERE channel_id = ? AND status = 'processing'
                   ORDER BY created_at, event_id""",
                (channel_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def failed_inbox(self, *, limit: int = 100) -> list[dict[str, object]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """SELECT event_id, channel_id, author_id, content, attempts, error
                   FROM inbox_messages WHERE status = 'failed'
                   ORDER BY created_at, event_id LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def requeue_failed_inbox(self, event_id: str) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE inbox_messages
                   SET status = 'pending', attempts = 0, transport_attempts = 0,
                       provider_attempts = 0,
                       next_attempt_at = NULL, error = NULL
                   WHERE event_id = ? AND status = 'failed'""",
                (event_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError("failed inbox event not found")

    def claim_pending(self, *, channel_id: str, limit: int = 50) -> list[IncomingMessage]:
        """Atomically claim the oldest messages for one channel."""
        with self.transaction() as connection:
            candidates = connection.execute(
                """SELECT event_id,
                          CASE
                            WHEN next_attempt_at IS NULL
                              OR next_attempt_at <= CURRENT_TIMESTAMP
                            THEN 1 ELSE 0
                          END AS ready
                   FROM inbox_messages
                   WHERE channel_id = ? AND status = 'pending'
                   ORDER BY created_at, event_id LIMIT ?""",
                (channel_id, limit),
            ).fetchall()
            ids: list[str] = []
            for row in candidates:
                if not bool(row["ready"]):
                    break
                ids.append(str(row["event_id"]))
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"""UPDATE inbox_messages
                        SET routing_lifecycle = COALESCE(
                                routing_lifecycle,
                                (
                                    SELECT lifecycle FROM games
                                    WHERE games.game_id = inbox_messages.game_id
                                )
                            ),
                            status = ?, attempts = attempts + 1
                        WHERE event_id IN ({placeholders})""",
                    (InboxStatus.PROCESSING.value, *ids),
                )
                rows = connection.execute(
                    f"""SELECT event_id, channel_id, game_id, scene_id,
                               author_id, content, created_at, guild_id,
                               parent_channel_id, reply_to_event_id,
                               reply_to_author_id, reply_context, attachments_json,
                               routing_lifecycle
                        FROM inbox_messages
                        WHERE event_id IN ({placeholders})
                        ORDER BY created_at, event_id""",
                    ids,
                ).fetchall()
            else:
                rows = []
        return [self._incoming_message_from_row(row, has_routing_snapshot=True) for row in rows]

    def complete_batch(
        self,
        *,
        event_ids: list[str],
        channel_id: str,
        contents: list[str],
        idempotency_key: str,
        additional_deliveries: list[tuple[str, ...]] | None = None,
        payloads: list[tuple[object, ...]] | None = None,
        assistant_turns: list[tuple[str, str, str]] | None = None,
        completion_game_id: str | None = None,
    ) -> None:
        if not event_ids:
            raise ValueError("event_ids cannot be empty")
        if len(set(event_ids)) != len(event_ids):
            raise ValueError("event_ids must be unique")
        with self.transaction() as connection:
            completion_context_changed = False
            completion_current_game_id: str | None = None
            placeholders = ",".join("?" for _ in event_ids)
            source_rows = connection.execute(
                f"""SELECT event_id, channel_id, game_id, author_id, guild_id, status,
                            created_at, ingress_game_id
                     FROM inbox_messages WHERE event_id IN ({placeholders})""",
                event_ids,
            ).fetchall()
            sources = {str(row["event_id"]): row for row in source_rows}
            if len(sources) != len(event_ids):
                raise RuntimeError("batch completion references an unknown inbox message")
            if any(str(row["channel_id"]) != channel_id for row in source_rows):
                raise ValueError("batch completion cannot cross channels")
            if completion_game_id is not None:
                if any(row["game_id"] is not None for row in source_rows):
                    raise ValueError(
                        "completion game can only associate structurally unbound messages"
                    )
                binding = connection.execute(
                    "SELECT game_id FROM channel_bindings WHERE channel_id = ?",
                    (channel_id,),
                ).fetchone()
                completion_current_game_id = None if binding is None else str(binding["game_id"])
                completion_context_changed = completion_current_game_id != completion_game_id
                if not completion_context_changed:
                    for row in source_rows:
                        if row["ingress_game_id"] == "":
                            raise RuntimeError(
                                "legacy inbox row has no trustworthy routing snapshot"
                            )
                        scene_id, participants = self._scene_snapshot(
                            connection,
                            game_id=completion_game_id,
                            player_id=str(row["author_id"]),
                        )
                        connection.execute(
                            """UPDATE inbox_messages
                               SET game_id = ?, scene_id = ?, scene_participants_json = ?
                               WHERE event_id = ? AND game_id IS NULL
                                 AND (ingress_game_id IS NULL OR ingress_game_id <> '')
                                 AND status = 'processing'""",
                            (
                                completion_game_id,
                                scene_id,
                                participants,
                                row["event_id"],
                            ),
                        )
                    last_source = max(
                        source_rows,
                        key=lambda row: (str(row["created_at"]), str(row["event_id"])),
                    )
                    queued = connection.execute(
                        """SELECT event_id, author_id FROM inbox_messages
                           WHERE channel_id = ? AND game_id IS NULL AND status = 'pending'
                             AND (ingress_game_id IS NULL OR ingress_game_id <> '')
                             AND (
                               created_at > ?
                               OR (created_at = ? AND event_id > ?)
                             )
                           ORDER BY created_at, event_id""",
                        (
                            channel_id,
                            last_source["created_at"],
                            last_source["created_at"],
                            last_source["event_id"],
                        ),
                    ).fetchall()
                    for row in queued:
                        scene_id, participants = self._scene_snapshot(
                            connection,
                            game_id=completion_game_id,
                            player_id=str(row["author_id"]),
                        )
                        connection.execute(
                            """UPDATE inbox_messages
                               SET game_id = ?, scene_id = ?, scene_participants_json = ?
                               WHERE event_id = ? AND game_id IS NULL AND status = 'pending'""",
                            (
                                completion_game_id,
                                scene_id,
                                participants,
                                row["event_id"],
                            ),
                        )
                    source_rows = connection.execute(
                        f"""SELECT event_id, channel_id, game_id, author_id, guild_id, status,
                                   created_at, ingress_game_id
                            FROM inbox_messages WHERE event_id IN ({placeholders})""",
                        event_ids,
                    ).fetchall()
                    sources = {str(row["event_id"]): row for row in source_rows}
            source_games = {row["game_id"] for row in source_rows}
            if len(source_games) != 1:
                raise ValueError("batch completion cannot cross games")
            default_source = sources[event_ids[0]]
            source_game_id = default_source["game_id"]
            source_context_changed = False
            source_current_game_id: str | None = None
            if source_game_id is not None:
                source_binding = connection.execute(
                    "SELECT game_id FROM channel_bindings WHERE channel_id = ?",
                    (channel_id,),
                ).fetchone()
                source_current_game_id = (
                    None if source_binding is None else str(source_binding["game_id"])
                )
                source_context_changed = source_current_game_id != str(source_game_id)
                if source_context_changed and source_current_game_id is None:
                    own_detach = connection.execute(
                        """SELECT operation_type, game_id, channel_id
                           FROM event_operations WHERE event_id = ?""",
                        (str(default_source["event_id"]),),
                    ).fetchone()
                    source_context_changed = not (
                        own_detach is not None
                        and own_detach["operation_type"] in {"unbind_game", "new_session"}
                        and own_detach["game_id"] == source_game_id
                        and own_detach["channel_id"] == channel_id
                    )

            def insert_outbox(
                *,
                key: str,
                target_channel: str,
                content: str,
                embed: dict[str, object] | None = None,
                kind: str,
                source_event_id: str | None,
                source_author_id: str | None,
                source_guild_id: str | None,
            ) -> None:
                connection.execute(
                    """INSERT OR IGNORE INTO outbox_messages
                       (idempotency_key, channel_id, content, embed_json, kind,
                        source_event_id, source_author_id, source_guild_id,
                        discord_nonce)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        key,
                        target_channel,
                        content,
                        json.dumps(embed, ensure_ascii=False) if embed is not None else None,
                        kind,
                        source_event_id,
                        source_author_id,
                        source_guild_id,
                        _discord_nonce(key),
                    ),
                )

            def scoped_delivery_error(target_channel: str, kind: str) -> str | None:
                if kind not in {"narrative", "roleplay_reply", "player_narration"}:
                    return None
                source_registry = connection.execute(
                    "SELECT guild_id, kind FROM discord_channels WHERE channel_id = ?",
                    (default_source["channel_id"],),
                ).fetchone()
                source_guild = default_source["guild_id"]
                if source_guild is None and source_registry is not None:
                    source_guild = source_registry["guild_id"]
                target = connection.execute(
                    "SELECT guild_id, kind FROM discord_channels WHERE channel_id = ?",
                    (target_channel,),
                ).fetchone()
                if target is None:
                    return "target_unknown"
                if str(target["kind"]).casefold() not in _MESSAGEABLE_DISCORD_CHANNEL_KINDS:
                    return "target_not_messageable"
                if target["guild_id"] is None:
                    return "target_direct_message"
                if source_guild is None:
                    return "source_direct_message_or_unknown"
                if target["guild_id"] != source_guild:
                    return "target_cross_server"
                source_game_id = default_source["game_id"]
                if source_game_id is None:
                    return None
                game = connection.execute(
                    "SELECT narrative_channel_id FROM games WHERE game_id = ?",
                    (source_game_id,),
                ).fetchone()
                if game is None or game["narrative_channel_id"] != target_channel:
                    return "target_no_longer_narrative_channel"
                target_binding = connection.execute(
                    "SELECT game_id FROM channel_bindings WHERE channel_id = ?",
                    (target_channel,),
                ).fetchone()
                if target_binding is not None and target_binding["game_id"] != source_game_id:
                    return "target_bound_to_another_game"
                return None

            def game_locale(game_id: str | None) -> str:
                if game_id is None:
                    return "ru"
                game = connection.execute(
                    "SELECT locale FROM games WHERE game_id = ?",
                    (game_id,),
                ).fetchone()
                return "ru" if game is None else str(game["locale"])

            cursor = connection.execute(
                f"""UPDATE inbox_messages
                    SET status = ?, error = NULL, next_attempt_at = NULL
                    WHERE event_id IN ({placeholders}) AND status = ?""",
                (InboxStatus.PROCESSED.value, *event_ids, InboxStatus.PROCESSING.value),
            )
            if cursor.rowcount != len(event_ids):
                raise RuntimeError("batch completion lost ownership of inbox messages")
            if completion_context_changed:
                from masterclaw.app.i18n import tr

                notice_key = f"{idempotency_key}:completion-context-changed"
                insert_outbox(
                    key=notice_key,
                    target_channel=channel_id,
                    content=tr(
                        game_locale(completion_game_id),
                        "completion_context_changed",
                    ),
                    kind="system_notice",
                    source_event_id=str(default_source["event_id"]),
                    source_author_id=str(default_source["author_id"]),
                    source_guild_id=default_source["guild_id"],
                )
                connection.execute(
                    """INSERT OR IGNORE INTO domain_events
                       (game_id, event_type, payload_json, causation_id)
                       VALUES (?, 'completion_context_changed', ?, ?)""",
                    (
                        completion_game_id,
                        json.dumps(
                            {
                                "channel_id": channel_id,
                                "completion_game_id": completion_game_id,
                                "current_game_id": completion_current_game_id,
                                "source_event_id": str(default_source["event_id"]),
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        f"completion-context-changed:{default_source['event_id']}",
                    ),
                )
                return
            if source_context_changed:
                from masterclaw.app.i18n import tr

                for row in source_rows:
                    self._cancel_source_pending_if_origin_unbound(
                        connection,
                        game_id=str(row["game_id"]),
                        player_id=str(row["author_id"]),
                        origin_channel_id=channel_id,
                        source_event_id=str(row["event_id"]),
                        queue_notice=False,
                    )
                notice_content = tr(
                    game_locale(str(source_game_id)),
                    "source_game_context_changed",
                )
                notice_key = f"{idempotency_key}:source-context-changed"
                existing_notice = connection.execute(
                    """SELECT id FROM outbox_messages
                       WHERE channel_id = ? AND source_event_id = ?
                         AND kind = 'system_notice' AND delivered_at IS NULL
                         AND delivery_claim IS NULL AND attempts < 5
                       ORDER BY CASE WHEN idempotency_key = ? THEN 0 ELSE 1 END, id
                       LIMIT 1""",
                    (
                        channel_id,
                        str(default_source["event_id"]),
                        notice_key,
                    ),
                ).fetchone()
                if existing_notice is None:
                    insert_outbox(
                        key=notice_key,
                        target_channel=channel_id,
                        content=notice_content,
                        kind="system_notice",
                        source_event_id=str(default_source["event_id"]),
                        source_author_id=str(default_source["author_id"]),
                        source_guild_id=default_source["guild_id"],
                    )
                else:
                    connection.execute(
                        "UPDATE outbox_messages SET content = ? WHERE id = ?",
                        (notice_content, existing_notice["id"]),
                    )
                    connection.execute(
                        """UPDATE outbox_messages
                           SET attempts = 5, error = 'SourceGameContextChanged',
                               next_attempt_at = NULL
                           WHERE channel_id = ? AND source_event_id = ?
                             AND kind = 'system_notice' AND delivered_at IS NULL
                             AND delivery_claim IS NULL AND attempts < 5 AND id != ?""",
                        (
                            channel_id,
                            str(default_source["event_id"]),
                            existing_notice["id"],
                        ),
                    )
                connection.execute(
                    """INSERT OR IGNORE INTO domain_events
                       (game_id, event_type, payload_json, causation_id)
                       VALUES (?, 'source_game_context_changed', ?, ?)""",
                    (
                        source_game_id,
                        json.dumps(
                            {
                                "channel_id": channel_id,
                                "current_game_id": source_current_game_id,
                                "source_event_id": str(default_source["event_id"]),
                                "source_game_id": str(source_game_id),
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        f"source-context-changed:{default_source['event_id']}",
                    ),
                )
                return
            for index, chunk in enumerate(contents):
                if chunk.strip():
                    key = f"{idempotency_key}:{index}"
                    insert_outbox(
                        key=key,
                        target_channel=channel_id,
                        content=chunk,
                        kind="response",
                        source_event_id=default_source["event_id"],
                        source_author_id=default_source["author_id"],
                        source_guild_id=default_source["guild_id"],
                    )
            for index, payload in enumerate(payloads or []):
                if len(payload) not in {2, 4}:
                    raise ValueError("response payload must contain 2 or 4 values")
                content = str(payload[0])
                embed = payload[1]
                if embed is not None and not isinstance(embed, dict):
                    raise ValueError("response embed must be a mapping")
                source_event_id = str(payload[2]) if len(payload) == 4 and payload[2] else None
                source_author_id = str(payload[3]) if len(payload) == 4 and payload[3] else None
                if source_event_id is not None and source_event_id not in sources:
                    raise ValueError("response payload must reference this inbox batch")
                source = sources.get(source_event_id or "", default_source)
                if content.strip() or embed is not None:
                    key = f"{idempotency_key}:payload:{index}"
                    insert_outbox(
                        key=key,
                        target_channel=channel_id,
                        content=content,
                        embed=embed,
                        kind="response",
                        source_event_id=str(source["event_id"]),
                        source_author_id=source_author_id or str(source["author_id"]),
                        source_guild_id=source["guild_id"],
                    )
            for delivery in additional_deliveries or []:
                if len(delivery) not in {3, 4}:
                    raise ValueError("additional delivery must contain 3 or 4 values")
                target_channel, content, suffix = map(str, delivery[:3])
                kind = str(delivery[3]) if len(delivery) == 4 else "message"
                if not content.strip():
                    continue
                delivery_error = scoped_delivery_error(target_channel, kind)
                if delivery_error is not None:
                    connection.execute(
                        """INSERT OR IGNORE INTO domain_events
                           (game_id, event_type, payload_json, causation_id)
                           VALUES (?, 'prose_delivery_skipped', ?, ?)""",
                        (
                            default_source["game_id"],
                            json.dumps(
                                {
                                    "kind": kind,
                                    "reason": delivery_error,
                                    "source_channel_id": channel_id,
                                    "source_event_id": str(default_source["event_id"]),
                                    "target_channel_id": target_channel,
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                            (f"prose-delivery-skipped:{default_source['event_id']}:{suffix}"),
                        ),
                    )
                    from masterclaw.app.i18n import tr

                    insert_outbox(
                        key=f"{idempotency_key}:prose-delivery-skipped",
                        target_channel=channel_id,
                        content=tr(
                            game_locale(default_source["game_id"]),
                            "prose_delivery_skipped",
                        ),
                        kind="system_notice",
                        source_event_id=str(default_source["event_id"]),
                        source_author_id=str(default_source["author_id"]),
                        source_guild_id=default_source["guild_id"],
                    )
                    continue
                key = f"{idempotency_key}:delivery:{suffix}"
                insert_outbox(
                    key=key,
                    target_channel=target_channel,
                    content=content,
                    kind=kind,
                    source_event_id=str(default_source["event_id"]),
                    source_author_id=str(default_source["author_id"]),
                    source_guild_id=default_source["guild_id"],
                )
            ordinals: dict[str, int] = {}
            for source_event_id, recipient_author_id, content in assistant_turns or []:
                if source_event_id not in sources:
                    raise ValueError("assistant turn must reference this inbox batch")
                if not content.strip():
                    continue
                ordinal = ordinals.get(source_event_id, 0)
                ordinals[source_event_id] = ordinal + 1
                connection.execute(
                    """INSERT OR IGNORE INTO assistant_responses
                       (source_event_id, channel_id, recipient_author_id, content, ordinal,
                        created_at)
                       SELECT event_id, channel_id, ?, ?, ?, created_at
                       FROM inbox_messages WHERE event_id = ?""",
                    (
                        recipient_author_id,
                        content.strip(),
                        ordinal,
                        source_event_id,
                    ),
                )
            for row in source_rows:
                if row["game_id"] is None:
                    continue
                cancelled = self._cancel_source_pending_if_origin_unbound(
                    connection,
                    game_id=str(row["game_id"]),
                    player_id=str(row["author_id"]),
                    origin_channel_id=channel_id,
                    source_event_id=str(row["event_id"]),
                )
                if cancelled:
                    connection.execute(
                        """DELETE FROM assistant_responses
                           WHERE source_event_id = ?""",
                        (row["event_id"],),
                    )

    def fail_batch(
        self,
        *,
        event_ids: list[str],
        error: str,
        retry: bool = True,
        provider_transient: bool = False,
    ) -> bool:
        if not event_ids:
            return False
        if provider_transient and not retry:
            raise ValueError("provider_transient requires retry")
        with self.transaction() as connection:
            placeholders = ",".join("?" for _ in event_ids)
            if provider_transient:
                rows = connection.execute(
                    f"""SELECT event_id, provider_attempts
                        FROM inbox_messages
                        WHERE event_id IN ({placeholders})""",
                    event_ids,
                ).fetchall()
                for row in rows:
                    provider_attempts = int(row["provider_attempts"]) + 1
                    if provider_attempts >= INBOX_PROVIDER_MAX_ATTEMPTS:
                        connection.execute(
                            """UPDATE inbox_messages
                               SET status = 'failed', provider_attempts = ?,
                                   next_attempt_at = NULL, error = ?
                               WHERE event_id = ?""",
                            (
                                provider_attempts,
                                error[:2000],
                                row["event_id"],
                            ),
                        )
                        continue
                    delay = INBOX_PROVIDER_RETRY_DELAYS_SECONDS[provider_attempts - 1]
                    connection.execute(
                        """UPDATE inbox_messages
                           SET status = 'pending', provider_attempts = ?,
                               next_attempt_at = datetime('now', ?), error = ?
                           WHERE event_id = ?""",
                        (
                            provider_attempts,
                            f"+{delay} seconds",
                            error[:2000],
                            row["event_id"],
                        ),
                    )
            elif retry:
                connection.execute(
                    f"""UPDATE inbox_messages
                        SET status = CASE WHEN attempts >= 3 THEN 'failed' ELSE 'pending' END,
                            next_attempt_at = NULL, error = ?
                        WHERE event_id IN ({placeholders})""",
                    (error[:2000], *event_ids),
                )
            else:
                connection.execute(
                    f"""UPDATE inbox_messages
                        SET status = 'failed', next_attempt_at = NULL, error = ?
                        WHERE event_id IN ({placeholders})""",
                    (error[:2000], *event_ids),
                )
            rows = connection.execute(
                f"SELECT status FROM inbox_messages WHERE event_id IN ({placeholders})",
                event_ids,
            ).fetchall()
            return bool(rows) and all(row["status"] == InboxStatus.FAILED.value for row in rows)

    def queue_system_notice(
        self,
        *,
        channel_id: str,
        key: str,
        content: str,
        source_event_id: str | None = None,
        source_author_id: str | None = None,
        source_guild_id: str | None = None,
    ) -> None:
        if not content.strip():
            raise ValueError("system notice cannot be empty")
        idempotency_key = f"system-notice:{key}"
        with self.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO outbox_messages
                   (idempotency_key, channel_id, content, kind, source_event_id,
                    source_author_id, source_guild_id, discord_nonce)
                   VALUES (?, ?, ?, 'system_notice', ?, ?, ?, ?)""",
                (
                    idempotency_key,
                    channel_id,
                    content.strip(),
                    source_event_id,
                    source_author_id,
                    source_guild_id,
                    _discord_nonce(idempotency_key),
                ),
            )

    def recover_interrupted_work(self) -> int:
        """Single-worker startup recovery for messages claimed before a crash."""
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE inbox_messages SET status = 'pending',
                   error = 'recovered after worker restart'
                   WHERE status = 'processing'"""
            )
            return cursor.rowcount

    def put_pending(self, pending: PendingInteraction) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO pending_interactions
                   (interaction_id, game_id, player_id, origin_channel_id, scene_id, kind, prompt,
                    payload_json, status, revision)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pending.interaction_id,
                    pending.game_id,
                    pending.player_id,
                    pending.origin_channel_id,
                    pending.scene_id,
                    pending.kind.value,
                    pending.prompt,
                    json.dumps(pending.payload, ensure_ascii=False),
                    pending.status.value,
                    pending.revision,
                ),
            )

    def open_pending(
        self,
        *,
        game_id: str,
        player_id: str,
        channel_id: str | None = None,
    ) -> PendingInteraction | None:
        with closing(self.connect()) as connection:
            if channel_id is None:
                row = connection.execute(
                    """SELECT * FROM pending_interactions
                       WHERE game_id = ? AND player_id = ? AND status = 'open'
                       ORDER BY created_at DESC LIMIT 1""",
                    (game_id, player_id),
                ).fetchone()
            else:
                row = connection.execute(
                    """SELECT * FROM pending_interactions
                       WHERE game_id = ? AND player_id = ? AND status = 'open'
                         AND (origin_channel_id IS NULL OR origin_channel_id = ?)
                       ORDER BY created_at DESC LIMIT 1""",
                    (game_id, player_id, channel_id),
                ).fetchone()
        if row is None:
            return None
        created_at = datetime.fromisoformat(row["created_at"])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return PendingInteraction(
            interaction_id=row["interaction_id"],
            game_id=row["game_id"],
            player_id=row["player_id"],
            scene_id=row["scene_id"],
            kind=PendingKind(row["kind"]),
            prompt=row["prompt"],
            payload=json.loads(row["payload_json"]),
            status=PendingStatus(row["status"]),
            revision=row["revision"],
            created_at=created_at,
            origin_channel_id=row["origin_channel_id"],
        )

    def pending_by_id(self, interaction_id: str) -> PendingInteraction | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM pending_interactions WHERE interaction_id = ?",
                (interaction_id,),
            ).fetchone()
        if row is None:
            return None
        created_at = datetime.fromisoformat(row["created_at"])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return PendingInteraction(
            interaction_id=row["interaction_id"],
            game_id=row["game_id"],
            player_id=row["player_id"],
            scene_id=row["scene_id"],
            kind=PendingKind(row["kind"]),
            prompt=row["prompt"],
            payload=json.loads(row["payload_json"]),
            status=PendingStatus(row["status"]),
            revision=row["revision"],
            created_at=created_at,
            origin_channel_id=row["origin_channel_id"],
        )

    def closed_pending_for_event(
        self,
        *,
        game_id: str,
        player_id: str,
        event_id: str,
    ) -> PendingInteraction | None:
        """Return the terminal pending step durably handled by one inbox event.

        The marker deliberately lives in ``payload_json``: it is domain replay data rather
        than a new relation, so old databases can adopt the contract without a schema
        migration.  More than one match would make replay ambiguous and is treated as
        corruption instead of silently choosing a result.
        """

        with closing(self.connect()) as connection:
            rows = connection.execute(
                """SELECT interaction_id, payload_json
                   FROM pending_interactions
                   WHERE game_id = ? AND player_id = ?
                     AND status IN ('resolved', 'cancelled', 'expired')
                   ORDER BY updated_at DESC, interaction_id DESC""",
                (game_id, player_id),
            ).fetchall()
        matching_ids = [
            str(row["interaction_id"])
            for row in rows
            if json.loads(row["payload_json"]).get("closed_by_event_id") == event_id
        ]
        if not matching_ids:
            return None
        if len(matching_ids) != 1:
            raise RuntimeError("multiple pending interactions were closed by one event")
        pending = self.pending_by_id(matching_ids[0])
        assert pending is not None
        return pending

    def cancel_pending(
        self,
        *,
        interaction_id: str,
        player_id: str,
        expected_revision: int,
        closed_by_event_id: str | None = None,
    ) -> None:
        self._close_pending(
            interaction_id=interaction_id,
            player_id=player_id,
            expected_revision=expected_revision,
            status=PendingStatus.CANCELLED,
            closed_by_event_id=closed_by_event_id,
        )

    def expire_pending(
        self,
        *,
        interaction_id: str,
        player_id: str,
        expected_revision: int,
        closed_by_event_id: str | None = None,
    ) -> None:
        self._close_pending(
            interaction_id=interaction_id,
            player_id=player_id,
            expected_revision=expected_revision,
            status=PendingStatus.EXPIRED,
            closed_by_event_id=closed_by_event_id,
        )

    def _close_pending(
        self,
        *,
        interaction_id: str,
        player_id: str,
        expected_revision: int,
        status: PendingStatus,
        closed_by_event_id: str | None = None,
    ) -> None:
        if status not in {PendingStatus.CANCELLED, PendingStatus.EXPIRED}:
            raise ValueError(f"unsupported pending close status: {status.value}")
        with self.transaction() as connection:
            pending = connection.execute(
                """SELECT kind, payload_json FROM pending_interactions
                   WHERE interaction_id = ? AND player_id = ?
                     AND revision = ? AND status = 'open'""",
                (interaction_id, player_id, expected_revision),
            ).fetchone()
            if pending is None:
                raise RuntimeError("pending interaction revision conflict")
            payload = json.loads(pending["payload_json"])
            if closed_by_event_id is not None:
                payload["closed_by_event_id"] = closed_by_event_id
            cursor = connection.execute(
                """UPDATE pending_interactions
                   SET status = ?, payload_json = ?, revision = revision + 1,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE interaction_id = ? AND player_id = ?
                     AND revision = ? AND status = 'open'""",
                (
                    status.value,
                    json.dumps(payload, ensure_ascii=False),
                    interaction_id,
                    player_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("pending interaction revision conflict")
            if PendingKind(pending["kind"]) is PendingKind.POOL_CONFIRMATION:
                self._refund_roll_helpers(connection, interaction_id=interaction_id)

    @staticmethod
    def _refund_roll_helpers(connection: sqlite3.Connection, *, interaction_id: str) -> None:
        helpers = connection.execute(
            """SELECT helper_character_id FROM roll_helpers
               WHERE interaction_id = ?""",
            (interaction_id,),
        ).fetchall()
        for helper in helpers:
            helper_row = connection.execute(
                "SELECT sheet_json FROM characters WHERE character_id = ?",
                (helper["helper_character_id"],),
            ).fetchone()
            if helper_row is None:
                raise RuntimeError("roll helper character is missing")
            helper_sheet = json.loads(helper_row["sheet_json"])
            helper_sheet["reserve_current"] = min(
                helper_sheet["reserve_maximum"],
                helper_sheet["reserve_current"] + 1,
            )
            connection.execute(
                """UPDATE characters
                   SET sheet_json = ?, revision = revision + 1
                   WHERE character_id = ?""",
                (
                    json.dumps(helper_sheet, ensure_ascii=False),
                    helper["helper_character_id"],
                ),
            )

    def resolve_pending(
        self,
        *,
        interaction_id: str,
        player_id: str,
        expected_revision: int,
        answer: str | None = None,
        closed_by_event_id: str | None = None,
    ) -> None:
        with self.transaction() as connection:
            payload_json = None
            if answer is not None or closed_by_event_id is not None:
                row = connection.execute(
                    """SELECT payload_json FROM pending_interactions
                       WHERE interaction_id = ? AND player_id = ?
                         AND revision = ? AND status = 'open'""",
                    (interaction_id, player_id, expected_revision),
                ).fetchone()
                if row is None:
                    raise RuntimeError("pending interaction revision conflict")
                payload = json.loads(row["payload_json"])
                if answer is not None:
                    payload["answer"] = answer
                if closed_by_event_id is not None:
                    payload["closed_by_event_id"] = closed_by_event_id
                payload_json = json.dumps(payload, ensure_ascii=False)
            cursor = connection.execute(
                """UPDATE pending_interactions
                   SET status = 'resolved',
                       payload_json = COALESCE(?, payload_json),
                       revision = revision + 1,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE interaction_id = ? AND player_id = ?
                     AND revision = ? AND status = 'open'""",
                (payload_json, interaction_id, player_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("pending interaction revision conflict")

    def revise_pending(
        self,
        *,
        interaction_id: str,
        player_id: str,
        expected_revision: int,
        kind: PendingKind,
        prompt: str,
        payload: dict[str, object],
        origin_channel_id: str | None = None,
    ) -> None:
        """Replace the question represented by an open non-roll pending interaction.

        A revised clarification starts a fresh TTL window but keeps the stable interaction id,
        which lets retries detect that the continuation is still unresolved.
        """
        if kind in {PendingKind.POOL_CONFIRMATION, PendingKind.PLAYER_NARRATION}:
            raise ValueError("roll and narration pending interactions cannot be revised")
        if not prompt.strip():
            raise ValueError("pending prompt cannot be empty")
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE pending_interactions
                   SET kind = ?, prompt = ?, payload_json = ?,
                       origin_channel_id = COALESCE(?, origin_channel_id),
                       revision = revision + 1,
                       created_at = CURRENT_TIMESTAMP,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE interaction_id = ? AND player_id = ?
                     AND revision = ? AND status = 'open'
                     AND kind IN ('choice', 'clarification')""",
                (
                    kind.value,
                    prompt.strip(),
                    json.dumps(payload, ensure_ascii=False),
                    origin_channel_id,
                    interaction_id,
                    player_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("pending interaction revision conflict")

    def replace_pending(
        self,
        *,
        current_interaction_id: str,
        player_id: str,
        expected_revision: int,
        replacement: PendingInteraction,
        answer: str | None = None,
        closed_by_event_id: str | None = None,
    ) -> None:
        """Atomically resolve a continuation and install its next pending step."""
        if replacement.player_id != player_id:
            raise ValueError("replacement pending belongs to another player")
        with self.transaction() as connection:
            row = connection.execute(
                """SELECT game_id, kind, payload_json FROM pending_interactions
                   WHERE interaction_id = ? AND player_id = ?
                     AND revision = ? AND status = 'open'""",
                (current_interaction_id, player_id, expected_revision),
            ).fetchone()
            if row is None:
                raise RuntimeError("pending interaction revision conflict")
            if row["game_id"] != replacement.game_id:
                raise ValueError("replacement pending belongs to another game")
            if PendingKind(row["kind"]) not in {PendingKind.CHOICE, PendingKind.CLARIFICATION}:
                raise ValueError("only a choice or clarification can be replaced")
            payload = json.loads(row["payload_json"])
            if answer is not None:
                payload["answer"] = answer
            if closed_by_event_id is not None:
                payload["closed_by_event_id"] = closed_by_event_id
            cursor = connection.execute(
                """UPDATE pending_interactions
                   SET status = 'resolved', payload_json = ?,
                       revision = revision + 1,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE interaction_id = ? AND player_id = ?
                     AND revision = ? AND status = 'open'""",
                (
                    json.dumps(payload, ensure_ascii=False),
                    current_interaction_id,
                    player_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("pending interaction revision conflict")
            connection.execute(
                """INSERT INTO pending_interactions
                   (interaction_id, game_id, player_id, origin_channel_id, scene_id, kind, prompt,
                    payload_json, status, revision)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    replacement.interaction_id,
                    replacement.game_id,
                    replacement.player_id,
                    replacement.origin_channel_id,
                    replacement.scene_id,
                    replacement.kind.value,
                    replacement.prompt,
                    json.dumps(replacement.payload, ensure_ascii=False),
                    replacement.status.value,
                    replacement.revision,
                ),
            )

    def pending_outbox(
        self, *, channel_id: str | None = None, limit: int = 50
    ) -> list[sqlite3.Row]:
        with closing(self.connect()) as connection:
            if channel_id is None:
                return connection.execute(
                    """SELECT id, idempotency_key, channel_id, content, embed_json,
                              kind, source_event_id, source_author_id, source_guild_id,
                              discord_nonce, discord_message_id, delivery_claim,
                              attempts, next_attempt_at
                       FROM outbox_messages
                       WHERE delivered_at IS NULL AND attempts < 5
                       ORDER BY id LIMIT ?""",
                    (limit,),
                ).fetchall()
            return connection.execute(
                """SELECT id, idempotency_key, channel_id, content, embed_json,
                          kind, source_event_id, source_author_id, source_guild_id,
                          discord_nonce, discord_message_id, delivery_claim,
                          attempts, next_attempt_at
                   FROM outbox_messages
                   WHERE delivered_at IS NULL AND attempts < 5 AND channel_id = ?
                   ORDER BY id LIMIT ?""",
                (channel_id, limit),
            ).fetchall()

    def claim_outbox(self, *, limit: int = 50) -> list[sqlite3.Row]:
        """Atomically reserve deliveries so concurrent daemons cannot send duplicates."""
        claim = uuid.uuid4().hex
        with self.transaction() as connection:
            rows = connection.execute(
                """SELECT candidate.id
                   FROM outbox_messages AS candidate
                   WHERE candidate.delivered_at IS NULL AND candidate.attempts < 5
                     AND (
                       candidate.next_attempt_at IS NULL
                       OR candidate.next_attempt_at <= CURRENT_TIMESTAMP
                     )
                     AND (
                       candidate.delivery_claim IS NULL
                       OR candidate.claimed_at < datetime('now', '-2 minutes')
                     )
                     AND NOT EXISTS (
                       SELECT 1 FROM outbox_messages AS older
                       WHERE older.channel_id = candidate.channel_id
                         AND older.id < candidate.id
                         AND older.delivered_at IS NULL
                         AND older.attempts < 5
                     )
                   ORDER BY candidate.id LIMIT ?""",
                (limit,),
            ).fetchall()
            ids = [row["id"] for row in rows]
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            connection.execute(
                f"""UPDATE outbox_messages
                    SET delivery_claim = ?, claimed_at = CURRENT_TIMESTAMP
                    WHERE id IN ({placeholders})""",
                (claim, *ids),
            )
            return connection.execute(
                """SELECT id, idempotency_key, channel_id, content, embed_json,
                          kind, source_event_id, source_author_id, source_guild_id,
                          discord_nonce, discord_message_id, delivery_claim,
                          attempts, next_attempt_at
                   FROM outbox_messages WHERE delivery_claim = ? ORDER BY id""",
                (claim,),
            ).fetchall()

    def release_outbox_claims(
        self,
        outbox_ids: list[int],
        *,
        delivery_claim: str | None = None,
    ) -> int:
        if not outbox_ids:
            return 0
        placeholders = ",".join("?" for _ in outbox_ids)
        with self.transaction() as connection:
            rows = connection.execute(
                f"""SELECT id, delivery_claim FROM outbox_messages
                    WHERE id IN ({placeholders}) AND delivered_at IS NULL""",
                tuple(outbox_ids),
            ).fetchall()
            for row in rows:
                if row["delivery_claim"] != delivery_claim:
                    raise RuntimeError("outbox delivery claim lost")
            if delivery_claim is None:
                # Releasing an unclaimed direct/setup row has no useful state change.
                return 0
            cursor = connection.execute(
                f"""UPDATE outbox_messages
                    SET delivery_claim = NULL, claimed_at = NULL
                    WHERE id IN ({placeholders}) AND delivered_at IS NULL
                      AND delivery_claim = ?""",
                (*outbox_ids, delivery_claim),
            )
            return cursor.rowcount

    def failed_outbox(self, *, limit: int = 100) -> list[dict[str, object]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """SELECT id, idempotency_key, channel_id, content, attempts, error
                   FROM outbox_messages
                   WHERE delivered_at IS NULL AND attempts >= 5
                   ORDER BY id LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def requeue_failed_outbox(self, outbox_id: int) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE outbox_messages
                   SET attempts = 0, error = NULL,
                       delivery_claim = NULL, claimed_at = NULL, next_attempt_at = NULL
                   WHERE id = ? AND delivered_at IS NULL AND attempts >= 5""",
                (outbox_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError("failed outbox message not found")

    def roll_for_interaction(self, interaction_id: str) -> RollRecord | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM rolls WHERE interaction_id = ?", (interaction_id,)
            ).fetchone()
        if row is None:
            return None
        from masterclaw.domain.mechanics import NarratorRights

        return RollRecord(
            roll_id=row["roll_id"],
            interaction_id=row["interaction_id"],
            confirmation_event_id=row["confirmation_event_id"],
            game_id=row["game_id"],
            character_id=row["character_id"],
            pool_size=row["pool_size"],
            difficulty=row["difficulty"],
            reserve_spent=row["reserve_spent"],
            help_dice=row["help_dice"],
            dice=tuple(json.loads(row["dice_json"])),
            hits=row["hits"],
            narrator_rights=NarratorRights(row["narrator_rights"]),
            reserve_after=row["reserve_after"],
        )

    def offer_help(
        self,
        *,
        game_id: str,
        helper_player_id: str,
        target_player_id: str,
        event_id: str | None = None,
        channel_id: str | None = None,
    ) -> PendingInteraction:
        """Attach one help die, with an optional inbox-scoped atomic replay receipt."""

        if (event_id is None) != (channel_id is None):
            raise ValueError("offer-help event id and channel id must be supplied together")
        if event_id is not None and (
            not event_id.strip() or not channel_id or not channel_id.strip()
        ):
            raise ValueError("offer-help event id and channel id cannot be empty")
        if helper_player_id == target_player_id:
            raise ValueError("a player cannot help their own roll")
        operation_input = {
            "game_id": game_id,
            "helper_player_id": helper_player_id,
            "target_player_id": target_player_id,
        }
        with self.transaction() as connection:
            if event_id is not None:
                prior = self._matching_event_operation(
                    connection,
                    event_id=event_id,
                    operation_type="offer_help",
                    game_id=game_id,
                    channel_id=channel_id,
                    input_payload=operation_input,
                )
                if prior is not None:
                    replayed = self._pending_from_help_operation_result(prior)
                    if (
                        replayed.game_id != game_id
                        or prior.get("helper_player_id") != helper_player_id
                        or prior.get("target_player_id") != target_player_id
                    ):
                        raise RuntimeError("stored offer-help operation result is invalid")
                    return replayed
            pending_row = connection.execute(
                """SELECT * FROM pending_interactions
                   WHERE game_id = ? AND player_id = ? AND status = 'open'
                   ORDER BY created_at DESC LIMIT 1""",
                (game_id, target_player_id),
            ).fetchone()
            if pending_row is None or pending_row["kind"] != PendingKind.POOL_CONFIRMATION.value:
                raise ValueError("target player has no pool awaiting confirmation")
            created_at = datetime.fromisoformat(str(pending_row["created_at"]))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            pending = PendingInteraction(
                interaction_id=str(pending_row["interaction_id"]),
                game_id=str(pending_row["game_id"]),
                player_id=str(pending_row["player_id"]),
                scene_id=(
                    None if pending_row["scene_id"] is None else str(pending_row["scene_id"])
                ),
                kind=PendingKind(str(pending_row["kind"])),
                prompt=str(pending_row["prompt"]),
                payload=json.loads(pending_row["payload_json"]),
                status=PendingStatus(str(pending_row["status"])),
                revision=int(pending_row["revision"]),
                created_at=created_at,
                origin_channel_id=(
                    None
                    if pending_row["origin_channel_id"] is None
                    else str(pending_row["origin_channel_id"])
                ),
            )
            helper_location = connection.execute(
                """SELECT scene_id FROM player_locations
                   WHERE game_id = ? AND player_id = ?""",
                (game_id, helper_player_id),
            ).fetchone()
            if helper_location is None or helper_location["scene_id"] != pending.scene_id:
                raise ValueError("helper must be in the same scene")
            character = connection.execute(
                """SELECT character_id, sheet_json,
                          (SELECT locale FROM games WHERE game_id = ?) AS locale
                   FROM characters WHERE game_id = ? AND player_id = ?""",
                (game_id, game_id, helper_player_id),
            ).fetchone()
            if character is None:
                raise ValueError("helper has no character")
            already_recorded = connection.execute(
                """SELECT 1 FROM roll_helpers
                   WHERE interaction_id = ? AND helper_player_id = ?""",
                (pending.interaction_id, helper_player_id),
            ).fetchone()
            if already_recorded is not None:
                if event_id is not None:
                    # A successful durable event writes its helper row and marker atomically.
                    # No marker means this is a distinct event, not a crash replay.
                    raise ValueError("this character already helps the roll")
                # Preserve the legacy direct-service idempotency contract for callers without
                # durable inbox identity.
                return pending
            sheet = json.loads(character["sheet_json"])
            if int(sheet["reserve_current"]) < 1:
                raise ValueError("helper has no reserve die")
            try:
                connection.execute(
                    """INSERT INTO roll_helpers
                       (interaction_id, helper_character_id, helper_player_id)
                       VALUES (?, ?, ?)""",
                    (pending.interaction_id, character["character_id"], helper_player_id),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("this character already helps the roll") from error
            sheet["reserve_current"] -= 1
            connection.execute(
                """UPDATE characters SET sheet_json = ?, revision = revision + 1
                   WHERE character_id = ?""",
                (json.dumps(sheet, ensure_ascii=False), character["character_id"]),
            )
            if event_id is not None:
                result = self._help_operation_result(
                    pending,
                    helper_player_id=helper_player_id,
                    target_player_id=target_player_id,
                    locale=str(character["locale"]),
                )
                self._insert_event_operation(
                    connection,
                    event_id=event_id,
                    operation_type="offer_help",
                    game_id=game_id,
                    channel_id=channel_id,
                    input_payload=operation_input,
                    result=result,
                )
        return pending

    def help_count(self, interaction_id: str) -> int:
        with closing(self.connect()) as connection:
            return int(
                connection.execute(
                    """SELECT COUNT(*) AS count FROM roll_helpers
                       WHERE interaction_id = ?""",
                    (interaction_id,),
                ).fetchone()["count"]
            )

    def roll_for_confirmation_event(self, event_id: str) -> RollRecord | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT interaction_id FROM rolls WHERE confirmation_event_id = ?",
                (event_id,),
            ).fetchone()
        return None if row is None else self.roll_for_interaction(row["interaction_id"])

    def roll_by_id(self, roll_id: str) -> RollRecord | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT interaction_id FROM rolls WHERE roll_id = ?", (roll_id,)
            ).fetchone()
        return None if row is None else self.roll_for_interaction(row["interaction_id"])

    def commit_roll(
        self,
        *,
        record: RollRecord,
        character_revision: int,
        pending_revision: int,
        narration_interaction_id: str | None = None,
        narration_prompt: str = "Describe the outcome within your narrator rights.",
        consumed_bonus_ids: tuple[str, ...] = (),
    ) -> None:
        with self.transaction() as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM rolls WHERE interaction_id = ?",
                    (record.interaction_id,),
                ).fetchone()
                is not None
            ):
                return
            pending = connection.execute(
                """SELECT status, revision, player_id, origin_channel_id, scene_id, game_id,
                          payload_json
                   FROM pending_interactions
                   WHERE interaction_id = ?""",
                (record.interaction_id,),
            ).fetchone()
            if pending is None or pending["status"] != PendingStatus.OPEN.value:
                raise RuntimeError("pending interaction is not open")
            if pending["revision"] != pending_revision:
                raise RuntimeError("pending interaction revision conflict")
            character = connection.execute(
                "SELECT sheet_json, revision FROM characters WHERE character_id = ?",
                (record.character_id,),
            ).fetchone()
            if character is None or character["revision"] != character_revision:
                raise RuntimeError("character revision conflict")
            sheet = json.loads(character["sheet_json"])
            sheet["reserve_current"] = record.reserve_after
            bonuses = list(sheet.get("temporary_bonuses", []))
            available_bonus_ids = {str(item["bonus_id"]) for item in bonuses}
            missing_bonus_ids = set(consumed_bonus_ids) - available_bonus_ids
            if missing_bonus_ids:
                raise RuntimeError(f"temporary bonus no longer exists: {sorted(missing_bonus_ids)}")
            consumed = set(consumed_bonus_ids)
            sheet["temporary_bonuses"] = [
                item for item in bonuses if str(item["bonus_id"]) not in consumed
            ]
            character_update = connection.execute(
                """UPDATE characters SET sheet_json = ?, revision = revision + 1
                   WHERE character_id = ? AND revision = ?""",
                (json.dumps(sheet, ensure_ascii=False), record.character_id, character_revision),
            )
            pending_update = connection.execute(
                """UPDATE pending_interactions
                   SET status = 'resolved', revision = revision + 1,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE interaction_id = ? AND revision = ? AND status = 'open'""",
                (record.interaction_id, pending_revision),
            )
            if character_update.rowcount != 1 or pending_update.rowcount != 1:
                raise RuntimeError("roll commit lost aggregate ownership")
            pending_payload = json.loads(pending["payload_json"])
            pending_payload["closed_by_event_id"] = record.confirmation_event_id
            connection.execute(
                """UPDATE pending_interactions SET payload_json = ?
                   WHERE interaction_id = ?""",
                (
                    json.dumps(pending_payload, ensure_ascii=False),
                    record.interaction_id,
                ),
            )
            connection.execute(
                """INSERT INTO rolls
                   (roll_id, interaction_id, confirmation_event_id, game_id,
                    character_id, pool_size, reserve_spent, help_dice,
                    difficulty, dice_json, hits, narrator_rights,
                    reserve_after)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.roll_id,
                    record.interaction_id,
                    record.confirmation_event_id,
                    record.game_id,
                    record.character_id,
                    record.pool_size,
                    record.reserve_spent,
                    record.help_dice,
                    record.difficulty,
                    json.dumps(record.dice),
                    record.hits,
                    record.narrator_rights.value,
                    record.reserve_after,
                ),
            )
            if record.hits < record.difficulty:
                helpers = connection.execute(
                    """SELECT helper_character_id FROM roll_helpers
                       WHERE interaction_id = ?""",
                    (record.interaction_id,),
                ).fetchall()
                for helper in helpers:
                    helper_row = connection.execute(
                        "SELECT sheet_json FROM characters WHERE character_id = ?",
                        (helper["helper_character_id"],),
                    ).fetchone()
                    helper_sheet = json.loads(helper_row["sheet_json"])
                    helper_sheet["reserve_current"] = min(
                        helper_sheet["reserve_maximum"],
                        helper_sheet["reserve_current"] + 1,
                    )
                    connection.execute(
                        """UPDATE characters
                           SET sheet_json = ?, revision = revision + 1
                           WHERE character_id = ?""",
                        (
                            json.dumps(helper_sheet, ensure_ascii=False),
                            helper["helper_character_id"],
                        ),
                    )
            connection.execute(
                """INSERT INTO domain_events
                   (game_id, event_type, payload_json, causation_id)
                   VALUES (?, 'roll_committed', ?, ?)""",
                (
                    record.game_id,
                    json.dumps(
                        {
                            "roll_id": record.roll_id,
                            "interaction_id": record.interaction_id,
                            "hits": record.hits,
                            "difficulty": record.difficulty,
                            "narrator_rights": record.narrator_rights.value,
                            "reserve_after": record.reserve_after,
                            "consumed_bonus_ids": list(consumed_bonus_ids),
                        },
                        ensure_ascii=False,
                    ),
                    record.interaction_id,
                ),
            )
            if narration_interaction_id is not None:
                connection.execute(
                    """INSERT INTO pending_interactions
                       (interaction_id, game_id, player_id, origin_channel_id, scene_id,
                        kind, prompt,
                        payload_json, status, revision)
                       VALUES (?, ?, ?, ?, ?, 'player_narration', ?, ?, 'open', 0)""",
                    (
                        narration_interaction_id,
                        pending["game_id"],
                        pending["player_id"],
                        pending["origin_channel_id"],
                        pending["scene_id"],
                        narration_prompt,
                        json.dumps(
                            {
                                "roll_id": record.roll_id,
                                "narrator_rights": record.narrator_rights.value,
                                "prompt_source_event_id": record.confirmation_event_id,
                            }
                        ),
                    ),
                )

    def outbox_source_for_discord_message(
        self,
        *,
        channel_id: str,
        discord_message_id: str,
    ) -> str | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                """SELECT source_event_id FROM outbox_messages
                   WHERE channel_id = ? AND discord_message_id = ?
                   ORDER BY id DESC LIMIT 1""",
                (channel_id, discord_message_id),
            ).fetchone()
        return (
            None if row is None or row["source_event_id"] is None else str(row["source_event_id"])
        )

    def outbox_delivery_for_discord_message(
        self,
        *,
        channel_id: str,
        discord_message_id: str,
    ) -> dict[str, str | None] | None:
        """Resolve one delivered Discord message without conflating response and notice kinds."""

        with closing(self.connect()) as connection:
            row = connection.execute(
                """SELECT source_event_id, kind FROM outbox_messages
                   WHERE channel_id = ? AND discord_message_id = ?
                   ORDER BY id DESC LIMIT 1""",
                (channel_id, discord_message_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "source_event_id": (
                None if row["source_event_id"] is None else str(row["source_event_id"])
            ),
            "kind": str(row["kind"]),
        }

    def mark_delivered(
        self,
        outbox_id: int,
        *,
        discord_message_id: str | None = None,
        delivery_claim: str | None = None,
    ) -> None:
        claim_clause = (
            " AND delivery_claim IS NULL" if delivery_claim is None else " AND delivery_claim = ?"
        )
        parameters: tuple[object, ...] = (discord_message_id, outbox_id)
        if delivery_claim is not None:
            parameters += (delivery_claim,)
        with self.transaction() as connection:
            if not self._owns_outbox_delivery(
                connection,
                outbox_id=outbox_id,
                delivery_claim=delivery_claim,
            ):
                return
            connection.execute(
                f"""UPDATE outbox_messages
                      SET delivered_at = CURRENT_TIMESTAMP, error = NULL,
                           delivery_claim = NULL, claimed_at = NULL, next_attempt_at = NULL,
                           discord_message_id = COALESCE(?, discord_message_id)
                    WHERE id = ? AND delivered_at IS NULL{claim_clause}""",
                parameters,
            )

    def mark_delivery_terminal(
        self,
        outbox_id: int,
        error: str,
        *,
        delivery_claim: str | None = None,
    ) -> None:
        claim_clause = (
            " AND delivery_claim IS NULL" if delivery_claim is None else " AND delivery_claim = ?"
        )
        parameters: tuple[object, ...] = (error[:2000], outbox_id)
        if delivery_claim is not None:
            parameters += (delivery_claim,)
        with self.transaction() as connection:
            if not self._owns_outbox_delivery(
                connection,
                outbox_id=outbox_id,
                delivery_claim=delivery_claim,
            ):
                return
            connection.execute(
                f"""UPDATE outbox_messages
                   SET attempts = 5, error = ?, delivery_claim = NULL,
                       claimed_at = NULL, next_attempt_at = NULL
                   WHERE id = ? AND delivered_at IS NULL{claim_clause}""",
                parameters,
            )

    def mark_delivery_failed(
        self,
        outbox_id: int,
        error: str,
        *,
        delivery_claim: str | None = None,
    ) -> None:
        claim_clause = (
            " AND delivery_claim IS NULL" if delivery_claim is None else " AND delivery_claim = ?"
        )
        lookup_parameters: tuple[object, ...] = (outbox_id,)
        if delivery_claim is not None:
            lookup_parameters += (delivery_claim,)
        with self.transaction() as connection:
            if not self._owns_outbox_delivery(
                connection,
                outbox_id=outbox_id,
                delivery_claim=delivery_claim,
            ):
                return
            row = connection.execute(
                f"""SELECT attempts FROM outbox_messages
                    WHERE id = ? AND delivered_at IS NULL{claim_clause}""",
                lookup_parameters,
            ).fetchone()
            if row is None:
                return
            attempts = int(row["attempts"]) + 1
            delay = OUTBOX_RETRY_DELAYS_SECONDS[
                min(attempts - 1, len(OUTBOX_RETRY_DELAYS_SECONDS) - 1)
            ]
            update_parameters: tuple[object, ...] = (
                attempts,
                error[:2000],
                f"+{delay} seconds",
                outbox_id,
            )
            if delivery_claim is not None:
                update_parameters += (delivery_claim,)
            connection.execute(
                f"""UPDATE outbox_messages
                   SET attempts = ?, error = ?,
                       delivery_claim = NULL, claimed_at = NULL,
                       next_attempt_at = datetime('now', ?)
                   WHERE id = ?{claim_clause}""",
                update_parameters,
            )

    @staticmethod
    def _owns_outbox_delivery(
        connection: sqlite3.Connection,
        *,
        outbox_id: int,
        delivery_claim: str | None,
    ) -> bool:
        row = connection.execute(
            """SELECT delivered_at, delivery_claim
               FROM outbox_messages WHERE id = ?""",
            (outbox_id,),
        ).fetchone()
        if row is None or row["delivered_at"] is not None:
            return False
        if row["delivery_claim"] != delivery_claim:
            raise RuntimeError("outbox delivery claim lost")
        return True
