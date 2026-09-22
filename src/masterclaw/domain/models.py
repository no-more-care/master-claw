from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class OperatingMode(StrEnum):
    WORLD_MANAGEMENT = "world_management"
    PREPARATION = "preparation"
    PLAY = "play"
    PAUSED = "paused"
    FINISHED = "finished"


class GameLifecycle(StrEnum):
    DRAFT = "draft"
    PREPARING = "preparing"
    ACTIVE = "active"
    PAUSED = "paused"
    FINISHED = "finished"


class InboxStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ChannelState:
    channel_id: str
    game_id: str | None = None
    lifecycle: GameLifecycle | None = None


@dataclass(frozen=True, slots=True)
class IncomingAttachment:
    attachment_id: str
    filename: str
    content_type: str | None = None
    size: int | None = None


@dataclass(frozen=True, slots=True)
class IncomingMessage:
    event_id: str
    channel_id: str
    author_id: str
    content: str
    created_at: datetime
    guild_id: str | None = None
    parent_channel_id: str | None = None
    reply_to_event_id: str | None = None
    reply_to_author_id: str | None = None
    reply_context: str | None = None
    attachments: tuple[IncomingAttachment, ...] = ()
    routing_game_id: str | None = field(default=None, compare=False)
    routing_lifecycle: GameLifecycle | None = field(default=None, compare=False)
    routing_scene_id: str | None = field(default=None, compare=False)
    has_routing_snapshot: bool = field(default=False, compare=False)

    def __post_init__(self) -> None:
        if not self.event_id or not self.channel_id or not self.author_id:
            raise ValueError("event_id, channel_id and author_id are required")
        if not self.content.strip():
            raise ValueError("message content cannot be empty")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")

    @classmethod
    def now(
        cls, *, event_id: str, channel_id: str, author_id: str, content: str
    ) -> IncomingMessage:
        return cls(event_id, channel_id, author_id, content, datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class AddressedResponse:
    author_id: str
    text: str
    source_event_id: str | None = None


@dataclass(frozen=True, slots=True)
class OutboundDelivery:
    channel_id: str
    content: str
    kind: str = "message"


@dataclass(frozen=True, slots=True)
class HandlerResponse:
    text: str
    deliveries: tuple[OutboundDelivery, ...] = ()
    completion_game_id: str | None = None
    render_live_status: bool = False


@dataclass(frozen=True, slots=True)
class ResponseBatch:
    channel_id: str
    items: tuple[AddressedResponse, ...]
    deliveries: tuple[OutboundDelivery, ...] = ()
    completion_game_id: str | None = None

    def render_discord(self) -> str:
        return "\n\n".join(item.text for item in self.items)
