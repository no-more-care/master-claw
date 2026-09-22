import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from masterclaw.domain.models import GameLifecycle, IncomingMessage
from masterclaw.domain.state import (
    GameState,
    PendingInteraction,
    PendingKind,
    PendingStatus,
    ReserveRecoveryMode,
    WorldState,
)
from masterclaw.storage.sqlite import SCHEMA_VERSION, SQLiteStore


def _create_version_two_world_workspace_database(
    store: SQLiteStore, rows: list[tuple[str, str, str, str, str, str, int]]
) -> None:
    with store.connect() as connection:
        connection.execute(
            """CREATE TABLE schema_version (
                   version INTEGER PRIMARY KEY,
                   applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        connection.execute("INSERT INTO schema_version(version) VALUES (2)")
        connection.execute(
            """CREATE TABLE worlds (
                   world_id TEXT PRIMARY KEY,
                   title TEXT NOT NULL,
                   status TEXT NOT NULL DEFAULT 'draft',
                   content_json TEXT NOT NULL DEFAULT '{}',
                   revision INTEGER NOT NULL DEFAULT 0
               )"""
        )
        for world_id in dict.fromkeys(row[1] for row in rows):
            connection.execute(
                "INSERT INTO worlds(world_id, title) VALUES (?, ?)",
                (world_id, f"World {world_id}"),
            )
        connection.execute(
            """CREATE TABLE world_workspaces (
                   channel_id TEXT PRIMARY KEY,
                   world_id TEXT NOT NULL REFERENCES worlds(world_id) ON DELETE CASCADE,
                   stage TEXT NOT NULL DEFAULT 'collecting',
                   brief TEXT NOT NULL,
                   settings_json TEXT NOT NULL DEFAULT '{}',
                   sources_json TEXT NOT NULL DEFAULT '{}',
                   revision INTEGER NOT NULL DEFAULT 0
               )"""
        )
        connection.executemany(
            """INSERT INTO world_workspaces
               (channel_id, world_id, stage, brief, settings_json, sources_json, revision)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )


