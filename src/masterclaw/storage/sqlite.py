from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from masterclaw.domain.actions import RollRecord
from masterclaw.domain.characters import CharacterState, Condition, PlotItem
from masterclaw.domain.mechanics import CharacterSheet, Flag, FlagType, Trait
from masterclaw.domain.models import ChannelState, GameLifecycle, InboxStatus, IncomingMessage
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
    WorldState,
)

MIGRATION_1_SQL = """
CREATE TABLE IF NOT EXISTS inbox_messages (
    event_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    author_id TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS inbox_pending_order
    ON inbox_messages(status, created_at, event_id);
CREATE TABLE IF NOT EXISTS channel_bindings (
    channel_id TEXT PRIMARY KEY,
    game_id TEXT,
    lifecycle TEXT,
    revision INTEGER NOT NULL DEFAULT 0
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
CREATE TABLE IF NOT EXISTS worlds (
    world_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    content_json TEXT NOT NULL DEFAULT '{}',
    revision INTEGER NOT NULL DEFAULT 0
);
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
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    delivered_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
"""

MIGRATION_2_SQL = """
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT REFERENCES games(game_id) ON DELETE SET NULL,
    role TEXT NOT NULL,
    model TEXT NOT NULL,
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
"""

MIGRATIONS: tuple[tuple[int, str], ...] = ((1, MIGRATION_1_SQL), (2, MIGRATION_2_SQL))
SCHEMA_VERSION = MIGRATIONS[-1][0]


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

    def initialize(self) -> None:
        with closing(self.connect()) as connection:
            with connection:
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS schema_version (
                           version INTEGER PRIMARY KEY,
                           applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                       )"""
                )
                current = connection.execute(
                    "SELECT COALESCE(MAX(version), 0) AS version FROM schema_version"
                ).fetchone()["version"]
                if current > SCHEMA_VERSION:
                    raise RuntimeError(
                        f"unsupported database schema {current}; latest is {SCHEMA_VERSION}"
                    )
                for version, script in MIGRATIONS:
                    if version <= current:
                        continue
                    connection.executescript(
                        "BEGIN IMMEDIATE;\n"
                        + script
                        + f"\nINSERT INTO schema_version(version) VALUES ({version});\n"
                        + "COMMIT;"
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
                   (game_id, role, model, response_id, prompt_tokens,
                    completion_tokens, cache_read_tokens, cache_write_tokens,
                    reasoning_tokens, latency_ms, cost, success, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    game_id,
                    role,
                    model,
                    response_id,
                    *values,
                    cost,
                    int(success),
                    error[:2000] if error else None,
                ),
            )

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

    def enqueue(self, message: IncomingMessage) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO inbox_messages
                   (event_id, channel_id, author_id, content, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    message.event_id,
                    message.channel_id,
                    message.author_id,
                    message.content,
                    message.created_at.isoformat(),
                ),
            )
            return cursor.rowcount == 1

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

    def create_world(self, world: WorldState) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO worlds(world_id, title, status, revision)
                   VALUES (?, ?, ?, ?)""",
                (world.world_id, world.title, world.status, world.revision),
            )

    def world_state(self, world_id: str) -> WorldState | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT world_id, title, status, revision FROM worlds WHERE world_id = ?",
                (world_id,),
            ).fetchone()
        if row is None:
            return None
        return WorldState(row["world_id"], row["title"], row["status"], row["revision"])

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
            cursor = connection.execute(
                """UPDATE games
                   SET narrative_channel_id = ?, revision = revision + 1
                   WHERE game_id = ? AND revision = ?""",
                (channel_id, game_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("game revision conflict")

    def set_progression_enabled(
        self, *, game_id: str, enabled: bool, expected_revision: int
    ) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE games
                   SET progression_enabled = ?, revision = revision + 1
                   WHERE game_id = ? AND revision = ? AND lifecycle = 'preparing'""",
                (int(enabled), game_id, expected_revision),
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
                   SET narrator_rights_level = ?, revision = revision + 1
                   WHERE game_id = ? AND revision = ?""",
                (level.value, game_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("game revision conflict")

    def scene_count(self, game_id: str) -> int:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM scenes WHERE game_id = ?", (game_id,)
            ).fetchone()
        return int(row["count"])

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

    def character_count(self, game_id: str) -> int:
        with closing(self.connect()) as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM characters WHERE game_id = ?",
                    (game_id,),
                ).fetchone()["count"]
            )

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
    ) -> CharacterState:
        if xp_cost <= 0:
            raise ValueError("xp_cost must be positive")
        with self.transaction() as connection:
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
                    f"advancement:{character_id}:{expected_revision}",
                ),
            )
        updated = self.character_for_player(game_id=row["game_id"], player_id=row["player_id"])
        assert updated is not None
        return updated

    def record_activity(self, *, game_id: str, occurred_at) -> ActivityUpdate:
        """Record activity and automatically award newly completed XP intervals."""
        from datetime import datetime

        if occurred_at.tzinfo is None:
            raise ValueError("activity timestamp must be timezone-aware")
        with self.transaction() as connection:
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
                    (occurred_at.isoformat(), credited, game_id),
                )
            xp_each = 0
            completed = total // XP_INTERVAL_SECONDS
            if bool(row["progression_enabled"]) and completed > row["awarded_intervals"]:
                character_count = connection.execute(
                    "SELECT COUNT(*) AS count FROM characters WHERE game_id = ?",
                    (game_id,),
                ).fetchone()["count"]
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
            return ActivityUpdate(credited, total, xp_each)

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

    def bind_channel(self, *, channel_id: str, game_id: str) -> None:
        with self.transaction() as connection:
            game = connection.execute(
                "SELECT lifecycle FROM games WHERE game_id = ?", (game_id,)
            ).fetchone()
            if game is None:
                raise ValueError(f"game does not exist: {game_id}")
            connection.execute(
                """INSERT INTO channel_bindings(channel_id, game_id, lifecycle)
                   VALUES (?, ?, ?)
                   ON CONFLICT(channel_id) DO UPDATE SET
                     game_id = excluded.game_id,
                     lifecycle = excluded.lifecycle,
                     revision = channel_bindings.revision + 1""",
                (channel_id, game_id, game["lifecycle"]),
            )

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

    def game_state(self, game_id: str) -> GameState | None:
        with closing(self.connect()) as connection:
            row = connection.execute("SELECT * FROM games WHERE game_id = ?", (game_id,)).fetchone()
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
            revision=row["revision"],
        )

    def pending(self, *, channel_id: str, limit: int = 50) -> list[IncomingMessage]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """SELECT event_id, channel_id, author_id, content, created_at
                   FROM inbox_messages
                   WHERE channel_id = ? AND status = 'pending'
                   ORDER BY created_at, event_id LIMIT ?""",
                (channel_id, limit),
            ).fetchall()
        from datetime import datetime

        return [
            IncomingMessage(
                event_id=row["event_id"],
                channel_id=row["channel_id"],
                author_id=row["author_id"],
                content=row["content"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def pending_inbox_channels(self) -> list[str]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """SELECT DISTINCT channel_id FROM inbox_messages
                   WHERE status = 'pending' ORDER BY channel_id"""
            ).fetchall()
        return [str(row["channel_id"]) for row in rows]

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
                   SET status = 'pending', attempts = 0, error = NULL
                   WHERE event_id = ? AND status = 'failed'""",
                (event_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError("failed inbox event not found")

    def claim_pending(self, *, channel_id: str, limit: int = 50) -> list[IncomingMessage]:
        """Atomically claim the oldest messages for one channel."""
        with self.transaction() as connection:
            rows = connection.execute(
                """SELECT event_id, channel_id, author_id, content, created_at
                   FROM inbox_messages
                   WHERE channel_id = ? AND status = 'pending'
                   ORDER BY created_at, event_id LIMIT ?""",
                (channel_id, limit),
            ).fetchall()
            ids = [row["event_id"] for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"""UPDATE inbox_messages
                        SET status = ?, attempts = attempts + 1
                        WHERE event_id IN ({placeholders})""",
                    (InboxStatus.PROCESSING.value, *ids),
                )
        from datetime import datetime

        return [
            IncomingMessage(
                event_id=row["event_id"],
                channel_id=row["channel_id"],
                author_id=row["author_id"],
                content=row["content"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def complete_batch(
        self,
        *,
        event_ids: list[str],
        channel_id: str,
        contents: list[str],
        idempotency_key: str,
        additional_deliveries: list[tuple[str, str, str]] | None = None,
    ) -> None:
        if not event_ids:
            raise ValueError("event_ids cannot be empty")
        with self.transaction() as connection:
            placeholders = ",".join("?" for _ in event_ids)
            cursor = connection.execute(
                f"""UPDATE inbox_messages SET status = ?, error = NULL
                    WHERE event_id IN ({placeholders}) AND status = ?""",
                (InboxStatus.PROCESSED.value, *event_ids, InboxStatus.PROCESSING.value),
            )
            if cursor.rowcount != len(event_ids):
                raise RuntimeError("batch completion lost ownership of inbox messages")
            for index, chunk in enumerate(contents):
                if chunk.strip():
                    connection.execute(
                        """INSERT OR IGNORE INTO outbox_messages
                           (idempotency_key, channel_id, content) VALUES (?, ?, ?)""",
                        (f"{idempotency_key}:{index}", channel_id, chunk),
                    )
            for target_channel, content, suffix in additional_deliveries or []:
                if content.strip():
                    connection.execute(
                        """INSERT OR IGNORE INTO outbox_messages
                           (idempotency_key, channel_id, content) VALUES (?, ?, ?)""",
                        (f"{idempotency_key}:delivery:{suffix}", target_channel, content),
                    )

    def fail_batch(self, *, event_ids: list[str], error: str, retry: bool = True) -> bool:
        if not event_ids:
            return False
        with self.transaction() as connection:
            placeholders = ",".join("?" for _ in event_ids)
            if retry:
                connection.execute(
                    f"""UPDATE inbox_messages
                        SET status = CASE WHEN attempts >= 3 THEN 'failed' ELSE 'pending' END,
                            error = ?
                        WHERE event_id IN ({placeholders})""",
                    (error[:2000], *event_ids),
                )
            else:
                connection.execute(
                    f"""UPDATE inbox_messages SET status = 'failed', error = ?
                        WHERE event_id IN ({placeholders})""",
                    (error[:2000], *event_ids),
                )
            rows = connection.execute(
                f"SELECT status FROM inbox_messages WHERE event_id IN ({placeholders})",
                event_ids,
            ).fetchall()
            return bool(rows) and all(row["status"] == InboxStatus.FAILED.value for row in rows)

    def queue_system_notice(self, *, channel_id: str, key: str, content: str) -> None:
        if not content.strip():
            raise ValueError("system notice cannot be empty")
        with self.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO outbox_messages
                   (idempotency_key, channel_id, content) VALUES (?, ?, ?)""",
                (f"system-notice:{key}", channel_id, content.strip()),
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
                   (interaction_id, game_id, player_id, scene_id, kind, prompt,
                    payload_json, status, revision)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pending.interaction_id,
                    pending.game_id,
                    pending.player_id,
                    pending.scene_id,
                    pending.kind.value,
                    pending.prompt,
                    json.dumps(pending.payload, ensure_ascii=False),
                    pending.status.value,
                    pending.revision,
                ),
            )

    def open_pending(self, *, game_id: str, player_id: str) -> PendingInteraction | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                """SELECT * FROM pending_interactions
                   WHERE game_id = ? AND player_id = ? AND status = 'open'
                   ORDER BY created_at DESC LIMIT 1""",
                (game_id, player_id),
            ).fetchone()
        if row is None:
            return None
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
        )

    def pending_by_id(self, interaction_id: str) -> PendingInteraction | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM pending_interactions WHERE interaction_id = ?",
                (interaction_id,),
            ).fetchone()
        if row is None:
            return None
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
        )

    def cancel_pending(
        self, *, interaction_id: str, player_id: str, expected_revision: int
    ) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE pending_interactions
                   SET status = 'cancelled', revision = revision + 1,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE interaction_id = ? AND player_id = ?
                     AND revision = ? AND status = 'open'""",
                (interaction_id, player_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("pending interaction revision conflict")

    def resolve_pending(
        self, *, interaction_id: str, player_id: str, expected_revision: int
    ) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE pending_interactions
                   SET status = 'resolved', revision = revision + 1,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE interaction_id = ? AND player_id = ?
                     AND revision = ? AND status = 'open'""",
                (interaction_id, player_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("pending interaction revision conflict")

    def pending_outbox(
        self, *, channel_id: str | None = None, limit: int = 50
    ) -> list[sqlite3.Row]:
        with closing(self.connect()) as connection:
            if channel_id is None:
                return connection.execute(
                    """SELECT id, idempotency_key, channel_id, content, attempts
                   FROM outbox_messages
                   WHERE delivered_at IS NULL AND attempts < 5
                   ORDER BY id LIMIT ?""",
                    (limit,),
                ).fetchall()
            return connection.execute(
                """SELECT id, idempotency_key, channel_id, content, attempts
                   FROM outbox_messages
                   WHERE delivered_at IS NULL AND attempts < 5 AND channel_id = ?
                   ORDER BY id LIMIT ?""",
                (channel_id, limit),
            ).fetchall()

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
                """UPDATE outbox_messages SET attempts = 0, error = NULL
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
        self, *, game_id: str, helper_player_id: str, target_player_id: str
    ) -> PendingInteraction:
        if helper_player_id == target_player_id:
            raise ValueError("a player cannot help their own roll")
        pending = self.open_pending(game_id=game_id, player_id=target_player_id)
        if pending is None or pending.kind is not PendingKind.POOL_CONFIRMATION:
            raise ValueError("target player has no pool awaiting confirmation")
        helper_scene = self.scene_projection(game_id=game_id, player_id=helper_player_id)
        if helper_scene is None or helper_scene["scene_id"] != pending.scene_id:
            raise ValueError("helper must be in the same scene")
        with self.transaction() as connection:
            character = connection.execute(
                """SELECT character_id, sheet_json FROM characters
                   WHERE game_id = ? AND player_id = ?""",
                (game_id, helper_player_id),
            ).fetchone()
            if character is None:
                raise ValueError("helper has no character")
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
                """SELECT status, revision, player_id, scene_id, game_id
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
                        },
                        ensure_ascii=False,
                    ),
                    record.interaction_id,
                ),
            )
            if narration_interaction_id is not None:
                connection.execute(
                    """INSERT INTO pending_interactions
                       (interaction_id, game_id, player_id, scene_id, kind, prompt,
                        payload_json, status, revision)
                       VALUES (?, ?, ?, ?, 'player_narration', ?, ?, 'open', 0)""",
                    (
                        narration_interaction_id,
                        pending["game_id"],
                        pending["player_id"],
                        pending["scene_id"],
                        "Опишите исход в пределах полученных прав рассказчика.",
                        json.dumps(
                            {
                                "roll_id": record.roll_id,
                                "narrator_rights": record.narrator_rights.value,
                            }
                        ),
                    ),
                )

    def mark_delivered(self, outbox_id: int) -> None:
        with self.transaction() as connection:
            connection.execute(
                """UPDATE outbox_messages
                   SET delivered_at = CURRENT_TIMESTAMP, error = NULL
                   WHERE id = ?""",
                (outbox_id,),
            )

    def mark_delivery_failed(self, outbox_id: int, error: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                """UPDATE outbox_messages SET attempts = attempts + 1, error = ?
                   WHERE id = ?""",
                (error[:2000], outbox_id),
            )
