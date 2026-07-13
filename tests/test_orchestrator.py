import asyncio

from masterclaw.app.orchestrator import ChannelOrchestrator
from masterclaw.domain.models import IncomingMessage
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
    assert len(outbox) == 1
    assert "<@a> none" in outbox[0]["content"]
    assert "<@b> one" in outbox[0]["content"]


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
        raise RuntimeError("broken")

    orchestrator = ChannelOrchestrator(store, handler)
    for _ in range(3):
        try:
            asyncio.run(orchestrator.process_available("c"))
        except RuntimeError:
            pass
    assert store.pending(channel_id="c") == []
    assert store.failed_inbox()[0]["event_id"] == "1"
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
    assert len(store.pending_outbox(channel_id="c")) == 1
