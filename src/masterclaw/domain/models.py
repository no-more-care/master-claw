from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class OperatingMode(StrEnum):
    WORLD_MANAGEMENT = "world_management"
    PREPARATION = "preparation"
    PLAY = "play"


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
class IncomingMessage:
    event_id: str
    channel_id: str
    author_id: str
    content: str
    created_at: datetime

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


@dataclass(frozen=True, slots=True)
class OutboundDelivery:
    channel_id: str
    content: str
    kind: str = "message"


@dataclass(frozen=True, slots=True)
class HandlerResponse:
    text: str
    deliveries: tuple[OutboundDelivery, ...] = ()


@dataclass(frozen=True, slots=True)
class ResponseBatch:
    channel_id: str
    items: tuple[AddressedResponse, ...]
    deliveries: tuple[OutboundDelivery, ...] = ()

    def render_discord(self) -> str:
        return "\n\n".join(f"<@{item.author_id}> {item.text}" for item in self.items)
