from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable

from masterclaw.app.batching import SequentialBatchProcessor
from masterclaw.app.response_format import split_discord_message
from masterclaw.domain.models import (
    AddressedResponse,
    HandlerResponse,
    IncomingMessage,
    OutboundDelivery,
    ResponseBatch,
)
from masterclaw.storage.sqlite import SQLiteStore

ApplicationHandler = Callable[[IncomingMessage], Awaitable[str | HandlerResponse]]


class ChannelOrchestrator:
    """Durable, sequential processing boundary for a Discord channel."""

    def __init__(self, store: SQLiteStore, handler: ApplicationHandler) -> None:
        self._store = store
        self._handler = handler
        self._batcher = SequentialBatchProcessor()

    async def process_available(self, channel_id: str, *, limit: int = 50) -> ResponseBatch | None:
        messages = self._store.claim_pending(channel_id=channel_id, limit=limit)
        if not messages:
            return None
        event_ids = [message.event_id for message in messages]
        try:
            batch = await self._batcher.process(messages, self._handler)
            content = batch.render_discord()
            key = self._idempotency_key(channel_id, event_ids)
            self._store.complete_batch(
                event_ids=event_ids,
                channel_id=channel_id,
                contents=split_discord_message(content),
                idempotency_key=key,
                additional_deliveries=self._delivery_payloads(batch),
            )
            return batch
        except Exception as error:
            self._store.fail_batch(event_ids=event_ids, error=repr(error), retry=True)
            raise

    async def process_until_quiet(
        self,
        channel_id: str,
        *,
        quiet_seconds: float = 1.5,
        batch_limit: int = 50,
        max_batches: int = 10,
    ) -> ResponseBatch | None:
        """Include messages arriving during processing in one final outbox block."""
        all_event_ids: list[str] = []
        all_responses: list[AddressedResponse] = []
        all_deliveries: list[OutboundDelivery] = []
        try:
            for _ in range(max_batches):
                await asyncio.sleep(quiet_seconds)
                messages = self._store.claim_pending(channel_id=channel_id, limit=batch_limit)
                if not messages:
                    break
                all_event_ids.extend(message.event_id for message in messages)
                partial = await self._batcher.process(messages, self._handler)
                all_responses.extend(partial.items)
                all_deliveries.extend(partial.deliveries)
            if not all_event_ids:
                return None
            batch = ResponseBatch(channel_id, tuple(all_responses), tuple(all_deliveries))
            self._store.complete_batch(
                event_ids=all_event_ids,
                channel_id=channel_id,
                contents=split_discord_message(batch.render_discord()),
                idempotency_key=self._idempotency_key(channel_id, all_event_ids),
                additional_deliveries=self._delivery_payloads(batch),
            )
            return batch
        except Exception as error:
            self._store.fail_batch(event_ids=all_event_ids, error=repr(error), retry=True)
            raise

    @staticmethod
    def _idempotency_key(channel_id: str, event_ids: list[str]) -> str:
        raw = f"{channel_id}:{','.join(event_ids)}".encode()
        return "discord-batch:" + hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _delivery_payloads(batch: ResponseBatch) -> list[tuple[str, str, str]]:
        payloads: list[tuple[str, str, str]] = []
        for delivery_index, delivery in enumerate(batch.deliveries):
            for chunk_index, chunk in enumerate(split_discord_message(delivery.content)):
                suffix = f"{delivery.kind}:{delivery_index}:{chunk_index}"
                payloads.append((delivery.channel_id, chunk, suffix))
        return payloads
