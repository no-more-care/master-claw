import asyncio
from datetime import UTC, datetime, timedelta

from masterclaw.app.batching import SequentialBatchProcessor
from masterclaw.domain.models import IncomingMessage


def message(event_id: str, author_id: str, content: str, offset: int) -> IncomingMessage:
    return IncomingMessage(
        event_id=event_id,
        channel_id="channel",
        author_id=author_id,
        content=content,
        created_at=datetime.now(UTC) + timedelta(seconds=offset),
    )


def test_messages_are_processed_sequentially_and_rendered_together() -> None:
    state: list[str] = []

    async def handler(incoming: IncomingMessage) -> str:
        seen = ",".join(state) or "nothing"
        state.append(incoming.content)
        return f"Before this message I saw: {seen}."

    batch = asyncio.run(
        SequentialBatchProcessor().process(
            [message("1", "alice", "first", 0), message("2", "bob", "second", 1)],
            handler,
        )
    )

    assert batch.items[1].text.endswith("first.")
    assert batch.render_discord() == (
        "Before this message I saw: nothing.\n\nBefore this message I saw: first."
    )


def test_batch_cannot_span_channels() -> None:
    first = message("1", "alice", "first", 0)
    second = IncomingMessage.now(
        event_id="2", channel_id="other", author_id="bob", content="second"
    )

    async def handler(_: IncomingMessage) -> str:
        return "ok"

    try:
        asyncio.run(SequentialBatchProcessor().process([first, second], handler))
    except ValueError as error:
        assert "multiple channels" in str(error)
    else:
        raise AssertionError("expected ValueError")
