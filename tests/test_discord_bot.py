import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from masterclaw.adapters.discord_bot import DiscordIngressClient


class FakeStore:
    def __init__(self) -> None:
        self.enqueued = []
        self.delivered = []
        self.failed = []
        self.outbox = []
        self.monitored = set()
        self.notices = []
        self.processing = []
        self.released = []
        self.recoveries = 0

    def enqueue(self, message) -> bool:
        self.enqueued.append(message)
        return True

    def claim_outbox(self):
        rows = self.outbox
        self.outbox = []
        return rows

    def release_outbox_claims(self, outbox_ids: list[int]) -> int:
        self.released.append(outbox_ids)
        return len(outbox_ids)

    def mark_delivered(self, outbox_id: int) -> None:
        self.delivered.append(outbox_id)

    def mark_delivery_failed(self, outbox_id: int, error: str) -> None:
        self.failed.append((outbox_id, error))

    def channel_monitoring_enabled(self, channel_id: str) -> bool:
        return channel_id in self.monitored

    def enable_channel_monitoring(self, channel_id: str) -> bool:
        changed = channel_id not in self.monitored
        self.monitored.add(channel_id)
        return changed

    def disable_channel_monitoring(self, channel_id: str) -> bool:
        changed = channel_id in self.monitored
        self.monitored.discard(channel_id)
        return changed

    def queue_system_notice(self, *, channel_id: str, key: str, content: str) -> None:
        self.notices.append((channel_id, key, content))

    def processing_inbox(self, *, channel_id: str):
        return self.processing

    def recover_interrupted_work(self) -> int:
        self.recoveries += 1
        return 0

    def pending_inbox_channels(self):
        return []


