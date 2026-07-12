import asyncio

from masterclaw.app.orchestrator import ChannelOrchestrator
from masterclaw.domain.models import HandlerResponse, IncomingMessage, OutboundDelivery
from masterclaw.storage.sqlite import SQLiteStore


def test_game_and_narrative_deliveries_commit_with_same_inbox_batch(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.enqueue(
        IncomingMessage.now(
            event_id="1", channel_id="game-channel", author_id="alice", content="confirm"
        )
    )

    async def handler(_):
        return HandlerResponse(
            "Mechanical result",
            (OutboundDelivery("narrative-channel", "Fictional outcome", "narrative"),),
        )

    asyncio.run(ChannelOrchestrator(store, handler).process_available("game-channel"))
    game = store.pending_outbox(channel_id="game-channel")
    narrative = store.pending_outbox(channel_id="narrative-channel")
    assert len(game) == 1 and "Mechanical result" in game[0]["content"]
    assert len(narrative) == 1 and narrative[0]["content"] == "Fictional outcome"


def test_outbox_stops_automatic_retry_and_can_be_requeued(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.enqueue(
        IncomingMessage.now(event_id="1", channel_id="game", author_id="alice", content="hello")
    )

    async def handler(_):
        return "response"

    asyncio.run(ChannelOrchestrator(store, handler).process_available("game"))
    outbox_id = store.pending_outbox()[0]["id"]
    for _ in range(5):
        store.mark_delivery_failed(outbox_id, "network")
    assert store.pending_outbox() == []
    assert store.failed_outbox()[0]["id"] == outbox_id
    store.requeue_failed_outbox(outbox_id)
    assert store.pending_outbox()[0]["id"] == outbox_id
