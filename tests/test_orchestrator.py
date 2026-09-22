import asyncio

import pytest

from masterclaw.app.orchestrator import ChannelOrchestrator
from masterclaw.domain.models import (
    GameLifecycle,
    HandlerResponse,
    IncomingMessage,
    OutboundDelivery,
)
from masterclaw.domain.state import GameState, WorldState
from masterclaw.pipelines.base import (
    DeterministicProviderError,
    PipelineValidationError,
    TransientProviderError,
)
from masterclaw.storage.sqlite import SQLiteStore


def test_orchestrator_claims_completes_and_creates_one_outbox_message(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.enqueue(IncomingMessage.now(event_id="1", channel_id="c", author_id="a", content="one"))
    store.enqueue(IncomingMessage.now(event_id="2", channel_id="c", author_id="b", content="two"))
    seen: list[str] = []

    async def handler(message: IncomingMessage) -> str:
        prior = ",".join(seen) or "none"
        seen.append(message.content)
        return prior

    result = asyncio.run(ChannelOrchestrator(store, handler).process_available("c"))
    assert result is not None
    assert result.items[1].text == "one"
    assert store.pending(channel_id="c") == []
    outbox = store.pending_outbox(channel_id="c")
    assert len(outbox) == 2
    assert outbox[0]["content"] == "none"
    assert outbox[1]["content"] == "one"
    assert "<@" not in "".join(row["content"] for row in outbox)


def test_unbound_ordinary_turn_does_not_adopt_a_concurrent_live_binding(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.enqueue(
        IncomingMessage.now(
            event_id="ordinary",
            channel_id="channel",
            author_id="alice",
            content="ordinary unbound request",
        )
    )

    async def handler(_: IncomingMessage) -> str:
        store.bind_channel(channel_id="channel", game_id="game")
        return "ordinary response"

    result = asyncio.run(ChannelOrchestrator(store, handler).process_available("channel"))

    assert result is not None
    assert result.completion_game_id is None
    with store.connect() as connection:
        row = connection.execute(
            "SELECT game_id FROM inbox_messages WHERE event_id = 'ordinary'"
        ).fetchone()
    assert row["game_id"] is None


def test_explicit_completion_game_metadata_attaches_successful_selector(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.PREPARING))
    store.enqueue(
        IncomingMessage.now(
            event_id="selector",
            channel_id="channel",
            author_id="alice",
            content="choose World",
        )
    )

    async def handler(_: IncomingMessage) -> HandlerResponse:
        store.bind_channel(channel_id="channel", game_id="game")
        return HandlerResponse("selected", completion_game_id="game")

    result = asyncio.run(ChannelOrchestrator(store, handler).process_available("channel"))

    assert result is not None
    assert result.completion_game_id is None
    with store.connect() as connection:
        row = connection.execute(
            "SELECT game_id FROM inbox_messages WHERE event_id = 'selector'"
        ).fetchone()
    assert row["game_id"] == "game"


def test_one_drain_can_complete_two_fifo_world_selections(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world-x", "World X"))
    store.create_world(WorldState("world-y", "World Y"))
    store.create_game(GameState("game-x", "world-x", GameLifecycle.PREPARING))
    store.create_game(GameState("game-y", "world-y", GameLifecycle.PREPARING))
    for event_id in ("select-x", "new-x", "select-y"):
        store.enqueue(
            IncomingMessage.now(
                event_id=event_id,
                channel_id="channel",
                author_id="alice",
                content=event_id,
            )
        )

    async def handler(message: IncomingMessage) -> str | HandlerResponse:
        if message.event_id == "select-x":
            store.bind_channel(channel_id="channel", game_id="game-x")
            return HandlerResponse("selected x", completion_game_id="game-x")
        if message.event_id == "new-x":
            store.unbind_channel(channel_id="channel", expected_game_id="game-x")
            return "unbound x"
        store.bind_channel(channel_id="channel", game_id="game-y")
        return HandlerResponse("selected y", completion_game_id="game-y")

    result = asyncio.run(ChannelOrchestrator(store, handler).process_available("channel"))

    assert result is not None
    assert result.completion_game_id is None
    assert store.channel_state("channel").game_id == "game-y"
    with store.connect() as connection:
        rows = {
            row["event_id"]: row["game_id"]
            for row in connection.execute(
                """SELECT event_id, game_id FROM inbox_messages
                   WHERE event_id IN ('select-x', 'new-x', 'select-y')"""
            ).fetchall()
        }
    assert rows == {
        "select-x": "game-x",
        "new-x": "game-x",
        "select-y": "game-y",
    }


def test_orchestrator_requeues_batch_after_handler_error(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.enqueue(IncomingMessage.now(event_id="1", channel_id="c", author_id="a", content="one"))

    async def handler(_: IncomingMessage) -> str:
        raise RuntimeError("broken")

    try:
        asyncio.run(ChannelOrchestrator(store, handler).process_available("c"))
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError")
    assert [item.event_id for item in store.pending(channel_id="c")] == ["1"]


def test_repeated_handler_failure_moves_message_to_dead_letter_state(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.enqueue(IncomingMessage.now(event_id="1", channel_id="c", author_id="a", content="one"))

    async def handler(_: IncomingMessage) -> str:
        raise RuntimeError("broken with sk-live-secret")

    orchestrator = ChannelOrchestrator(store, handler)
    for _ in range(3):
        try:
            asyncio.run(orchestrator.process_available("c"))
        except RuntimeError:
            pass
    assert store.pending(channel_id="c") == []
    failed = store.failed_inbox()[0]
    assert failed["event_id"] == "1"
    assert failed["error"] == "RuntimeError"
    assert "secret" not in str(failed["error"])
    notice = store.pending_outbox(channel_id="c")
    assert len(notice) == 1
    assert "do not repeat it" in notice[0]["content"]
    store.requeue_failed_inbox("1")
    assert [item.event_id for item in store.pending(channel_id="c")] == ["1"]


def test_startup_recovery_returns_processing_messages_to_queue(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.enqueue(IncomingMessage.now(event_id="1", channel_id="c", author_id="a", content="one"))
    assert [item.event_id for item in store.claim_pending(channel_id="c")] == ["1"]
    assert store.pending(channel_id="c") == []
    assert store.recover_interrupted_work() == 1
    assert [item.event_id for item in store.pending(channel_id="c")] == ["1"]


def test_typed_validation_failure_is_not_retried_as_a_whole_batch(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.enqueue(IncomingMessage.now(event_id="1", channel_id="c", author_id="a", content="one"))

    async def handler(_: IncomingMessage) -> str:
        raise PipelineValidationError("bounded pipeline already exhausted repair")

    try:
        asyncio.run(ChannelOrchestrator(store, handler).process_available("c"))
    except PipelineValidationError:
        pass
    else:
        raise AssertionError("expected PipelineValidationError")

    assert store.pending(channel_id="c") == []
    assert store.failed_inbox()[0]["attempts"] == 1
    assert len(store.pending_outbox(channel_id="c")) == 1


def test_until_quiet_combines_messages_arriving_during_processing(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.enqueue(IncomingMessage.now(event_id="1", channel_id="c", author_id="a", content="one"))
    seen: list[str] = []

    async def handler(message: IncomingMessage) -> str:
        seen.append(message.content)
        if message.event_id == "1":
            store.enqueue(
                IncomingMessage.now(event_id="2", channel_id="c", author_id="b", content="answer")
            )
        return "/".join(seen)

    result = asyncio.run(
        ChannelOrchestrator(store, handler).process_until_quiet("c", quiet_seconds=0)
    )
    assert result is not None
    assert [item.author_id for item in result.items] == ["a", "b"]
    assert len(store.pending_outbox(channel_id="c")) == 2


def test_status_panel_is_persisted_as_colored_embed(tmp_path) -> None:
    import json

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.enqueue(IncomingMessage.now(event_id="1", channel_id="c", author_id="a", content="one"))

    async def handler(_: IncomingMessage) -> str:
        return "## 🎭 ИГРА · Мир\n**Режим:** игра\n\nРезультат действия"

    asyncio.run(ChannelOrchestrator(store, handler).process_available("c"))
    row = store.pending_outbox(channel_id="c")[0]
    embed = json.loads(row["embed_json"])
    assert row["content"] == "Результат действия"
    assert embed["title"] == "🎭 ИГРА · Мир"
    assert embed["color"] == 0x57F287


def test_handler_failure_is_isolated_from_other_messages_in_claim(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    for event_id in ("1", "2", "3"):
        store.enqueue(
            IncomingMessage.now(event_id=event_id, channel_id="c", author_id="a", content=event_id)
        )

    async def handler(message: IncomingMessage) -> str:
        if message.event_id == "2":
            raise RuntimeError("only this message is broken")
        return f"ok:{message.event_id}"

    try:
        asyncio.run(ChannelOrchestrator(store, handler).process_available("c"))
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected isolated handler failure")

    assert [row["content"] for row in store.pending_outbox(channel_id="c")] == ["ok:1"]
    assert [message.event_id for message in store.pending(channel_id="c")] == ["2", "3"]


def test_poison_message_does_not_consume_attempts_of_unprocessed_followers(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    for event_id in ("1", "2", "3"):
        store.enqueue(
            IncomingMessage.now(event_id=event_id, channel_id="c", author_id="a", content=event_id)
        )
    seen: list[str] = []

    async def handler(message: IncomingMessage) -> str:
        seen.append(message.event_id)
        if message.event_id == "1":
            raise RuntimeError("poison")
        return f"ok:{message.event_id}"

    orchestrator = ChannelOrchestrator(store, handler)
    for _ in range(3):
        try:
            asyncio.run(orchestrator.process_available("c"))
        except RuntimeError:
            pass

    assert seen == ["1", "1", "1"]
    assert [(row["event_id"], row["attempts"]) for row in store.failed_inbox()] == [("1", 3)]
    assert [message.event_id for message in store.pending(channel_id="c")] == ["2", "3"]

    asyncio.run(orchestrator.process_available("c"))
    assert seen == ["1", "1", "1", "2", "3"]
    assert [row["content"] for row in store.pending_outbox(channel_id="c")][-2:] == [
        "ok:2",
        "ok:3",
    ]


def test_deterministic_provider_error_is_dead_lettered_without_replay(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.enqueue(IncomingMessage.now(event_id="1", channel_id="c", author_id="a", content="one"))
    calls = 0

    async def handler(_: IncomingMessage) -> str:
        nonlocal calls
        calls += 1
        raise DeterministicProviderError("request must change")

    try:
        asyncio.run(ChannelOrchestrator(store, handler).process_available("c"))
    except DeterministicProviderError:
        pass
    else:
        raise AssertionError("expected deterministic provider failure")

    assert calls == 1
    assert store.pending(channel_id="c") == []
    assert store.failed_inbox()[0]["attempts"] == 1


def test_transient_provider_outage_uses_durable_budget_then_succeeds(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "transient-provider.sqlite3")
    store.initialize()
    store.enqueue(IncomingMessage.now(event_id="1", channel_id="c", author_id="a", content="one"))
    calls = 0

    async def handler(_: IncomingMessage) -> str:
        nonlocal calls
        calls += 1
        if calls <= 4:
            raise TransientProviderError("temporary provider outage")
        return "provider recovered"

    orchestrator = ChannelOrchestrator(store, handler)
    for expected_provider_attempts in range(1, 5):
        with pytest.raises(TransientProviderError):
            asyncio.run(orchestrator.process_available("c"))
        assert store.failed_inbox() == []
        with store.connect() as connection:
            row = connection.execute(
                """SELECT status, attempts, provider_attempts, next_attempt_at
                   FROM inbox_messages WHERE event_id = '1'"""
            ).fetchone()
        assert row["status"] == "pending"
        assert row["attempts"] == expected_provider_attempts
        assert row["provider_attempts"] == expected_provider_attempts
        assert row["next_attempt_at"] is not None
        assert asyncio.run(orchestrator.process_available("c")) is None
        with store.transaction() as connection:
            connection.execute(
                """UPDATE inbox_messages
                   SET next_attempt_at = datetime('now', '-1 second')
                   WHERE event_id = '1'"""
            )

    recovered = asyncio.run(orchestrator.process_available("c"))

    assert recovered is not None
    assert calls == 5
    assert store.failed_inbox() == []
    assert [row["content"] for row in store.pending_outbox(channel_id="c")] == [
        "provider recovered"
    ]
    with store.connect() as connection:
        status = connection.execute(
            """SELECT status, attempts, provider_attempts, next_attempt_at
               FROM inbox_messages WHERE event_id = '1'"""
        ).fetchone()
    assert dict(status) == {
        "status": "processed",
        "attempts": 5,
        "provider_attempts": 4,
        "next_attempt_at": None,
    }


def test_followups_receive_the_previous_player_facing_answer_in_chat_context(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    messages = (
        ("1", "What is behind the sealed arch?"),
        ("2", "why?"),
        ("3", "Does the rule require two hits?"),
        ("4", "yes"),
    )
    for event_id, content in messages:
        store.enqueue(
            IncomingMessage.now(
                event_id=event_id,
                channel_id="c",
                author_id="alice",
                content=content,
            )
        )
    contexts = {}

    async def handler(message: IncomingMessage) -> str:
        if message.event_id == "1":
            return "The arch is sealed by the old observatory mechanism."
        if message.event_id == "3":
            return "Yes. This check requires two hits."
        contexts[message.event_id] = store.recent_chat_messages(channel_id="c", limit=2)
        return "Follow-up answered."

    asyncio.run(ChannelOrchestrator(store, handler).process_available("c"))

    assert [turn["role"] for turn in contexts["2"]] == ["user", "assistant"]
    assert "old observatory mechanism" in contexts["2"][-1]["content"]
    assert contexts["2"][-1]["recipient_author_id"] == "alice"
    assert [turn["role"] for turn in contexts["4"]] == ["user", "assistant"]
    assert "requires two hits" in contexts["4"][-1]["content"]
    outbox = store.pending_outbox(channel_id="c")
    assert [row["source_event_id"] for row in outbox] == ["1", "2", "3", "4"]
    assert all(row["source_author_id"] == "alice" for row in outbox)


def test_internal_deliveries_are_not_added_to_player_chat_history(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.enqueue(
        IncomingMessage.now(event_id="1", channel_id="game", author_id="alice", content="inspect")
    )

    async def handler(_message: IncomingMessage) -> HandlerResponse:
        return HandlerResponse(
            "You notice scratches on the lock.",
            (OutboundDelivery("gm-log", "SECRET: the lock is a mimic", "narrative"),),
        )

    asyncio.run(ChannelOrchestrator(store, handler).process_available("game"))
    history = store.recent_chat_messages(channel_id="game", limit=10)

    assert [turn["role"] for turn in history] == ["user", "assistant"]
    assert "scratches on the lock" in history[-1]["content"]
    assert "mimic" not in repr(history)
