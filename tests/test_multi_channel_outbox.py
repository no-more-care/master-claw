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


def test_outbox_delivery_is_claimed_only_once(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.enqueue(
        IncomingMessage.now(event_id="1", channel_id="game", author_id="alice", content="hello")
    )

    async def handler(_):
        return "response"

    asyncio.run(ChannelOrchestrator(store, handler).process_available("game"))
    assert len(store.claim_outbox()) == 1
    assert store.claim_outbox() == []


def test_outbox_claims_fifo_heads_per_channel_and_respects_retry_delay(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.queue_system_notice(channel_id="a", key="a-1", content="a first")
    store.queue_system_notice(channel_id="a", key="a-2", content="a second")
    store.queue_system_notice(channel_id="b", key="b-1", content="b first")

    claimed = store.claim_outbox()
    assert [row["content"] for row in claimed] == ["a first", "b first"]
    first_a = claimed[0]["id"]
    first_b = claimed[1]["id"]
    store.mark_delivery_failed(first_a, "NetworkError")
    store.mark_delivered(first_b)

    assert store.claim_outbox() == []
    assert store.pending_outbox(channel_id="a")[0]["next_attempt_at"] is not None

    store.queue_system_notice(channel_id="c", key="c-1", content="c first")
    other_channel = store.claim_outbox()
    assert [row["content"] for row in other_channel] == ["c first"]
    store.mark_delivered(other_channel[0]["id"])

    with store.transaction() as connection:
        connection.execute(
            "UPDATE outbox_messages SET next_attempt_at = datetime('now', '-1 second') "
            "WHERE id = ?",
            (first_a,),
        )
    retried = store.claim_outbox()
    assert [row["content"] for row in retried] == ["a first"]
    store.mark_delivered(retried[0]["id"])
    assert [row["content"] for row in store.claim_outbox()] == ["a second"]


def test_outbox_claims_can_all_be_released_for_immediate_retry(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.queue_system_notice(channel_id="a", key="a", content="a")
    store.queue_system_notice(channel_id="b", key="b", content="b")

    claimed = store.claim_outbox()
    claimed_ids = [row["id"] for row in claimed]
    assert store.release_outbox_claims(claimed_ids) == 2

    assert [row["id"] for row in store.claim_outbox()] == claimed_ids


def test_terminal_failure_unblocks_next_message_in_same_channel(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.queue_system_notice(channel_id="a", key="a-1", content="head")
    store.queue_system_notice(channel_id="a", key="a-2", content="tail")

    head = store.claim_outbox()[0]
    for _ in range(5):
        store.mark_delivery_failed(head["id"], "NetworkError")

    assert [row["content"] for row in store.claim_outbox()] == ["tail"]
