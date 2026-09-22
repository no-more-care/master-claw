from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC

import discord

from masterclaw.app.i18n import locale_for_text, tr
from masterclaw.app.orchestrator import ChannelOrchestrator
from masterclaw.domain.models import IncomingAttachment, IncomingMessage
from masterclaw.runtime.resources import AsyncCloseable
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
        close_resources: Callable[[], Awaitable[None]] | None = None,
        resources: AsyncCloseable | None = None,
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
        if resources is not None and close_resources is not None:
            raise ValueError("provide a resource bundle or legacy close callback, not both")
        self._close_resources = resources.aclose if resources is not None else close_resources

    async def on_ready(self) -> None:
        recovered = 0
        if not self._startup_recovery_complete:
            recovered = self._store.recover_interrupted_work()
            self._startup_recovery_complete = True
        logger.info("Discord connected as %s; recovered %s inbox messages", self.user, recovered)
        for channel in self.get_all_channels():
            self._record_channel(channel)
        self._ensure_outbox_publisher()
        self._outbox_wakeup.set()
        pending_schedule = getattr(self._store, "pending_inbox_schedule", None)
        schedule = (
            pending_schedule()
            if callable(pending_schedule)
            else [(channel_id, 0) for channel_id in self._store.pending_inbox_channels()]
        )
        for channel_id, delay_seconds in schedule:
            prior = self._scheduled.get(channel_id)
            if prior is None or prior.done():
                self._scheduled[channel_id] = asyncio.create_task(
                    self._process_after_delay(channel_id, delay_seconds)
                    if delay_seconds
                    else self._process_after_debounce(channel_id)
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
        scheduled = [task for task in self._scheduled.values() if not task.done()]
        self._scheduled.clear()
        for task in scheduled:
            task.cancel()
        for task in scheduled:
            with suppress(asyncio.CancelledError):
                await task
        task = self._outbox_task
        self._outbox_task = None
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        try:
            await super().close()
        finally:
            close_resources, self._close_resources = self._close_resources, None
            if close_resources is not None:
                await close_resources()

    @staticmethod
    def _locale_for_text(content: str) -> str:
        return locale_for_text(content)

    @staticmethod
    def _channel_metadata(channel: object) -> tuple[str, str | None, str | None, str]:
        channel_id = str(channel.id)  # type: ignore[attr-defined]
        guild = getattr(channel, "guild", None)
        guild_id = str(guild.id) if guild is not None and hasattr(guild, "id") else None
        parent_id = getattr(channel, "parent_id", None)
        if parent_id is None:
            parent = getattr(channel, "parent", None)
            parent_id = getattr(parent, "id", None)
        channel_type = getattr(channel, "type", None)
        kind = str(channel_type) if channel_type is not None else type(channel).__name__.casefold()
        return channel_id, guild_id, str(parent_id) if parent_id is not None else None, kind

    def _record_channel(self, channel: object) -> tuple[str, str | None, str | None, str]:
        metadata = self._channel_metadata(channel)
        record = getattr(self._store, "record_discord_channel", None)
        if callable(record):
            channel_id, guild_id, parent_channel_id, kind = metadata
            record(
                channel_id=channel_id,
                guild_id=guild_id,
                parent_channel_id=parent_channel_id,
                kind=kind,
            )
        return metadata

    async def _resolve_channel(self, channel_id: int, *, capability: str) -> object | None:
        channel = self.get_channel(channel_id)
        if channel is not None and callable(getattr(channel, capability, None)):
            return channel
        try:
            channel = await self.fetch_channel(channel_id)
        except (discord.HTTPException, discord.ClientException):
            logger.warning(
                "Discord channel %s could not be fetched for %s",
                channel_id,
                capability,
                exc_info=True,
            )
            return None
        if channel is None:
            return None
        self._record_channel(channel)
        if callable(getattr(channel, capability, None)):
            return channel
        logger.warning(
            "Discord channel %s does not support %s",
            channel_id,
            capability,
        )
        return None

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
        if message.author.bot:
            return
        content = str(message.content or "")
        raw_attachments = tuple(getattr(message, "attachments", ()) or ())
        if not content.strip() and not raw_attachments:
            return
        channel_id, guild_id, parent_channel_id, _kind = self._record_channel(message.channel)
        if parent_channel_id is not None:
            inherit = getattr(self._store, "inherit_thread_context", None)
            if callable(inherit):
                try:
                    inherit(
                        channel_id=channel_id,
                        parent_channel_id=parent_channel_id,
                        guild_id=guild_id,
                    )
                except ValueError:
                    logger.warning("Discord thread %s could not inherit its parent", channel_id)
                    self._store.queue_system_notice(
                        channel_id=channel_id,
                        key=f"thread-context:{message.id}",
                        content=tr(self._locale_for_text(content), "thread_context_unavailable"),
                        source_event_id=str(message.id),
                        source_author_id=str(message.author.id),
                        source_guild_id=guild_id,
                    )
                    await self._publish_outbox()
                    return
        control = self._channel_control(content)
        if control is not None:
            enabled = control == "enable"
            changed = (
                self._store.enable_channel_monitoring(channel_id)
                if enabled
                else self._store.disable_channel_monitoring(channel_id)
            )
            locale = self._locale_for_text(content)
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
                source_event_id=str(message.id),
                source_author_id=str(message.author.id),
                source_guild_id=guild_id,
            )
            await self._publish_outbox()
            return
        if not self._store.channel_monitoring_enabled(channel_id):
            return
        attachments = tuple(
            IncomingAttachment(
                attachment_id=str(getattr(attachment, "id", "")),
                filename=str(getattr(attachment, "filename", "attachment")),
                content_type=(
                    str(attachment.content_type)
                    if getattr(attachment, "content_type", None) is not None
                    else None
                ),
                size=(
                    int(attachment.size) if getattr(attachment, "size", None) is not None else None
                ),
            )
            for attachment in raw_attachments
        )
        if attachments:
            self._store.queue_system_notice(
                channel_id=channel_id,
                key=f"attachments-unsupported:{message.id}",
                content=tr(self._locale_for_text(content), "attachments_unsupported"),
                source_event_id=str(message.id),
                source_author_id=str(message.author.id),
                source_guild_id=guild_id,
            )
            self._outbox_wakeup.set()
            if not content.strip():
                await self._publish_outbox()
                return
        reference = getattr(message, "reference", None)
        reply_to_event_id = getattr(reference, "message_id", None)
        resolved = getattr(reference, "resolved", None)
        reply_author = getattr(resolved, "author", None)
        reply_content = getattr(resolved, "content", None)
        created_at = message.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        else:
            created_at = created_at.astimezone(UTC)
        incoming = IncomingMessage(
            event_id=str(message.id),
            channel_id=channel_id,
            author_id=str(message.author.id),
            content=content,
            created_at=created_at,
            guild_id=guild_id,
            parent_channel_id=parent_channel_id,
            reply_to_event_id=(str(reply_to_event_id) if reply_to_event_id is not None else None),
            reply_to_author_id=(
                str(reply_author.id)
                if reply_author is not None and hasattr(reply_author, "id")
                else None
            ),
            reply_context=(
                str(reply_content)[:2000]
                if isinstance(reply_content, str) and reply_content.strip()
                else None
            ),
            attachments=attachments,
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
        channel_unavailable = False
        retry_delay: int | None = None
        try:
            channel = await self._resolve_channel(int(channel_id), capability="typing")
            if channel is None:
                channel_unavailable = True
                defer = getattr(self._store, "defer_pending_channel", None)
                if callable(defer):
                    retry_delay = defer(
                        channel_id=channel_id,
                        error="DiscordChannelUnavailable",
                    )
                logger.warning(
                    "Discord channel %s is unavailable; retry delay=%s",
                    channel_id,
                    retry_delay,
                )
                return
            mark_available = getattr(self._store, "mark_channel_available", None)
            if callable(mark_available):
                mark_available(channel_id)
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
        except discord.HTTPException:
            channel_unavailable = True
            defer = getattr(self._store, "defer_pending_channel", None)
            if callable(defer):
                retry_delay = defer(
                    channel_id=channel_id,
                    error="DiscordChannelHTTPError",
                )
            logger.warning(
                "Discord channel %s rejected processing; retry delay=%s",
                channel_id,
                retry_delay,
            )
            await self._publish_outbox()
        except Exception:
            logger.exception("Discord batch processing failed for channel %s", channel_id)
            await self._publish_outbox()
        finally:
            if channel_unavailable and retry_delay is not None:
                self._scheduled[channel_id] = asyncio.create_task(
                    self._process_after_delay(channel_id, retry_delay)
                )
            elif not channel_unavailable:
                pending_delay = self._pending_inbox_delay(channel_id)
                if pending_delay is not None:
                    self._scheduled[channel_id] = asyncio.create_task(
                        self._process_after_delay(channel_id, pending_delay)
                        if pending_delay
                        else self._process_after_debounce(channel_id)
                    )
            reset_trace(trace_token)

    def _pending_inbox_delay(self, channel_id: str) -> int | None:
        pending_schedule = getattr(self._store, "pending_inbox_schedule", None)
        if callable(pending_schedule):
            for pending_channel_id, delay_seconds in pending_schedule():
                if pending_channel_id == channel_id:
                    return max(0, int(delay_seconds))
            return None
        return 0 if channel_id in self._store.pending_inbox_channels() else None

    async def _process_after_delay(self, channel_id: str, delay_seconds: float) -> None:
        await asyncio.sleep(delay_seconds)
        await self._process_after_debounce(channel_id)

    async def _publish_slow_notice(self, channel_id: str, *, delay_seconds: float = 15.0) -> None:
        await asyncio.sleep(delay_seconds)
        rows = self._store.processing_inbox(channel_id=channel_id)
        if not rows:
            return
        row = rows[0]
        content = str(row["content"])
        locale = self._locale_for_text(content)
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
        if normalized in disable_markers:
            return "disable"
        if normalized in enable_markers:
            return "enable"
        return None

    async def _publish_outbox(self) -> bool:
        async with self._outbox_publish_lock:
            rows = list(self._store.claim_outbox())
            if not rows:
                return False

            def value(row: object, key: str, default: object = None) -> object:
                keys = row.keys() if hasattr(row, "keys") else ()
                return row[key] if key in keys else default  # type: ignore[index]

            def delivery_claim(row: object) -> str | None:
                claim = value(row, "delivery_claim")
                return str(claim) if claim is not None else None

            def failed(outbox_id: int, error: str, row: object) -> None:
                claim = delivery_claim(row)
                if claim is None:
                    self._store.mark_delivery_failed(outbox_id, error)
                else:
                    self._store.mark_delivery_failed(
                        outbox_id,
                        error,
                        delivery_claim=claim,
                    )

            def terminal(outbox_id: int, error: str, row: object) -> None:
                mark_terminal = getattr(self._store, "mark_delivery_terminal", None)
                if callable(mark_terminal):
                    claim = delivery_claim(row)
                    if claim is None:
                        mark_terminal(outbox_id, error)
                    else:
                        mark_terminal(outbox_id, error, delivery_claim=claim)
                else:
                    failed(outbox_id, error, row)

            outstanding = {int(row["id"]) for row in rows}
            claims_by_id = {int(row["id"]): delivery_claim(row) for row in rows}
            try:
                for row in rows:
                    outbox_id = int(row["id"])
                    try:
                        channel_id = int(row["channel_id"])
                    except (TypeError, ValueError):
                        failed(outbox_id, "InvalidDiscordChannelId", row)
                        outstanding.discard(outbox_id)
                        continue
                    channel = await self._resolve_channel(channel_id, capability="send")
                    if channel is None:
                        failed(outbox_id, "DiscordChannelUnavailable", row)
                        outstanding.discard(outbox_id)
                        continue
                    kind = str(value(row, "kind", "message"))
                    source_guild_id = value(row, "source_guild_id")
                    if kind in {"narrative", "roleplay_reply"} and source_guild_id is None:
                        terminal(
                            outbox_id,
                            "NarrativeDeliveryFromDirectMessageProhibited",
                            row,
                        )
                        outstanding.discard(outbox_id)
                        continue
                    target_guild = getattr(channel, "guild", None)
                    target_guild_id = (
                        str(target_guild.id)
                        if target_guild is not None and hasattr(target_guild, "id")
                        else None
                    )
                    if source_guild_id is not None and str(source_guild_id) != target_guild_id:
                        terminal(outbox_id, "CrossGuildDeliveryProhibited", row)
                        outstanding.discard(outbox_id)
                        continue
                    try:
                        with stage_span(
                            "delivery.discord_send",
                            component="discord",
                            operation="channel.send",
                            attributes={"outbox_id": outbox_id},
                        ):
                            embed_json = value(row, "embed_json")
                            embed = (
                                discord.Embed.from_dict(json.loads(str(embed_json)))
                                if embed_json
                                else None
                            )
                            content = str(row["content"]) if row["content"] else None
                            send_options: dict[str, object] = {
                                "content": content,
                                "embed": embed,
                            }
                            nonce = value(row, "discord_nonce")
                            if nonce:
                                send_options["nonce"] = str(nonce)
                            source_event_id = value(row, "source_event_id")
                            source_author_id = value(row, "source_author_id")
                            if (
                                kind in {"response", "system_notice"}
                                and source_event_id is not None
                            ):
                                try:
                                    reference = discord.MessageReference(
                                        message_id=int(str(source_event_id)),
                                        channel_id=int(str(channel_id)),
                                        guild_id=(
                                            int(str(source_guild_id))
                                            if source_guild_id is not None
                                            else None
                                        ),
                                        fail_if_not_exists=False,
                                    )
                                except (TypeError, ValueError):
                                    if source_author_id is not None and content:
                                        author = str(source_author_id)
                                        address = f"<@{author}>" if author.isdigit() else author
                                        send_options["content"] = f"↪ {address}: {content}"
                                else:
                                    send_options["reference"] = reference
                                    send_options["mention_author"] = False
                            sent_message = await channel.send(**send_options)
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        failed(outbox_id, sanitized_error_summary(error), row)
                        outstanding.discard(outbox_id)
                        logger.warning(
                            "Discord outbox delivery failed for message %s (%s)",
                            outbox_id,
                            type(error).__name__,
                        )
                    else:
                        sent_message_id = getattr(sent_message, "id", None)
                        mark_kwargs: dict[str, object] = {
                            "discord_message_id": (
                                str(sent_message_id) if sent_message_id is not None else None
                            )
                        }
                        claim = delivery_claim(row)
                        if claim is not None:
                            mark_kwargs["delivery_claim"] = claim
                        self._store.mark_delivered(outbox_id, **mark_kwargs)
                        outstanding.discard(outbox_id)
            finally:
                if outstanding:
                    try:
                        grouped: dict[str | None, list[int]] = {}
                        for outbox_id in sorted(outstanding):
                            grouped.setdefault(claims_by_id[outbox_id], []).append(outbox_id)
                        for claim, outbox_ids in grouped.items():
                            if claim is None:
                                self._store.release_outbox_claims(outbox_ids)
                            else:
                                self._store.release_outbox_claims(
                                    outbox_ids,
                                    delivery_claim=claim,
                                )
                    except Exception:
                        logger.exception("Failed to release Discord outbox claims")
            return True
