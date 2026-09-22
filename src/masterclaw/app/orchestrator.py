from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Awaitable, Callable

from masterclaw.app.batching import SequentialBatchProcessor
from masterclaw.app.i18n import tr
from masterclaw.app.response_format import split_discord_message
from masterclaw.context.assembler import ContextAssemblyError
from masterclaw.domain.models import (
    AddressedResponse,
    HandlerResponse,
    IncomingMessage,
    OutboundDelivery,
    ResponseBatch,
)
from masterclaw.pipelines.base import DeterministicProviderError, TransientProviderError
from masterclaw.storage.sqlite import SQLiteStore
from masterclaw.telemetry import sanitized_error_summary, stage_span

ApplicationHandler = Callable[[IncomingMessage], Awaitable[str | HandlerResponse]]


class ChannelOrchestrator:
    """Durable, sequential processing boundary for a Discord channel."""

    def __init__(self, store: SQLiteStore, handler: ApplicationHandler) -> None:
        self._store = store
        self._handler = handler
        self._batcher = SequentialBatchProcessor()

    async def process_available(self, channel_id: str, *, limit: int = 50) -> ResponseBatch | None:
        all_responses: list[AddressedResponse] = []
        all_deliveries: list[OutboundDelivery] = []
        processed = False
        for _ in range(limit):
            with stage_span("inbox.claim", component="sqlite", operation="claim_pending"):
                messages = self._store.claim_pending(channel_id=channel_id, limit=1)
            if not messages:
                break
            processed = True
            partial = await self._process_claimed(messages)
            all_responses.extend(partial.items)
            all_deliveries.extend(partial.deliveries)
        if not processed:
            return None
        return ResponseBatch(channel_id, tuple(all_responses), tuple(all_deliveries))

    async def process_until_quiet(
        self,
        channel_id: str,
        *,
        quiet_seconds: float = 1.5,
        batch_limit: int = 50,
        max_batches: int = 10,
    ) -> ResponseBatch | None:
        """Include messages arriving during processing in one final outbox block."""
        all_responses: list[AddressedResponse] = []
        all_deliveries: list[OutboundDelivery] = []
        processed = False
        for _ in range(max_batches):
            await asyncio.sleep(quiet_seconds)
            claimed_in_window = False
            for _ in range(batch_limit):
                with stage_span("inbox.claim", component="sqlite", operation="claim_pending"):
                    messages = self._store.claim_pending(channel_id=channel_id, limit=1)
                if not messages:
                    break
                claimed_in_window = True
                processed = True
                partial = await self._process_claimed(messages)
                all_responses.extend(partial.items)
                all_deliveries.extend(partial.deliveries)
            if not claimed_in_window:
                break
        if not processed:
            return None
        return ResponseBatch(channel_id, tuple(all_responses), tuple(all_deliveries))

    async def _process_claimed(self, messages: list[IncomingMessage]) -> ResponseBatch:
        responses: list[AddressedResponse] = []
        deliveries: list[OutboundDelivery] = []
        completion_game_id: str | None = None
        for message in messages:
            try:
                with stage_span(
                    "batch.process", component="orchestrator", operation="process_message"
                ):
                    journaled = self._store.handler_result(message.event_id)
                    if journaled is None:
                        result = await self._handler(message)
                        journaled = self._store.checkpoint_handler_result(
                            event_id=message.event_id,
                            result=self._handler_result_payload(result),
                        )
                    partial = self._batcher.from_result(
                        message,
                        self._handler_result_from_payload(journaled),
                    )
            except Exception as error:
                terminal = self._store.fail_batch(
                    event_ids=[message.event_id],
                    error=sanitized_error_summary(error),
                    retry=self._should_retry(error),
                    provider_transient=isinstance(error, TransientProviderError),
                )
                if terminal:
                    self._queue_failure_notice(message)
                raise
            try:
                with stage_span("outbox.persist", component="sqlite", operation="complete_message"):
                    self._store.complete_batch(
                        event_ids=[message.event_id],
                        channel_id=message.channel_id,
                        contents=[],
                        idempotency_key=self._idempotency_key(
                            message.channel_id, [message.event_id]
                        ),
                        additional_deliveries=self._delivery_payloads(partial),
                        payloads=self._response_payloads(partial),
                        assistant_turns=self._assistant_turns(partial),
                        completion_game_id=partial.completion_game_id,
                    )
            except Exception as error:
                terminal = self._store.fail_batch(
                    event_ids=[message.event_id],
                    error=sanitized_error_summary(error),
                    retry=self._should_retry(error),
                    provider_transient=isinstance(error, TransientProviderError),
                )
                if terminal:
                    self._queue_failure_notice(message)
                raise
            responses.extend(partial.items)
            deliveries.extend(partial.deliveries)
            completion_game_id = partial.completion_game_id
        return ResponseBatch(
            messages[0].channel_id,
            tuple(responses),
            tuple(deliveries),
            completion_game_id,
        )

    @staticmethod
    def _should_retry(error: Exception) -> bool:
        return not isinstance(
            error,
            (ValueError, ContextAssemblyError, AssertionError, DeterministicProviderError),
        )

    @staticmethod
    def _handler_result_payload(
        result: str | HandlerResponse,
    ) -> dict[str, object]:
        if isinstance(result, HandlerResponse):
            return {
                "text": result.text,
                "deliveries": [
                    {
                        "channel_id": delivery.channel_id,
                        "content": delivery.content,
                        "kind": delivery.kind,
                    }
                    for delivery in result.deliveries
                ],
                "completion_game_id": result.completion_game_id,
                "render_live_status": result.render_live_status,
            }
        return {
            "text": result,
            "deliveries": [],
            "completion_game_id": None,
            "render_live_status": False,
        }

    @staticmethod
    def _handler_result_from_payload(
        payload: dict[str, object],
    ) -> HandlerResponse:
        return HandlerResponse(
            text=str(payload["text"]),
            deliveries=tuple(
                OutboundDelivery(
                    channel_id=str(delivery["channel_id"]),
                    content=str(delivery["content"]),
                    kind=str(delivery["kind"]),
                )
                for delivery in payload["deliveries"]
            ),
            completion_game_id=(
                None
                if payload["completion_game_id"] is None
                else str(payload["completion_game_id"])
            ),
            render_live_status=bool(payload["render_live_status"]),
        )

    @staticmethod
    def _idempotency_key(channel_id: str, event_ids: list[str]) -> str:
        raw = f"{channel_id}:{','.join(event_ids)}".encode()
        return "discord-batch:" + hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _delivery_payloads(batch: ResponseBatch) -> list[tuple[str, str, str, str]]:
        payloads: list[tuple[str, str, str, str]] = []
        for delivery_index, delivery in enumerate(batch.deliveries):
            for chunk_index, chunk in enumerate(split_discord_message(delivery.content)):
                suffix = f"{delivery.kind}:{delivery_index}:{chunk_index}"
                payloads.append((delivery.channel_id, chunk, suffix, delivery.kind))
        return payloads

    @staticmethod
    def _response_payloads(
        batch: ResponseBatch,
    ) -> list[tuple[str, dict[str, object] | None, str | None, str]]:
        payloads: list[tuple[str, dict[str, object] | None, str | None, str]] = []
        for item in batch.items:
            text = item.text.strip()
            embed = None
            body = text
            if text.startswith("## ") and "\n\n" in text:
                panel, body = text.split("\n\n", 1)
                title, _, description = panel.partition("\n")
                if len(description) <= 4096:
                    color = 0x5865F2
                    if "ПОДГОТОВКА" in title or "PREPARATION" in title:
                        color = 0xF0B232
                    elif "ИГРА" in title or "PLAY" in title:
                        color = 0x57F287
                    embed = {
                        "title": title.removeprefix("## "),
                        "description": description,
                        "color": color,
                    }
                else:
                    body = text
            chunks = split_discord_message(body) if body.strip() else [""]
            for index, chunk in enumerate(chunks):
                payloads.append(
                    (
                        chunk,
                        embed if index == 0 else None,
                        item.source_event_id,
                        item.author_id,
                    )
                )
        return payloads

    @staticmethod
    def _assistant_turns(batch: ResponseBatch) -> list[tuple[str, str, str]]:
        return [
            (item.source_event_id, item.author_id, item.text)
            for item in batch.items
            if item.source_event_id is not None and item.text.strip()
        ]

    def _queue_failure_notice(self, message: IncomingMessage) -> None:
        key = self._idempotency_key(message.channel_id, [message.event_id])
        channel = self._store.channel_state(message.channel_id)
        game = self._store.game_state(channel.game_id) if channel.game_id is not None else None
        locale = (
            game.locale
            if game is not None
            else ("ru" if re.search(r"[А-Яа-яЁё]", message.content) else "en")
        )
        self._store.queue_system_notice(
            channel_id=message.channel_id,
            key=key,
            content=tr(locale, "system_failure"),
            source_event_id=message.event_id,
            source_author_id=message.author_id,
            source_guild_id=message.guild_id,
        )