def test_only_one_world_project_can_be_active_per_channel(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "workspace-unique.sqlite3")
    store.initialize()
    store.create_world(WorldState("first", "First"))
    store.create_world(WorldState("second", "Second"))
    store.save_world_workspace(
        channel_id="channel",
        world_id="first",
        stage="collecting",
        brief="First brief",
        settings={},
        sources={},
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.save_world_workspace(
            channel_id="channel",
            world_id="second",
            stage="collecting",
            brief="Second brief",
            settings={},
            sources={},
        )

    assert store.world_workspace("channel")["world_id"] == "first"


def test_world_and_workspace_cas_rolls_back_if_channel_binds_during_intake(
    tmp_path,
) -> None:
    store = SQLiteStore(tmp_path / "workspace-cas.sqlite3")
    store.initialize()
    store.create_world(WorldState("bound-world", "Bound"))
    store.create_game(GameState("bound-game", "bound-world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="bound-game")

    with pytest.raises(RuntimeError, match="binding changed"):
        store.save_world_workspace(
            channel_id="channel",
            world_id="generated-world",
            stage="collecting",
            brief="Generated after an async intake",
            settings={},
            sources={},
            world=WorldState("generated-world", "Generated"),
        )

    assert store.world_state("generated-world") is None
    assert store.world_workspace("channel") is None
    assert store.channel_state("channel").game_id == "bound-game"


def test_active_world_workspace_blocks_binding_and_thread_inheritance(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "workspace-binding.sqlite3")
    store.initialize()
    store.create_world(WorldState("workspace-world", "Workspace"))
    store.save_world_workspace(
        channel_id="thread",
        world_id="workspace-world",
        stage="collecting",
        brief="Draft",
        settings={},
        sources={},
    )
    store.create_world(WorldState("game-world", "Game"))
    store.create_game(GameState("game", "game-world", GameLifecycle.ACTIVE))

    with pytest.raises(RuntimeError, match="active world workspace"):
        store.bind_channel(channel_id="thread", game_id="game")

    store.record_discord_channel(
        channel_id="parent",
        guild_id="guild",
        parent_channel_id=None,
        kind="text",
    )
    store.record_discord_channel(
        channel_id="thread",
        guild_id="guild",
        parent_channel_id="parent",
        kind="thread",
    )
    store.enable_channel_monitoring("parent")
    store.bind_channel(channel_id="parent", game_id="game")
    with pytest.raises(ValueError, match="active world workspace"):
        store.inherit_thread_context(
            channel_id="thread",
            parent_channel_id="parent",
            guild_id="guild",
        )

    assert store.world_workspace("thread")["world_id"] == "workspace-world"
    assert store.channel_state("thread").game_id is None


def test_inherited_thread_workspace_promotes_monitoring_and_stays_independent(
    tmp_path,
) -> None:
    store = SQLiteStore(tmp_path / "workspace-thread.sqlite3")
    store.initialize()
    store.record_discord_channel(
        channel_id="parent",
        guild_id="guild",
        parent_channel_id=None,
        kind="text",
    )
    store.record_discord_channel(
        channel_id="thread",
        guild_id="guild",
        parent_channel_id="parent",
        kind="thread",
    )
    store.enable_channel_monitoring("parent")
    assert store.inherit_thread_context(
        channel_id="thread",
        parent_channel_id="parent",
        guild_id="guild",
    )
    store.save_world_workspace(
        channel_id="thread",
        world_id="draft-world",
        stage="collecting",
        brief="Independent draft",
        settings={},
        sources={},
        world=WorldState("draft-world", "Draft"),
    )
    with store.connect() as connection:
        provenance = connection.execute(
            """SELECT inherited_from_channel_id FROM monitored_channels
               WHERE channel_id = 'thread'"""
        ).fetchone()
    assert provenance["inherited_from_channel_id"] is None

    store.create_world(WorldState("game-world", "Game"))
    store.create_game(GameState("game", "game-world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="parent", game_id="game")
    assert store.inherit_thread_context(
        channel_id="thread",
        parent_channel_id="parent",
        guild_id="guild",
    )
    assert store.channel_state("thread").game_id is None

    assert store.disable_channel_monitoring("parent")
    assert store.channel_monitoring_enabled("thread")
    assert store.world_workspace("thread")["world_id"] == "draft-world"
    store.pause_world_workspace(channel_id="thread", world_id="draft-world")
    assert store.world_workspace("thread") is None


def test_inbox_is_durable_ordered_and_idempotent(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "masterclaw.sqlite3")
    store.initialize()
    later = IncomingMessage.now(
        event_id="later", channel_id="channel", author_id="bob", content="second"
    )
    earlier = IncomingMessage(
        event_id="earlier",
        channel_id="channel",
        author_id="alice",
        content="first",
        created_at=later.created_at.replace(microsecond=max(0, later.created_at.microsecond - 1)),
    )

    assert store.enqueue(later)
    assert store.enqueue(earlier)
    assert not store.enqueue(earlier)
    assert store.pending_inbox_channels() == ["channel"]
    assert [item.event_id for item in store.pending(channel_id="channel")] == ["earlier", "later"]


def test_online_backup_contains_consistent_inbox_state(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "live.sqlite3")
    store.initialize()
    store.enqueue(
        IncomingMessage.now(event_id="1", channel_id="channel", author_id="alice", content="hello")
    )
    backup_path = store.backup(tmp_path / "backups" / "snapshot.sqlite3")
    backup = SQLiteStore(backup_path)
    assert [item.event_id for item in backup.pending(channel_id="channel")] == ["1"]


def test_schema_initialization_is_idempotent(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.initialize()


def test_version_three_transport_rows_migrate_with_safe_defaults(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "v3.sqlite3")
    with store.connect() as connection:
        connection.executescript(
            """CREATE TABLE schema_version (
                   version INTEGER PRIMARY KEY,
                   applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               );
               INSERT INTO schema_version(version) VALUES (3);
               CREATE TABLE inbox_messages (
                   event_id TEXT PRIMARY KEY,
                   channel_id TEXT NOT NULL,
                   author_id TEXT NOT NULL,
                   content TEXT NOT NULL,
                   created_at TEXT NOT NULL,
                   status TEXT NOT NULL DEFAULT 'pending',
                   attempts INTEGER NOT NULL DEFAULT 0,
                   error TEXT
               );
               INSERT INTO inbox_messages
                   (event_id, channel_id, author_id, content, created_at)
                   VALUES ('event', 'channel', 'alice', 'hello',
                           '2026-01-01T00:00:00+00:00');
               CREATE TABLE outbox_messages (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   idempotency_key TEXT NOT NULL UNIQUE,
                   channel_id TEXT NOT NULL,
                   content TEXT NOT NULL,
                   created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                   delivered_at TEXT,
                   attempts INTEGER NOT NULL DEFAULT 0,
                   error TEXT
               );
               INSERT INTO outbox_messages(idempotency_key, channel_id, content)
                   VALUES ('legacy-reply', 'channel', 'answer');"""
        )

    store.initialize()

    pending = store.pending(channel_id="channel")
    assert pending[0].guild_id is None
    assert pending[0].attachments == ()
    outbox = store.pending_outbox(channel_id="channel")
    assert outbox[0]["kind"] == "message"
    assert outbox[0]["discord_nonce"]
    assert store.schema_version() == SCHEMA_VERSION


def test_version_four_pending_inbox_rows_backfill_the_bound_game_snapshot(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "v4.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="game")
    store.enqueue(
        IncomingMessage.now(
            event_id="legacy-event",
            channel_id="channel",
            author_id="alice",
            content="legacy turn",
        )
    )
    with store.connect() as connection:
        connection.execute("DROP INDEX inbox_game_time")
        connection.execute("ALTER TABLE inbox_messages DROP COLUMN game_id")
        connection.execute("DELETE FROM schema_version")
        connection.execute("INSERT INTO schema_version(version) VALUES (4)")

    store.initialize()

    with store.connect() as connection:
        row = connection.execute(
            "SELECT game_id FROM inbox_messages WHERE event_id = 'legacy-event'"
        ).fetchone()
        indexes = {
            item["name"]
            for item in connection.execute("PRAGMA index_list(inbox_messages)").fetchall()
        }
    assert row["game_id"] == "game"
    assert "inbox_game_time" in indexes
    assert store.schema_version() == SCHEMA_VERSION


def test_version_four_processed_history_is_not_attributed_to_current_binding(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "v4-processed.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("old-game", "world", GameLifecycle.ACTIVE))
    store.create_game(GameState("current-game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="old-game")
    store.enqueue(
        IncomingMessage.now(
            event_id="old-secret",
            channel_id="channel",
            author_id="alice",
            content="old-game-only fact",
        )
    )
    store.claim_pending(channel_id="channel", limit=1)
    store.complete_batch(
        event_ids=["old-secret"],
        channel_id="channel",
        contents=[],
        idempotency_key="old-secret-response",
    )
    store.unbind_channel(channel_id="channel", expected_game_id="old-game")
    store.bind_channel(channel_id="channel", game_id="current-game")
    with store.connect() as connection:
        connection.execute("DROP INDEX inbox_game_time")
        connection.execute("ALTER TABLE inbox_messages DROP COLUMN ingress_game_id")
        connection.execute("ALTER TABLE inbox_messages DROP COLUMN scene_id")
        connection.execute("ALTER TABLE inbox_messages DROP COLUMN scene_participants_json")
        connection.execute("ALTER TABLE inbox_messages DROP COLUMN game_id")
        connection.execute("DELETE FROM schema_version")
        connection.execute("INSERT INTO schema_version(version) VALUES (4)")

    store.initialize()

    with store.connect() as connection:
        migrated = connection.execute(
            """SELECT game_id, ingress_game_id
               FROM inbox_messages WHERE event_id = 'old-secret'"""
        ).fetchone()
    assert migrated["game_id"] is None
    assert migrated["ingress_game_id"] is None
    assert (
        store.recent_chat_messages(
            game_id="current-game",
            channel_id="channel",
            limit=10,
        )
        == []
    )


def test_version_four_unbound_queue_is_not_claimed_by_a_later_binding(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "v4-unbound.sqlite3")
    store.initialize()
    store.enqueue(
        IncomingMessage.now(
            event_id="legacy-unbound",
            channel_id="channel",
            author_id="alice",
            content="old ambiguous turn",
        )
    )
    with store.connect() as connection:
        connection.execute("DROP INDEX inbox_game_time")
        connection.execute("ALTER TABLE inbox_messages DROP COLUMN ingress_game_id")
        connection.execute("ALTER TABLE inbox_messages DROP COLUMN scene_id")
        connection.execute("ALTER TABLE inbox_messages DROP COLUMN scene_participants_json")
        connection.execute("ALTER TABLE inbox_messages DROP COLUMN game_id")
        connection.execute("DELETE FROM schema_version")
        connection.execute("INSERT INTO schema_version(version) VALUES (4)")

    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("new-game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="new-game")

    claimed = store.claim_pending(channel_id="channel", limit=1)[0]
    assert claimed.has_routing_snapshot
    assert claimed.routing_game_id is None
    store.complete_batch(
        event_ids=["legacy-unbound"],
        channel_id="channel",
        contents=[],
        idempotency_key="legacy-unbound",
    )

    with store.connect() as connection:
        migrated = connection.execute(
            """SELECT game_id, ingress_game_id
               FROM inbox_messages WHERE event_id = 'legacy-unbound'"""
        ).fetchone()
    assert migrated["game_id"] is None
    assert migrated["ingress_game_id"] == ""


def test_legacy_world_workspaces_migrate_once_without_data_loss(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "legacy.sqlite3")
    _create_version_two_world_workspace_database(
        store,
        [("channel", "world", "drafting", "Brief", '{"tone":"dark"}', "{}", 4)],
    )

    store.initialize()
    store.initialize()

    assert store.schema_version() == SCHEMA_VERSION
    assert store.world_project("world") == {
        "channel_id": "channel",
        "world_id": "world",
        "stage": "drafting",
        "brief": "Brief",
        "settings": {"tone": "dark"},
        "sources": {},
        "revision": 4,
    }
    with store.connect() as connection:
        legacy_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'world_workspaces'"
        ).fetchone()
        versions = [
            row["version"]
            for row in connection.execute(
                "SELECT version FROM schema_version ORDER BY version"
            ).fetchall()
        ]
    assert legacy_table is None
    assert versions == [2, SCHEMA_VERSION]


def test_conflicting_legacy_workspaces_abort_and_preserve_every_source_row(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "legacy-conflict.sqlite3")
    _create_version_two_world_workspace_database(
        store,
        [
            ("channel-a", "world", "collecting", "First", "{}", "{}", 0),
            ("channel-b", "world", "drafting", "Second", "{}", "{}", 1),
        ],
    )

    with pytest.raises(RuntimeError, match="conflicts with an existing world project"):
        store.initialize()

    with store.connect() as connection:
        source_rows = connection.execute(
            "SELECT channel_id, brief FROM world_workspaces ORDER BY channel_id"
        ).fetchall()
        project_count = connection.execute(
            "SELECT COUNT(*) AS count FROM world_projects"
        ).fetchone()["count"]
        version = connection.execute(
            "SELECT MAX(version) AS version FROM schema_version"
        ).fetchone()["version"]
    assert [(row["channel_id"], row["brief"]) for row in source_rows] == [
        ("channel-a", "First"),
        ("channel-b", "Second"),
    ]
    assert project_count == 0
    assert version == 2


def test_initialize_backfills_missing_game_rules(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "rules-backfill.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.PREPARING))
    with store.transaction() as connection:
        connection.execute("DELETE FROM game_rules WHERE game_id = 'game'")

    store.initialize()

    assert store.game_state("game").reserve_recovery_mode is ReserveRecoveryMode.BOTH
    with store.connect() as connection:
        rule_count = connection.execute(
            "SELECT COUNT(*) AS count FROM game_rules WHERE game_id = 'game'"
        ).fetchone()["count"]
    assert rule_count == 1


def test_setting_reserve_mode_upserts_a_missing_rule(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "rules-upsert.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.PREPARING))
    with store.transaction() as connection:
        connection.execute("DELETE FROM game_rules WHERE game_id = 'game'")

    store.set_reserve_recovery_mode(
        game_id="game",
        mode=ReserveRecoveryMode.SAFE_REST,
        expected_revision=0,
    )

    game = store.game_state("game")
    assert game is not None
    assert game.reserve_recovery_mode is ReserveRecoveryMode.SAFE_REST
    assert game.revision == 1


def test_initialize_completes_pre_fingerprint_telemetry_schema(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    with store.connect() as connection:
        connection.execute(
            """CREATE TABLE llm_calls (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               game_id TEXT,
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
            )"""
        )
    store.initialize()
    with store.connect() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(llm_calls)")}
    assert "prompt_fingerprint" in columns


def test_channel_monitoring_is_explicit_and_persistent(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    assert store.channel_monitoring_enabled("channel") is False
    assert store.enable_channel_monitoring("channel") is True
    assert store.enable_channel_monitoring("channel") is False
    assert store.channel_monitoring_enabled("channel") is True
    assert SQLiteStore(store.path).channel_monitoring_enabled("channel") is True
    assert store.disable_channel_monitoring("channel") is True
    assert store.disable_channel_monitoring("channel") is False


def test_llm_metrics_are_persisted_and_aggregated_per_game(tmp_path) -> None:
    from masterclaw.domain.models import GameLifecycle
    from masterclaw.domain.state import GameState, WorldState

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.record_llm_call(
        game_id="game",
        role="reasoning",
        model="openrouter/nex-agi/nex-n2-pro",
        prompt_fingerprint="a" * 64,
        response_id="response-1",
        prompt_tokens=100,
        completion_tokens=40,
        cache_read_tokens=20,
        cache_write_tokens=0,
        reasoning_tokens=10,
        latency_ms=1250,
        cost=0.012,
        success=True,
    )
    totals = store.llm_session_totals("game")
    assert totals == {
        "calls": 1,
        "prompt_tokens": 100,
        "completion_tokens": 40,
        "reasoning_tokens": 10,
        "latency_ms": 1250,
        "cost": 0.012,
    }
    with store.connect() as connection:
        fingerprint = connection.execute("SELECT prompt_fingerprint FROM llm_calls").fetchone()[
            "prompt_fingerprint"
        ]
    assert fingerprint == "a" * 64


def test_recent_context_history_is_ordered_and_bounded(tmp_path) -> None:
    from masterclaw.domain.models import GameLifecycle
    from masterclaw.domain.state import GameState, WorldState

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="game")
    with store.transaction() as connection:
        for number in range(3):
            connection.execute(
                """INSERT INTO domain_events
                   (game_id, event_type, payload_json, causation_id)
                   VALUES (?, ?, ?, ?)""",
                ("game", "scene_changed", f'{{"number":{number}}}', f"cause-{number}"),
            )

    for number in range(3):
        message = IncomingMessage.now(
            event_id=f"message-{number}",
            channel_id="channel",
            author_id="alice",
            content=f"line {number}",
        )
        store.enqueue(message)
        store.claim_pending(channel_id="channel", limit=1)
        store.complete_batch(
            event_ids=[message.event_id],
            channel_id="channel",
            contents=["ok"],
            idempotency_key=f"reply-{number}",
        )

    events = store.recent_domain_events(game_id="game", limit=2)
    messages = store.recent_chat_messages(game_id="game", limit=2)
    assert [event["payload"]["number"] for event in events] == [1, 2]
    assert [message["content"] for message in messages] == ["line 1", "line 2"]


def test_recent_chat_history_is_limited_to_players_in_the_actors_scene(tmp_path) -> None:
    from masterclaw.domain.models import GameLifecycle
    from masterclaw.domain.state import GameState, WorldState

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="game")
    store.create_scene(scene_id="north", game_id="game", title="North")
    store.create_scene(scene_id="south", game_id="game", title="South")
    store.place_player(game_id="game", player_id="alice", scene_id="north")
    store.place_player(game_id="game", player_id="bob", scene_id="north")
    store.place_player(game_id="game", player_id="charlie", scene_id="south")

    for number, author in enumerate(("alice", "charlie", "bob")):
        message = IncomingMessage.now(
            event_id=f"scene-message-{number}",
            channel_id="channel",
            author_id=author,
            content=f"{author} line",
        )
        store.enqueue(message)
        store.claim_pending(channel_id="channel", limit=1)
        store.complete_batch(
            event_ids=[message.event_id],
            channel_id="channel",
            contents=["ok"],
            idempotency_key=f"scene-reply-{number}",
        )

    messages = store.recent_chat_messages(
        game_id="game",
        channel_id="channel",
        player_id="alice",
        limit=10,
    )
    assert [message["author_id"] for message in messages] == ["alice", "bob"]


def test_recent_chat_visibility_is_frozen_when_players_move_between_scenes(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="game")
    store.create_scene(scene_id="north", game_id="game", title="North")
    store.create_scene(scene_id="south", game_id="game", title="South")
    store.place_player(game_id="game", player_id="alice", scene_id="north")
    store.place_player(game_id="game", player_id="bob", scene_id="south")

    def record(event_id: str, content: str) -> None:
        store.enqueue(
            IncomingMessage.now(
                event_id=event_id,
                channel_id="channel",
                author_id="bob",
                content=content,
            )
        )
        store.claim_pending(channel_id="channel", limit=1)
        store.complete_batch(
            event_ids=[event_id],
            channel_id="channel",
            contents=[],
            idempotency_key=f"reply:{event_id}",
        )

    record("south-secret", "only Bob could see this in the south")
    store.place_player(game_id="game", player_id="bob", scene_id="north")
    record("shared-north", "Alice and Bob shared this in the north")
    store.place_player(game_id="game", player_id="bob", scene_id="south")

    history = store.recent_chat_messages(
        game_id="game",
        channel_id="channel",
        player_id="alice",
        limit=10,
    )

    assert [turn["source_event_id"] for turn in history] == ["shared-north"]
    assert history[0]["content"] == "Alice and Bob shared this in the north"


def test_preparation_chat_history_keeps_players_own_turns_before_scene_placement(
    tmp_path,
) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.PREPARING))
    store.bind_channel(channel_id="channel", game_id="game")
    message = IncomingMessage.now(
        event_id="question",
        channel_id="channel",
        author_id="alice",
        content="How do I create a character?",
    )
    store.enqueue(message)
    store.claim_pending(channel_id="channel", limit=1)
    store.complete_batch(
        event_ids=[message.event_id],
        channel_id="channel",
        contents=[],
        idempotency_key="reply",
        assistant_turns=[("question", "alice", "Describe the character in one paragraph.")],
    )

    history = store.recent_chat_messages(
        game_id="game", channel_id="channel", player_id="alice", limit=4
    )
    assert [turn["role"] for turn in history] == ["user", "assistant"]
    assert (
        store.recent_chat_messages(game_id="game", channel_id="channel", player_id="bob", limit=4)
        == []
    )


def test_chat_history_does_not_cross_game_rebinding_in_the_same_channel(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world-one", "World One"))
    store.create_world(WorldState("world-two", "World Two"))
    store.create_game(GameState("game-one", "world-one", GameLifecycle.ACTIVE))
    store.create_game(GameState("game-two", "world-two", GameLifecycle.ACTIVE))

    def record(event_id: str, content: str, *, bind_game_id: str | None = None) -> None:
        message = IncomingMessage.now(
            event_id=event_id,
            channel_id="channel",
            author_id="alice",
            content=content,
        )
        store.enqueue(message)
        store.claim_pending(channel_id="channel", limit=1)
        if bind_game_id is not None:
            store.bind_channel(channel_id="channel", game_id=bind_game_id)
        store.complete_batch(
            event_ids=[event_id],
            channel_id="channel",
            contents=[],
            idempotency_key=f"reply:{event_id}",
            assistant_turns=[(event_id, "alice", f"answer to {content}")],
            completion_game_id=bind_game_id,
        )

    # A selector remains unbound while processing and is associated only on successful completion.
    record("before-first-bind", "choose the first world", bind_game_id="game-one")
    record("first-game", "first game secret question")
    store.unbind_channel(channel_id="channel", expected_game_id="game-one")

    record("before-second-bind", "choose the second world", bind_game_id="game-two")
    record("second-game", "second game question")

    first = store.recent_chat_messages(game_id="game-one", channel_id="channel", limit=20)
    second = store.recent_chat_messages(game_id="game-two", channel_id="channel", limit=20)

    assert [turn["source_event_id"] for turn in first] == [
        "before-first-bind",
        "before-first-bind",
        "first-game",
        "first-game",
    ]
    assert [turn["source_event_id"] for turn in second] == [
        "before-second-bind",
        "before-second-bind",
        "second-game",
        "second-game",
    ]
    assert all("first game" not in str(turn["content"]) for turn in second)


def test_fifo_binding_snapshots_survive_selection_crash_and_replay(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world-one", "World One"))
    store.create_world(WorldState("world-two", "World Two"))
    store.create_game(GameState("game-one", "world-one", GameLifecycle.ACTIVE))
    store.create_game(GameState("game-two", "world-two", GameLifecycle.PREPARING))
    store.bind_channel(channel_id="channel", game_id="game-one")
    for event_id, content in (
        ("new-session", "/game new"),
        ("select-world", "choose World Two"),
    ):
        store.enqueue(
            IncomingMessage.now(
                event_id=event_id,
                channel_id="channel",
                author_id="alice",
                content=content,
            )
        )

    detach = store.claim_pending(channel_id="channel", limit=1)[0]
    assert detach.has_routing_snapshot
    assert detach.routing_game_id == "game-one"
    store.unbind_channel(channel_id="channel", expected_game_id="game-one")
    store.complete_batch(
        event_ids=["new-session"],
        channel_id="channel",
        contents=[],
        idempotency_key="detach",
    )

    selector = store.claim_pending(channel_id="channel", limit=1)[0]
    assert selector.has_routing_snapshot
    assert selector.routing_game_id is None
    store.bind_channel(channel_id="channel", game_id="game-two")
    assert (
        store.fail_batch(
            event_ids=["select-world"],
            error="simulated crash after bind",
            retry=True,
        )
        is False
    )

    replay = store.claim_pending(channel_id="channel", limit=1)[0]
    assert store.channel_state("channel").game_id == "game-two"
    assert replay.has_routing_snapshot
    assert replay.routing_game_id is None
    store.complete_batch(
        event_ids=["select-world"],
        channel_id="channel",
        contents=[],
        idempotency_key="select",
        completion_game_id="game-two",
    )

    with store.connect() as connection:
        rows = {
            row["event_id"]: row["game_id"]
            for row in connection.execute(
                """SELECT event_id, game_id FROM inbox_messages
                   WHERE event_id IN ('new-session', 'select-world')"""
            ).fetchall()
        }
    assert rows == {
        "new-session": "game-one",
        "select-world": "game-two",
    }


def test_selector_completion_owns_null_queue_but_external_bind_does_not(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("selected", "world", GameLifecycle.PREPARING))
    store.create_game(GameState("external", "world", GameLifecycle.ACTIVE))
    received_at = datetime(2026, 1, 1, tzinfo=UTC)
    for index, event_id in enumerate(("selector", "selector-follower")):
        store.enqueue(
            IncomingMessage(
                event_id=event_id,
                channel_id="selector-channel",
                author_id="alice",
                content=event_id,
                created_at=received_at.replace(second=index),
            )
        )
    store.claim_pending(channel_id="selector-channel", limit=1)
    store.bind_channel(channel_id="selector-channel", game_id="selected")
    with store.connect() as connection:
        assert (
            connection.execute(
                """SELECT game_id FROM inbox_messages
                   WHERE event_id = 'selector-follower'"""
            ).fetchone()["game_id"]
            is None
        )
    store.complete_batch(
        event_ids=["selector"],
        channel_id="selector-channel",
        contents=[],
        idempotency_key="selector",
        completion_game_id="selected",
    )

    for index, event_id in enumerate(("external-head", "external-follower")):
        store.enqueue(
            IncomingMessage(
                event_id=event_id,
                channel_id="external-channel",
                author_id="alice",
                content=event_id,
                created_at=received_at.replace(minute=1, second=index),
            )
        )
    store.claim_pending(channel_id="external-channel", limit=1)
    store.bind_channel(channel_id="external-channel", game_id="external")
    store.complete_batch(
        event_ids=["external-head"],
        channel_id="external-channel",
        contents=[],
        idempotency_key="external",
    )

    with store.connect() as connection:
        games = {
            row["event_id"]: row["game_id"]
            for row in connection.execute(
                """SELECT event_id, game_id FROM inbox_messages
                   WHERE event_id IN (
                     'selector', 'selector-follower',
                     'external-head', 'external-follower'
                   )"""
            ).fetchall()
        }
    assert games == {
        "selector": "selected",
        "selector-follower": "selected",
        "external-head": None,
        "external-follower": None,
    }


def test_completion_game_mismatch_preserves_live_binding_and_fences_result(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("selected", "world", GameLifecycle.PREPARING))
    store.create_game(GameState("other", "world", GameLifecycle.ACTIVE))
    store.enqueue(
        IncomingMessage.now(
            event_id="selector",
            channel_id="channel",
            author_id="alice",
            content="choose",
        )
    )
    store.claim_pending(channel_id="channel", limit=1)
    store.bind_channel(channel_id="channel", game_id="other")

    store.complete_batch(
        event_ids=["selector"],
        channel_id="channel",
        contents=["stale selection result"],
        idempotency_key="selector",
        assistant_turns=[("selector", "alice", "stale selection result")],
        completion_game_id="selected",
    )

    assert store.channel_state("channel").game_id == "other"
    notices = store.pending_outbox(channel_id="channel")
    assert len(notices) == 1
    assert notices[0]["kind"] == "system_notice"
    with store.connect() as connection:
        inbox = connection.execute(
            "SELECT game_id, status FROM inbox_messages WHERE event_id = 'selector'"
        ).fetchone()
        assistant_count = connection.execute(
            """SELECT COUNT(*) AS count FROM assistant_responses
               WHERE source_event_id = 'selector'"""
        ).fetchone()["count"]
    assert dict(inbox) == {"game_id": None, "status": "processed"}
    assert assistant_count == 0


def test_complete_batch_rejects_cross_channel_and_cross_game_sources(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    for channel in ("one", "two"):
        store.enqueue(
            IncomingMessage.now(
                event_id=f"event-{channel}",
                channel_id=channel,
                author_id="alice",
                content=channel,
            )
        )
        store.claim_pending(channel_id=channel, limit=1)

    with pytest.raises(ValueError, match="cross channels"):
        store.complete_batch(
            event_ids=["event-one", "event-two"],
            channel_id="one",
            contents=[],
            idempotency_key="cross-channel",
        )

    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game-one", "world", GameLifecycle.ACTIVE))
    store.create_game(GameState("game-two", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="game-channel", game_id="game-one")
    for event_id in ("game-event-one", "game-event-two"):
        store.enqueue(
            IncomingMessage.now(
                event_id=event_id,
                channel_id="game-channel",
                author_id="alice",
                content=event_id,
            )
        )
    store.claim_pending(channel_id="game-channel", limit=2)
    with store.transaction() as connection:
        connection.execute(
            """UPDATE inbox_messages SET game_id = 'game-two'
               WHERE event_id = 'game-event-two'"""
        )

    with pytest.raises(ValueError, match="cross games"):
        store.complete_batch(
            event_ids=["game-event-one", "game-event-two"],
            channel_id="game-channel",
            contents=[],
            idempotency_key="cross-game",
        )


def test_complete_batch_rejects_unknown_payload_source(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.enqueue(
        IncomingMessage.now(
            event_id="event",
            channel_id="channel",
            author_id="alice",
            content="hello",
        )
    )
    store.claim_pending(channel_id="channel", limit=1)

    with pytest.raises(ValueError, match="must reference this inbox batch"):
        store.complete_batch(
            event_ids=["event"],
            channel_id="channel",
            contents=[],
            idempotency_key="unknown-source",
            payloads=[("answer", None, "other-event", "alice")],
        )
    assert store.processing_inbox(channel_id="channel")


def test_unavailable_discord_channel_uses_durable_bounded_inbox_backoff(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.enqueue(
        IncomingMessage.now(
            event_id="event", channel_id="missing", author_id="alice", content="hello"
        )
    )

    delays = []
    for _ in range(4):
        delays.append(
            store.defer_pending_channel(channel_id="missing", error="DiscordChannelUnavailable")
        )
        assert store.pending_inbox_channels() == []
        assert store.pending_inbox_schedule()[0][0] == "missing"
        with store.transaction() as connection:
            connection.execute(
                """UPDATE inbox_messages
                   SET next_attempt_at = datetime('now', '-1 second')
                   WHERE event_id = 'event'"""
            )

    assert delays == [5, 30, 120, 300]
    assert (
        store.defer_pending_channel(channel_id="missing", error="DiscordChannelUnavailable") is None
    )
    assert store.pending_inbox_schedule() == []
    assert store.failed_inbox()[0]["error"] == "DiscordChannelUnavailable"


def test_provider_backoff_preserves_fifo_and_survives_a_five_minute_outage(
    tmp_path,
) -> None:
    store = SQLiteStore(tmp_path / "provider-backoff.sqlite3")
    store.initialize()
    received_at = datetime(2026, 1, 1, tzinfo=UTC)
    for offset, event_id in enumerate(("provider-head", "newer-ready")):
        assert store.enqueue(
            IncomingMessage(
                event_id=event_id,
                channel_id="channel",
                author_id="alice",
                content=event_id,
                created_at=received_at + timedelta(seconds=offset),
            )
        )

    expected_delays = (5, 30, 120, 300)
    for provider_attempt, expected_delay in enumerate(expected_delays, start=1):
        claimed = store.claim_pending(channel_id="channel", limit=1)
        assert [message.event_id for message in claimed] == ["provider-head"]
        assert (
            store.fail_batch(
                event_ids=["provider-head"],
                error="TransientProviderError",
                provider_transient=True,
            )
            is False
        )
        assert store.pending_inbox_channels() == []
        scheduled_channel, delay = store.pending_inbox_schedule()[0]
        assert scheduled_channel == "channel"
        assert expected_delay - 1 <= delay <= expected_delay
        with store.connect() as connection:
            before_available = connection.execute(
                """SELECT provider_attempts, next_attempt_at, status
                   FROM inbox_messages WHERE event_id = 'provider-head'"""
            ).fetchone()
        assert dict(before_available) == {
            "provider_attempts": provider_attempt,
            "next_attempt_at": before_available["next_attempt_at"],
            "status": "pending",
        }
        assert before_available["next_attempt_at"] is not None

        store.mark_channel_available("channel")
        with store.connect() as connection:
            after_available = connection.execute(
                """SELECT provider_attempts, next_attempt_at, status
                   FROM inbox_messages WHERE event_id = 'provider-head'"""
            ).fetchone()
        assert dict(after_available) == dict(before_available)
        assert store.claim_pending(channel_id="channel", limit=1) == []

        if provider_attempt < len(expected_delays):
            with store.transaction() as connection:
                connection.execute(
                    """UPDATE inbox_messages
                       SET next_attempt_at = datetime('now', '-1 second')
                       WHERE event_id = 'provider-head'"""
                )

    # The fourth transient failure is still pending and wakes after five minutes rather
    # than becoming a terminal failure around the old three-attempt boundary.
    assert store.failed_inbox() == []
    with store.connect() as connection:
        head = connection.execute(
            """SELECT status, attempts, provider_attempts, next_attempt_at
               FROM inbox_messages WHERE event_id = 'provider-head'"""
        ).fetchone()
        follower = connection.execute(
            """SELECT status, attempts, provider_attempts, next_attempt_at
               FROM inbox_messages WHERE event_id = 'newer-ready'"""
        ).fetchone()
    assert head["status"] == "pending"
    assert head["attempts"] == 4
    assert head["provider_attempts"] == 4
    assert head["next_attempt_at"] is not None
    assert dict(follower) == {
        "status": "pending",
        "attempts": 0,
        "provider_attempts": 0,
        "next_attempt_at": None,
    }


def test_thread_inherits_parent_monitoring_and_game_binding(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.record_discord_channel(
        channel_id="parent", guild_id="guild", parent_channel_id=None, kind="text"
    )
    store.record_discord_channel(
        channel_id="thread", guild_id="guild", parent_channel_id="parent", kind="thread"
    )
    store.enable_channel_monitoring("parent")
    store.bind_channel(channel_id="parent", game_id="game")

    assert store.inherit_thread_context(
        channel_id="thread", parent_channel_id="parent", guild_id="guild"
    )
    assert store.channel_monitoring_enabled("thread")
    assert store.channel_state("thread").game_id == "game"

    with pytest.raises(ValueError, match="different Discord servers"):
        store.inherit_thread_context(
            channel_id="other-thread", parent_channel_id="parent", guild_id="other-guild"
        )


def test_parent_bind_materializes_an_inherited_unbound_thread_immediately(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.record_discord_channel(
        channel_id="parent",
        guild_id="guild",
        parent_channel_id=None,
        kind="text",
    )
    store.record_discord_channel(
        channel_id="thread",
        guild_id="guild",
        parent_channel_id="parent",
        kind="thread",
    )
    store.enable_channel_monitoring("parent")
    assert store.inherit_thread_context(
        channel_id="thread",
        parent_channel_id="parent",
        guild_id="guild",
    )
    assert store.channel_state("thread").game_id is None

    store.bind_channel(channel_id="parent", game_id="game")

    assert store.channel_state("thread").game_id == "game"


def test_thread_binding_reconciles_parent_unbind_and_rebind(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world-one", "World One"))
    store.create_world(WorldState("world-two", "World Two"))
    store.create_game(GameState("game-one", "world-one", GameLifecycle.ACTIVE))
    store.create_game(GameState("game-two", "world-two", GameLifecycle.ACTIVE))
    store.record_discord_channel(
        channel_id="parent", guild_id="guild", parent_channel_id=None, kind="text"
    )
    store.record_discord_channel(
        channel_id="thread", guild_id="guild", parent_channel_id="parent", kind="thread"
    )
    store.enable_channel_monitoring("parent")
    store.bind_channel(channel_id="parent", game_id="game-one")
    store.inherit_thread_context(channel_id="thread", parent_channel_id="parent", guild_id="guild")
    assert store.channel_state("thread").game_id == "game-one"
    store.start_activity_clock(
        game_id="game-one",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    store.put_pending(
        PendingInteraction(
            "thread-choice",
            "game-one",
            "alice",
            None,
            PendingKind.CHOICE,
            "Choose",
            origin_channel_id="thread",
        )
    )

    store.unbind_channel(channel_id="parent", expected_game_id="game-one")
    assert store.channel_state("thread").game_id is None
    closed = store.pending_by_id("thread-choice")
    assert closed.status is PendingStatus.CANCELLED
    assert closed.payload["closed_reason"] == "origin_channel_unbound"
    assert store.activity_state("game-one")["last_event_at"] is None

    store.bind_channel(channel_id="parent", game_id="game-two")
    store.inherit_thread_context(channel_id="thread", parent_channel_id="parent", guild_id="guild")
    assert store.channel_state("thread").game_id == "game-two"


def test_parent_rebind_cleans_old_game_as_inherited_thread_moves(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world-one", "World One"))
    store.create_world(WorldState("world-two", "World Two"))
    store.create_game(GameState("game-one", "world-one", GameLifecycle.ACTIVE))
    store.create_game(GameState("game-two", "world-two", GameLifecycle.ACTIVE))
    store.record_discord_channel(
        channel_id="parent",
        guild_id="guild",
        parent_channel_id=None,
        kind="text",
    )
    store.record_discord_channel(
        channel_id="thread",
        guild_id="guild",
        parent_channel_id="parent",
        kind="thread",
    )
    store.enable_channel_monitoring("parent")
    store.bind_channel(channel_id="parent", game_id="game-one")
    store.inherit_thread_context(
        channel_id="thread",
        parent_channel_id="parent",
        guild_id="guild",
    )
    store.start_activity_clock(
        game_id="game-one",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    for interaction_id, player_id, origin in (
        ("parent-choice", "alice", "parent"),
        ("thread-choice", "bob", "thread"),
        ("legacy-choice", "carol", None),
    ):
        store.put_pending(
            PendingInteraction(
                interaction_id,
                "game-one",
                player_id,
                None,
                PendingKind.CHOICE,
                "Choose",
                origin_channel_id=origin,
            )
        )

    store.bind_channel(channel_id="parent", game_id="game-two")

    assert store.pending_by_id("parent-choice").payload["closed_reason"] == (
        "origin_channel_unbound"
    )
    assert store.channel_state("parent").game_id == "game-two"
    assert store.channel_state("thread").game_id == "game-two"
    assert store.pending_by_id("thread-choice").payload["closed_reason"] == (
        "origin_channel_unbound"
    )
    assert store.pending_by_id("legacy-choice").payload["closed_reason"] == ("last_channel_unbound")
    assert store.activity_state("game-one")["last_event_at"] is None


def test_explicit_thread_binding_is_not_overwritten_by_parent_inheritance(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world-one", "World One"))
    store.create_world(WorldState("world-two", "World Two"))
    store.create_game(GameState("game-one", "world-one", GameLifecycle.ACTIVE))
    store.create_game(GameState("game-two", "world-two", GameLifecycle.ACTIVE))
    store.record_discord_channel(
        channel_id="parent", guild_id="guild", parent_channel_id=None, kind="text"
    )
    store.record_discord_channel(
        channel_id="thread", guild_id="guild", parent_channel_id="parent", kind="thread"
    )
    store.enable_channel_monitoring("parent")
    store.bind_channel(channel_id="parent", game_id="game-one")
    store.bind_channel(channel_id="thread", game_id="game-two")

    assert store.inherit_thread_context(
        channel_id="thread",
        parent_channel_id="parent",
        guild_id="guild",
    )
    assert store.channel_monitoring_enabled("thread")
    assert store.channel_state("thread").game_id == "game-two"


def test_disabling_parent_cleans_only_inherited_thread_context(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world-one", "World One"))
    store.create_world(WorldState("world-two", "World Two"))
    store.create_game(GameState("game-one", "world-one", GameLifecycle.ACTIVE))
    store.create_game(GameState("game-two", "world-two", GameLifecycle.ACTIVE))
    store.record_discord_channel(
        channel_id="parent", guild_id="guild", parent_channel_id=None, kind="text"
    )
    for channel_id in ("inherited-thread", "explicit-thread"):
        store.record_discord_channel(
            channel_id=channel_id,
            guild_id="guild",
            parent_channel_id="parent",
            kind="thread",
        )
    store.enable_channel_monitoring("parent")
    store.bind_channel(channel_id="parent", game_id="game-one")
    for channel_id in ("inherited-thread", "explicit-thread"):
        assert store.inherit_thread_context(
            channel_id=channel_id,
            parent_channel_id="parent",
            guild_id="guild",
        )

    # Direct user actions turn the second thread's inherited rows into explicit ownership.
    store.enable_channel_monitoring("explicit-thread")
    store.bind_channel(channel_id="explicit-thread", game_id="game-two")
    assert store.disable_channel_monitoring("parent")
    assert not store.channel_monitoring_enabled("inherited-thread")
    assert store.channel_state("inherited-thread").game_id is None
    assert store.channel_monitoring_enabled("explicit-thread")
    assert store.channel_state("explicit-thread").game_id == "game-two"

    for channel_id in ("inherited-thread", "explicit-thread"):
        assert not store.inherit_thread_context(
            channel_id=channel_id,
            parent_channel_id="parent",
            guild_id="guild",
        )

    assert not store.channel_monitoring_enabled("inherited-thread")
    assert store.channel_state("inherited-thread").game_id is None
    assert store.channel_monitoring_enabled("explicit-thread")
    assert store.channel_state("explicit-thread").game_id == "game-two"


def test_narrative_channel_rejects_non_messageable_discord_kinds(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.PREPARING))
    store.record_discord_channel(
        channel_id="game-channel", guild_id="guild", parent_channel_id=None, kind="text"
    )
    for channel_id, kind in (
        ("voice", "voice"),
        ("category", "category"),
        ("forum", "forum"),
    ):
        store.record_discord_channel(
            channel_id=channel_id,
            guild_id="guild",
            parent_channel_id=None,
            kind=kind,
        )
    store.record_discord_channel(
        channel_id="thread",
        guild_id="guild",
        parent_channel_id="game-channel",
        kind="thread",
    )
    store.record_discord_channel(
        channel_id="public-thread",
        guild_id="guild",
        parent_channel_id="game-channel",
        kind="public_thread",
    )
    store.bind_channel(channel_id="game-channel", game_id="game")

    for channel_id in ("voice", "category", "forum"):
        with pytest.raises(ValueError, match="not messageable"):
            store.set_narrative_channel(
                game_id="game",
                channel_id=channel_id,
                expected_revision=0,
            )

    store.set_narrative_channel(game_id="game", channel_id="thread", expected_revision=0)
    store.set_narrative_channel(game_id="game", channel_id="public-thread", expected_revision=1)
    assert store.game_state("game").narrative_channel_id == "public-thread"


def test_narrative_channel_must_share_discord_guild_and_game_session(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_world(WorldState("other-world", "Other"))
    store.create_game(GameState("game", "world", GameLifecycle.PREPARING))
    store.create_game(GameState("other-game", "other-world", GameLifecycle.PREPARING))
    store.record_discord_channel(
        channel_id="game-channel", guild_id="guild", parent_channel_id=None, kind="text"
    )
    store.record_discord_channel(
        channel_id="narrative", guild_id="guild", parent_channel_id=None, kind="text"
    )
    store.record_discord_channel(
        channel_id="cross-guild", guild_id="other", parent_channel_id=None, kind="text"
    )
    store.record_discord_channel(
        channel_id="dm", guild_id=None, parent_channel_id=None, kind="private"
    )
    store.record_discord_channel(
        channel_id="other-game-channel", guild_id="guild", parent_channel_id=None, kind="text"
    )
    store.bind_channel(channel_id="game-channel", game_id="game")
    store.bind_channel(channel_id="other-game-channel", game_id="other-game")

    with pytest.raises(ValueError, match="same Discord server"):
        store.set_narrative_channel(game_id="game", channel_id="cross-guild", expected_revision=0)
    with pytest.raises(ValueError, match="direct messages"):
        store.set_narrative_channel(game_id="game", channel_id="dm", expected_revision=0)
    with pytest.raises(ValueError, match="another game"):
        store.set_narrative_channel(
            game_id="game", channel_id="other-game-channel", expected_revision=0
        )

    store.set_narrative_channel(game_id="game", channel_id="narrative", expected_revision=0)
    assert store.game_state("game").narrative_channel_id == "narrative"
    with pytest.raises(ValueError, match="assigned to another game"):
        store.set_narrative_channel(
            game_id="other-game", channel_id="narrative", expected_revision=0
        )
