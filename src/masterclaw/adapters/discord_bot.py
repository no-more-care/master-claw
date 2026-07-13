from __future__ import annotations

import asyncio
import logging
from datetime import UTC

import discord

from masterclaw.app.orchestrator import ChannelOrchestrator
from masterclaw.domain.models import IncomingMessage
from masterclaw.storage.sqlite import SQLiteStore

logger = logging.getLogger(__name__)


class DiscordIngressClient(discord.Client):
    """Thin Discord adapter; all durable work starts in SQLite."""

    def __init__(
        self,
        *,
        store: SQLiteStore,
        orchestrator: ChannelOrchestrator,
        debounce_seconds: float = 1.5,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self._store = store
        self._orchestrator = orchestrator
        self._debounce_seconds = debounce_seconds
        self._scheduled: dict[str, asyncio.Task[None]] = {}

    async def on_ready(self) -> None:
        recovered = self._store.recover_interrupted_work()
        logger.info("Discord connected as %s; recovered %s inbox messages", self.user, recovered)
        await self._publish_outbox()
        for channel_id in self._store.pending_inbox_channels():
            prior = self._scheduled.get(channel_id)
            if prior is None or prior.done():
                self._scheduled[channel_id] = asyncio.create_task(
                    self._process_after_debounce(channel_id)
                )

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.content.strip():
            return
        incoming = IncomingMessage(
            event_id=str(message.id),
            channel_id=str(message.channel.id),
            author_id=str(message.author.id),
            content=message.content,
            created_at=message.created_at.replace(tzinfo=UTC),
        )
        if not self._store.enqueue(incoming):
            return
        channel_id = str(message.channel.id)
        prior = self._scheduled.get(channel_id)
        if prior is None or prior.done():
            self._scheduled[channel_id] = asyncio.create_task(
                self._process_after_debounce(channel_id)
            )

    async def _process_after_debounce(self, channel_id: str) -> None:
        try:
            channel = self.get_channel(int(channel_id))
            if channel is None:
                raise RuntimeError("Discord channel is unavailable to the bot")
            async with channel.typing():
                await self._orchestrator.process_until_quiet(
                    channel_id, quiet_seconds=self._debounce_seconds
                )
            await self._publish_outbox()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Discord batch processing failed for channel %s", channel_id)
            await self._publish_outbox()
        finally:
            if channel_id in self._store.pending_inbox_channels():
                self._scheduled[channel_id] = asyncio.create_task(
                    self._process_after_debounce(channel_id)
                )

    async def _publish_outbox(self) -> None:
        for row in self._store.pending_outbox():
            channel = self.get_channel(int(row["channel_id"]))
            if channel is None:
                self._store.mark_delivery_failed(
                    row["id"], "Discord channel is unavailable to the bot"
                )
                continue
            try:
                await channel.send(row["content"])
            except Exception as error:
                self._store.mark_delivery_failed(row["id"], repr(error))
                raise
            else:
                self._store.mark_delivered(row["id"])
