import sqlite3

import pytest

from masterclaw.domain.models import GameLifecycle, IncomingMessage
from masterclaw.domain.state import GameState, ReserveRecoveryMode, WorldState
from masterclaw.storage.sqlite import SQLiteStore


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


def test_legacy_world_workspaces_migrate_once_without_data_loss(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "legacy.sqlite3")
    _create_version_two_world_workspace_database(
        store,
        [("channel", "world", "drafting", "Brief", '{"tone":"dark"}', "{}", 4)],
    )

    store.initialize()
    store.initialize()

    assert store.schema_version() == 3
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
    assert versions == [2, 3]


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
