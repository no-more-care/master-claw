import asyncio

from masterclaw.app.orchestrator import ChannelOrchestrator
from masterclaw.domain.models import IncomingMessage
from masterclaw.pipelines.base import PipelineValidationError
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
    assert "ничего повторять не нужно" in notice[0]["content"]
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
