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
        completion_game_id: str | None = None
        for message in messages:
            result = await handler(message)
            partial = self.from_result(message, result)
            responses.extend(partial.items)
            deliveries.extend(partial.deliveries)
            if partial.completion_game_id is not None:
                if (
                    completion_game_id is not None
                    and completion_game_id != partial.completion_game_id
                ):
                    raise ValueError("a response batch cannot complete multiple games")
                completion_game_id = partial.completion_game_id
        return ResponseBatch(
            channel_id,
            tuple(responses),
            tuple(deliveries),
            completion_game_id,
        )

    @staticmethod
    def from_result(
        message: IncomingMessage,
        result: str | HandlerResponse,
    ) -> ResponseBatch:
        if isinstance(result, HandlerResponse):
            text = result.text
            deliveries = result.deliveries
            completion_game_id = result.completion_game_id
        else:
            text = result
            deliveries = ()
            completion_game_id = None
        items = (
            (
                AddressedResponse(
                    message.author_id,
                    text.strip(),
                    source_event_id=message.event_id,
                ),
            )
            if text.strip()
            else ()
        )
        return ResponseBatch(
            message.channel_id,
            items,
            deliveries,
            completion_game_id,
        )
