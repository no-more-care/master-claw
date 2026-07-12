from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from masterclaw.domain.models import (
    AddressedResponse,
    HandlerResponse,
    IncomingMessage,
    OutboundDelivery,
    ResponseBatch,
)

MessageHandler = Callable[[IncomingMessage], Awaitable[str | HandlerResponse]]


class SequentialBatchProcessor:
    """Processes a channel batch in order and renders one addressed response block.

    The handler is invoked separately for every message, so it can reload the
    pending interaction and state created by the preceding message.
    """

    async def process(
        self, messages: Sequence[IncomingMessage], handler: MessageHandler
    ) -> ResponseBatch:
        if not messages:
            raise ValueError("cannot process an empty message batch")
        channel_id = messages[0].channel_id
        if any(message.channel_id != channel_id for message in messages):
            raise ValueError("a response batch cannot span multiple channels")

        responses: list[AddressedResponse] = []
        deliveries: list[OutboundDelivery] = []
        for message in messages:
            result = await handler(message)
            if isinstance(result, HandlerResponse):
                text = result.text
                deliveries.extend(result.deliveries)
            else:
                text = result
            if text.strip():
                responses.append(AddressedResponse(message.author_id, text.strip()))
        return ResponseBatch(channel_id, tuple(responses), tuple(deliveries))
