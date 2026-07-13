from masterclaw.domain.models import IncomingMessage
from masterclaw.storage.sqlite import SQLiteStore


def test_inbox_is_durable_ordered_and_idempotent(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "masterclaw.sqlite3")
    store.initialize()
    assert store.schema_version() == 2
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


def test_migration_runner_is_idempotent_and_rejects_newer_schema(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.initialize()
    assert store.schema_version() == 2
    with store.transaction() as connection:
        connection.execute("INSERT INTO schema_version(version) VALUES (3)")
    import pytest

    with pytest.raises(RuntimeError, match="unsupported database schema"):
        store.initialize()


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
        model="openrouter/x-ai/grok-4.5",
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