@pytest.mark.asyncio
async def test_discord_ingress_enqueues_and_schedules_each_channel_once() -> None:
    store = FakeStore()
    store.monitored.add("20")
    client = DiscordIngressClient(
        store=store,
        orchestrator=SimpleNamespace(),
        debounce_seconds=0,
    )
    processed = []

    async def process(channel_id: str) -> None:
        processed.append(channel_id)

    client._process_after_debounce = process  # type: ignore[method-assign]
    message = SimpleNamespace(
        id=10,
        channel=SimpleNamespace(id=20),
        author=SimpleNamespace(id=30, bot=False),
        content="hello",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await client.on_message(message)
    await asyncio.gather(*client._scheduled.values())
    assert store.enqueued[0].event_id == "10"
    assert store.enqueued[0].channel_id == "20"
    assert processed == ["20"]


@pytest.mark.asyncio
async def test_discord_ingress_ignores_messages_outside_enabled_channels() -> None:
    store = FakeStore()
    client = DiscordIngressClient(
        store=store,
        orchestrator=SimpleNamespace(),
        debounce_seconds=0,
    )
    message = SimpleNamespace(
        id=10,
        channel=SimpleNamespace(id=20),
        author=SimpleNamespace(id=30, bot=False),
        content="ordinary player message",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await client.on_message(message)
    assert store.enqueued == []
    assert client._scheduled == {}


@pytest.mark.asyncio
async def test_mentioned_bot_enables_channel_then_reads_unmentioned_messages() -> None:
    store = FakeStore()
    client = DiscordIngressClient(
        store=store,
        orchestrator=SimpleNamespace(),
        debounce_seconds=0,
    )
    client._connection.user = SimpleNamespace(id=99)

    async def publish() -> None:
        return None

    async def process(channel_id: str) -> None:
        return None

    client._publish_outbox = publish  # type: ignore[method-assign]
    client._process_after_debounce = process  # type: ignore[method-assign]
    activation = SimpleNamespace(
        id=10,
        channel=SimpleNamespace(id=20),
        author=SimpleNamespace(id=30, bot=False),
        content="<@99> включи этот канал для игры",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await client.on_message(activation)
    assert store.monitored == {"20"}
    assert store.enqueued == []
    assert "без обязательного упоминания" in store.notices[0][2]

    ordinary = SimpleNamespace(
        id=11,
        channel=SimpleNamespace(id=20),
        author=SimpleNamespace(id=31, bot=False),
        content="я осматриваю дверь",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await client.on_message(ordinary)
    await asyncio.gather(*client._scheduled.values())
    assert [message.event_id for message in store.enqueued] == ["11"]


@pytest.mark.asyncio
async def test_discord_outbox_marks_success_and_missing_channel_failure() -> None:
    store = FakeStore()
    store.outbox = [
        {"id": 1, "channel_id": "21", "content": "missing"},
        {"id": 2, "channel_id": "20", "content": "hello"},
    ]
    sent = []

    class Channel:
        async def send(self, *, content=None, embed=None) -> None:
            sent.append((content, embed))

    client = DiscordIngressClient(
        store=store,
        orchestrator=SimpleNamespace(),
        debounce_seconds=0,
    )
    client.get_channel = lambda channel_id: Channel() if channel_id == 20 else None  # type: ignore[method-assign]
    await client._publish_outbox()
    assert sent == [("hello", None)]
    assert store.delivered == [2]
    assert store.failed == [(1, "DiscordChannelUnavailable")]
    assert store.released == []


@pytest.mark.asyncio
async def test_discord_outbox_continues_after_invalid_channel_and_send_error() -> None:
    store = FakeStore()
    store.outbox = [
        {"id": 1, "channel_id": "invalid", "content": "bad id"},
        {"id": 2, "channel_id": "20", "content": "secret"},
        {"id": 3, "channel_id": "21", "content": "after failure"},
    ]
    sent = []

    class FailingChannel:
        async def send(self, *, content=None, embed=None) -> None:
            raise RuntimeError("private Discord response body")

    class WorkingChannel:
        async def send(self, *, content=None, embed=None) -> None:
            sent.append((content, embed))

    client = DiscordIngressClient(store=store, orchestrator=SimpleNamespace())
    client.get_channel = lambda channel_id: (  # type: ignore[method-assign]
        FailingChannel() if channel_id == 20 else WorkingChannel()
    )

    await client._publish_outbox()

    assert sent == [("after failure", None)]
    assert store.delivered == [3]
    assert store.failed == [(1, "InvalidDiscordChannelId"), (2, "RuntimeError")]
    assert "private" not in repr(store.failed)
    assert store.released == []


@pytest.mark.asyncio
async def test_discord_outbox_releases_every_claim_when_cancelled() -> None:
    store = FakeStore()
    store.outbox = [
        {"id": 1, "channel_id": "20", "content": "first"},
        {"id": 2, "channel_id": "20", "content": "second"},
    ]
    sending = asyncio.Event()

    class BlockingChannel:
        async def send(self, *, content=None, embed=None) -> None:
            sending.set()
            await asyncio.Future()

    client = DiscordIngressClient(store=store, orchestrator=SimpleNamespace())
    client.get_channel = lambda _channel_id: BlockingChannel()  # type: ignore[method-assign]
    publishing = asyncio.create_task(client._publish_outbox())
    await sending.wait()
    publishing.cancel()

    with pytest.raises(asyncio.CancelledError):
        await publishing
    assert store.released == [[1, 2]]


@pytest.mark.asyncio
async def test_ready_recovers_only_once_across_reconnects() -> None:
    store = FakeStore()
    client = DiscordIngressClient(
        store=store,
        orchestrator=SimpleNamespace(),
        outbox_poll_seconds=10,
    )

    await client.on_ready()
    publisher = client._outbox_task
    try:
        await client.on_ready()

        assert store.recoveries == 1
        assert client._outbox_task is publisher
    finally:
        assert publisher is not None
        publisher.cancel()
        with suppress(asyncio.CancelledError):
            await publisher


@pytest.mark.asyncio
async def test_outbox_publisher_polls_for_messages_created_after_ready() -> None:
    store = FakeStore()
    sent = asyncio.Event()

    class Channel:
        async def send(self, *, content=None, embed=None) -> None:
            sent.set()

    client = DiscordIngressClient(
        store=store,
        orchestrator=SimpleNamespace(),
        outbox_poll_seconds=0.01,
    )
    client.get_channel = lambda _channel_id: Channel()  # type: ignore[method-assign]
    await client.on_ready()
    store.outbox.append({"id": 1, "channel_id": "20", "content": "late"})
    try:
        await asyncio.wait_for(sent.wait(), timeout=0.5)
        assert store.delivered == [1]
    finally:
        assert client._outbox_task is not None
        client._outbox_task.cancel()
        with suppress(asyncio.CancelledError):
            await client._outbox_task


def test_discord_client_suppresses_all_allowed_mentions() -> None:
    client = DiscordIngressClient(store=FakeStore(), orchestrator=SimpleNamespace())

    assert client.allowed_mentions.everyone is False
    assert client.allowed_mentions.users is False
    assert client.allowed_mentions.roles is False
    assert client.allowed_mentions.replied_user is False


@pytest.mark.asyncio
async def test_slow_processing_notice_is_queued_once_without_mention() -> None:
    store = FakeStore()
    store.processing = [{"event_id": "event-1", "content": "Генерируй"}]
    client = DiscordIngressClient(
        store=store,
        orchestrator=SimpleNamespace(),
        debounce_seconds=0,
    )

    async def publish() -> None:
        return None

    client._publish_outbox = publish  # type: ignore[method-assign]
    await client._publish_slow_notice("20", delay_seconds=0)

    assert len(store.notices) == 1
    assert store.notices[0][1] == "slow-processing:event-1"
    assert "повторять" in store.notices[0][2]
    assert "<@" not in store.notices[0][2]
