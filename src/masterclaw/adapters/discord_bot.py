from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from contextlib import suppress
from datetime import UTC

import discord

from masterclaw.app.i18n import tr
from masterclaw.app.orchestrator import ChannelOrchestrator
from masterclaw.domain.models import IncomingMessage
from masterclaw.storage.sqlite import SQLiteStore
from masterclaw.telemetry import bind_trace, reset_trace, sanitized_error_summary, stage_span

logger = logging.getLogger(__name__)


class DiscordIngressClient(discord.Client):
    """Thin Discord adapter; all durable work starts in SQLite."""

    def __init__(
        self,
        *,
        store: SQLiteStore,
        orchestrator: ChannelOrchestrator,
        debounce_seconds: float = 1.5,
        outbox_poll_seconds: float = 1.0,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self._store = store
        self._orchestrator = orchestrator
        self._debounce_seconds = debounce_seconds
        self._outbox_poll_seconds = outbox_poll_seconds
        self._scheduled: dict[str, asyncio.Task[None]] = {}
        self._startup_recovery_complete = False
        self._outbox_publish_lock = asyncio.Lock()
        self._outbox_wakeup = asyncio.Event()
        self._outbox_task: asyncio.Task[None] | None = None

    async def on_ready(self) -> None:
        recovered = 0
        if not self._startup_recovery_complete:
            recovered = self._store.recover_interrupted_work()
            self._startup_recovery_complete = True
        logger.info("Discord connected as %s; recovered %s inbox messages", self.user, recovered)
        self._ensure_outbox_publisher()
        self._outbox_wakeup.set()
        for channel_id in self._store.pending_inbox_channels():
            prior = self._scheduled.get(channel_id)
            if prior is None or prior.done():
                self._scheduled[channel_id] = asyncio.create_task(
                    self._process_after_debounce(channel_id)
                )

    def _ensure_outbox_publisher(self) -> None:
        if self._outbox_task is None or self._outbox_task.done():
            self._outbox_task = asyncio.create_task(
                self._run_outbox_publisher(), name="masterclaw-outbox-publisher"
            )

    async def _run_outbox_publisher(self) -> None:
        while True:
            self._outbox_wakeup.clear()
            try:
                claimed_work = await self._publish_outbox()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Discord outbox publisher iteration failed")
                claimed_work = False
            if claimed_work:
                continue
            try:
                await asyncio.wait_for(
                    self._outbox_wakeup.wait(), timeout=self._outbox_poll_seconds
                )
            except TimeoutError:
                pass

    async def close(self) -> None:
        task = self._outbox_task
        self._outbox_task = None
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await super().close()

    async def on_message(self, message: discord.Message) -> None:
        trace_token = bind_trace(
            self._store,
            trace_id=f"ingress:{message.id}",
            channel_id=str(message.channel.id),
            event_id=str(message.id),
        )
        try:
            with stage_span("ingress.total", component="discord", operation="on_message"):
                await self._ingest_message(message)
        finally:
            reset_trace(trace_token)

    async def _ingest_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.content.strip():
            return
        channel_id = str(message.channel.id)
        control = self._channel_control(message.content)
        if control is not None:
            enabled = control == "enable"
            changed = (
                self._store.enable_channel_monitoring(channel_id)
                if enabled
                else self._store.disable_channel_monitoring(channel_id)
            )
            locale = "ru" if re.search(r"[А-Яа-яЁё]", message.content) else "en"
            key = (
                "channel_monitoring_enabled"
                if enabled and changed
                else "channel_monitoring_already_enabled"
                if enabled
                else "channel_monitoring_disabled"
                if changed
                else "channel_monitoring_already_disabled"
            )
            self._store.queue_system_notice(
                channel_id=channel_id,
                key=f"channel-monitoring:{message.id}",
                content=tr(locale, key),
            )
            await self._publish_outbox()
            return
        if not self._store.channel_monitoring_enabled(channel_id):
            return
        incoming = IncomingMessage(
            event_id=str(message.id),
            channel_id=str(message.channel.id),
            author_id=str(message.author.id),
            content=message.content,
            created_at=message.created_at.replace(tzinfo=UTC),
        )
        with stage_span("ingress.enqueue", component="sqlite", operation="enqueue"):
            if not self._store.enqueue(incoming):
                return
        prior = self._scheduled.get(channel_id)
        if prior is None or prior.done():
            self._scheduled[channel_id] = asyncio.create_task(
                self._process_after_debounce(channel_id)
            )

    async def _process_after_debounce(self, channel_id: str) -> None:
        trace_token = bind_trace(
            self._store,
            trace_id=f"batch:{channel_id}:{uuid.uuid4().hex}",
            channel_id=channel_id,
        )
        try:
            channel = self.get_channel(int(channel_id))
            if channel is None:
                raise RuntimeError("Discord channel is unavailable to the bot")
            slow_notice = asyncio.create_task(self._publish_slow_notice(channel_id))
            try:
                async with channel.typing():
                    await self._orchestrator.process_until_quiet(
                        channel_id, quiet_seconds=self._debounce_seconds
                    )
            finally:
                slow_notice.cancel()
                with suppress(asyncio.CancelledError):
                    await slow_notice
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
            reset_trace(trace_token)

    async def _publish_slow_notice(self, channel_id: str, *, delay_seconds: float = 15.0) -> None:
        await asyncio.sleep(delay_seconds)
        rows = self._store.processing_inbox(channel_id=channel_id)
        if not rows:
            return
        row = rows[0]
        content = str(row["content"])
        locale = "ru" if re.search(r"[А-Яа-яЁё]", content) else "en"
        self._store.queue_system_notice(
            channel_id=channel_id,
            key=f"slow-processing:{row['event_id']}",
            content=tr(locale, "slow_processing"),
        )
        await self._publish_outbox()

    def _channel_control(self, content: str) -> str | None:
        if self.user is None:
            return None
        user_id = str(self.user.id)
        mentions = (f"<@{user_id}>", f"<@!{user_id}>")
        if not any(mention in content for mention in mentions):
            return None
        normalized = content.casefold()
        for mention in mentions:
            normalized = normalized.replace(mention, " ")
        normalized = " ".join(normalized.split())
        enable_markers = (
            "включи этот канал для игры",
            "активируй этот канал для игры",
            "включи игровой режим",
            "enable this channel for play",
            "enable game monitoring",
        )
        disable_markers = (
            "выключи этот канал для игры",
            "отключи этот канал от игры",
            "выключи игровой режим",
            "disable this channel for play",
            "disable game monitoring",
        )
        if any(marker in normalized for marker in disable_markers):
            return "disable"
        if any(marker in normalized for marker in enable_markers):
            return "enable"
        return None

    async def _publish_outbox(self) -> bool:
        async with self._outbox_publish_lock:
            rows = list(self._store.claim_outbox())
            if not rows:
                return False
            outstanding = {int(row["id"]) for row in rows}
            try:
                for row in rows:
                    outbox_id = int(row["id"])
                    try:
                        channel_id = int(row["channel_id"])
                    except (TypeError, ValueError):
                        self._store.mark_delivery_failed(outbox_id, "InvalidDiscordChannelId")
                        outstanding.discard(outbox_id)
                        continue
                    channel = self.get_channel(channel_id)
                    if channel is None:
                        self._store.mark_delivery_failed(outbox_id, "DiscordChannelUnavailable")
                        outstanding.discard(outbox_id)
                        continue
                    try:
                        with stage_span(
                            "delivery.discord_send",
                            component="discord",
                            operation="channel.send",
                            attributes={"outbox_id": outbox_id},
                        ):
                            embed_json = row["embed_json"] if "embed_json" in row.keys() else None
                            embed = (
                                discord.Embed.from_dict(json.loads(embed_json))
                                if embed_json
                                else None
                            )
                            await channel.send(content=row["content"] or None, embed=embed)
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        self._store.mark_delivery_failed(outbox_id, sanitized_error_summary(error))
                        outstanding.discard(outbox_id)
                        logger.warning(
                            "Discord outbox delivery failed for message %s (%s)",
                            outbox_id,
                            type(error).__name__,
                        )
                    else:
                        self._store.mark_delivered(outbox_id)
                        outstanding.discard(outbox_id)
            finally:
                if outstanding:
                    try:
                        self._store.release_outbox_claims(sorted(outstanding))
                    except Exception:
                        logger.exception("Failed to release Discord outbox claims")
            return True
