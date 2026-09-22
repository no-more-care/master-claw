import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from masterclaw.adapters.discord_bot import DiscordIngressClient
from masterclaw.app.message_handler import MessageApplication
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.models import GameLifecycle
from masterclaw.domain.state import GameState, WorldState
from masterclaw.pipelines.state_decision import StateDecisionRouter
from masterclaw.storage.sqlite import SQLiteStore


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
        self.terminal = []
        self.deferred = []
        self.recorded_channels = []
        self.inherited_threads = []
        self.available = []
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

    def mark_delivered(self, outbox_id: int, *, discord_message_id=None) -> None:
        self.delivered.append(outbox_id)

    def mark_delivery_failed(self, outbox_id: int, error: str) -> None:
        self.failed.append((outbox_id, error))

    def mark_delivery_terminal(self, outbox_id: int, error: str) -> None:
        self.terminal.append((outbox_id, error))

    def defer_pending_channel(self, *, channel_id: str, error: str):
        self.deferred.append((channel_id, error))
        return None

    def mark_channel_available(self, channel_id: str) -> None:
        self.available.append(channel_id)

    def record_discord_channel(
        self,
        *,
        channel_id: str,
        guild_id: str | None,
        parent_channel_id: str | None,
        kind: str,
    ) -> None:
        self.recorded_channels.append((channel_id, guild_id, parent_channel_id, kind))

    def inherit_thread_context(
        self, *, channel_id: str, parent_channel_id: str, guild_id: str | None
    ) -> bool:
        self.inherited_threads.append((channel_id, parent_channel_id, guild_id))
        self.monitored.add(channel_id)
        return True

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

    def queue_system_notice(self, *, channel_id: str, key: str, content: str, **_metadata) -> None:
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

    async def fetch_channel(_channel_id: int):
        return None

    client.fetch_channel = fetch_channel  # type: ignore[method-assign]
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


def test_channel_control_requires_the_entire_unquoted_action() -> None:
    client = DiscordIngressClient(store=FakeStore(), orchestrator=SimpleNamespace())
    client._connection.user = SimpleNamespace(id=99)

    assert client._channel_control("<@99> enable this channel for play") == "enable"
    assert client._channel_control("<@99> disable this channel for play") == "disable"
    assert client._channel_control("<@99> do not enable this channel for play") is None
    assert client._channel_control('"<@99> enable this channel for play"') is None
    assert client._channel_control("someone wrote: <@99> enable this channel for play") is None


@pytest.mark.asyncio
async def test_ingress_preserves_reply_metadata_and_explicitly_rejects_attachments() -> None:
    store = FakeStore()
    store.monitored.add("20")
    client = DiscordIngressClient(store=store, orchestrator=SimpleNamespace(), debounce_seconds=0)

    async def publish() -> bool:
        return False

    async def process(_channel_id: str) -> None:
        return None

    client._publish_outbox = publish  # type: ignore[method-assign]
    client._process_after_debounce = process  # type: ignore[method-assign]
    message = SimpleNamespace(
        id=101,
        guild=SimpleNamespace(id=7),
        channel=SimpleNamespace(id=20, guild=SimpleNamespace(id=7)),
        author=SimpleNamespace(id=30, bot=False),
        content="Use the note",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        attachments=(
            SimpleNamespace(id=501, filename="note.txt", content_type="text/plain", size=12),
        ),
        reference=SimpleNamespace(
            message_id=100,
            resolved=SimpleNamespace(content="Earlier context", author=SimpleNamespace(id=31)),
        ),
    )

    await client.on_message(message)
    await asyncio.gather(*client._scheduled.values())

    incoming = store.enqueued[0]
    assert incoming.guild_id == "7"
    assert incoming.reply_to_event_id == "100"
    assert incoming.reply_to_author_id == "31"
    assert incoming.reply_context == "Earlier context"
    assert incoming.attachments[0].filename == "note.txt"
    assert store.notices[0][1] == "attachments-unsupported:101"
    assert "cannot read Discord attachments" in store.notices[0][2]


@pytest.mark.asyncio
async def test_monitored_thread_reconciles_parent_context_before_enqueue() -> None:
    store = FakeStore()
    store.monitored.update({"10", "20"})
    client = DiscordIngressClient(
        store=store,
        orchestrator=SimpleNamespace(),
        debounce_seconds=0,
    )

    async def process(_channel_id: str) -> None:
        return None

    client._process_after_debounce = process  # type: ignore[method-assign]
    message = SimpleNamespace(
        id=102,
        channel=SimpleNamespace(
            id=20,
            guild=SimpleNamespace(id=7),
            parent_id=10,
            type="public_thread",
        ),
        author=SimpleNamespace(id=30, bot=False),
        content="continue",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    await client.on_message(message)
    await asyncio.gather(*client._scheduled.values())

    assert store.inherited_threads == [("20", "10", "7")]
    assert [incoming.event_id for incoming in store.enqueued] == ["102"]


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_command", ["/world exit", "/world_exit"])
async def test_thread_workspace_remains_ingress_accessible_after_parent_changes(
    tmp_path,
    exit_command,
) -> None:
    store = SQLiteStore(tmp_path / "thread-workspace.sqlite3")
    store.initialize()
    store.record_discord_channel(
        channel_id="10",
        guild_id="7",
        parent_channel_id=None,
        kind="text",
    )
    store.record_discord_channel(
        channel_id="20",
        guild_id="7",
        parent_channel_id="10",
        kind="thread",
    )
    store.enable_channel_monitoring("10")
    assert store.inherit_thread_context(
        channel_id="20",
        parent_channel_id="10",
        guild_id="7",
    )
    store.save_world_workspace(
        channel_id="20",
        world_id="draft",
        stage="collecting",
        brief="Independent thread draft",
        settings={},
        sources={},
        world=WorldState("draft", "Draft"),
    )
    store.create_world(WorldState("game-world", "Game World"))
    store.create_game(GameState("game", "game-world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="10", game_id="game")
    assert store.disable_channel_monitoring("10")
    assert store.channel_monitoring_enabled("20")

    client = DiscordIngressClient(
        store=store,
        orchestrator=SimpleNamespace(),
        debounce_seconds=0,
    )

    async def process(_channel_id: str) -> None:
        return None

    client._process_after_debounce = process  # type: ignore[method-assign]
    message = SimpleNamespace(
        id=103,
        channel=SimpleNamespace(
            id=20,
            guild=SimpleNamespace(id=7),
            parent_id=10,
            type="public_thread",
        ),
        author=SimpleNamespace(id=30, bot=False),
        content=exit_command,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    await client.on_message(message)
    await asyncio.gather(*client._scheduled.values())

    claimed = store.claim_pending(channel_id="20", limit=1)
    assert [incoming.event_id for incoming in claimed] == ["103"]
    assert store.pending_outbox(channel_id="20") == []

    class NeverComplete:
        async def complete(self, **_kwargs):
            raise AssertionError("world editor exit must remain deterministic")

    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverComplete()),
    )
    response = await app(claimed[0])

    assert store.world_workspace("20") is None
    assert "thread cannot safely inherit" not in response


@pytest.mark.asyncio
async def test_inbox_processing_fetches_uncached_thread_and_records_metadata() -> None:
    store = FakeStore()
    processed = []
    fetches = []

    class Typing:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class Thread:
        id = 20
        guild = SimpleNamespace(id=7)
        parent_id = 10
        type = "public_thread"

        def typing(self):
            return Typing()

    class Orchestrator:
        async def process_until_quiet(self, channel_id: str, *, quiet_seconds: float) -> None:
            processed.append((channel_id, quiet_seconds))

    client = DiscordIngressClient(
        store=store,
        orchestrator=Orchestrator(),
        debounce_seconds=0,
    )
    client.get_channel = lambda _channel_id: None  # type: ignore[method-assign]

    async def fetch_channel(channel_id: int):
        fetches.append(channel_id)
        return Thread()

    client.fetch_channel = fetch_channel  # type: ignore[method-assign]

    await client._process_after_debounce("20")

    assert fetches == [20]
    assert processed == [("20", 0)]
    assert store.available == ["20"]
    assert store.deferred == []
    assert store.recorded_channels == [("20", "7", "10", "public_thread")]


@pytest.mark.asyncio
async def test_outbox_fetches_uncached_thread_and_records_metadata() -> None:
    store = FakeStore()
    store.outbox = [{"id": 1, "channel_id": "20", "content": "hello"}]
    sent = []
    fetches = []

    class Thread:
        id = 20
        guild = SimpleNamespace(id=7)
        parent_id = 10
        type = "public_thread"

        async def send(self, **options):
            sent.append(options)

    client = DiscordIngressClient(store=store, orchestrator=SimpleNamespace())
    client.get_channel = lambda _channel_id: None  # type: ignore[method-assign]

    async def fetch_channel(channel_id: int):
        fetches.append(channel_id)
        return Thread()

    client.fetch_channel = fetch_channel  # type: ignore[method-assign]

    await client._publish_outbox()

    assert fetches == [20]
    assert sent == [{"content": "hello", "embed": None}]
    assert store.delivered == [1]
    assert store.failed == []
    assert store.recorded_channels == [("20", "7", "10", "public_thread")]


@pytest.mark.asyncio
async def test_unavailable_inbox_channel_is_deferred_without_immediate_loop() -> None:
    store = FakeStore()
    client = DiscordIngressClient(store=store, orchestrator=SimpleNamespace())
    client.get_channel = lambda _channel_id: None  # type: ignore[method-assign]
    fetches = []

    async def fetch_channel(channel_id: int):
        fetches.append(channel_id)
        return None

    client.fetch_channel = fetch_channel  # type: ignore[method-assign]

    await client._process_after_debounce("20")

    assert fetches == [20]
    assert store.deferred == [("20", "DiscordChannelUnavailable")]
    assert "20" not in client._scheduled


@pytest.mark.asyncio
async def test_runtime_provider_backoff_schedules_the_durable_future_wakeup() -> None:
    class DelayedStore(FakeStore):
        def pending_inbox_schedule(self):
            return [("20", 120)]

    class Typing:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *_args):
            return None

    class Channel:
        id = 20
        guild = SimpleNamespace(id=7)
        parent_id = None
        type = "text"

        def typing(self):
            return Typing()

    class FailingOrchestrator:
        async def process_until_quiet(self, channel_id: str, *, quiet_seconds: float) -> None:
            raise RuntimeError("transient provider failure")

    store = DelayedStore()
    client = DiscordIngressClient(
        store=store,
        orchestrator=FailingOrchestrator(),
        debounce_seconds=0,
    )
    client.get_channel = lambda _channel_id: Channel()  # type: ignore[method-assign]
    scheduled = []

    async def publish():
        return False

    async def process_after_delay(channel_id: str, delay_seconds: float) -> None:
        scheduled.append((channel_id, delay_seconds))

    client._publish_outbox = publish  # type: ignore[method-assign]
    client._process_after_delay = process_after_delay  # type: ignore[method-assign]

    await client._process_after_debounce("20")
    await client._scheduled["20"]

    assert store.available == ["20"]
    assert scheduled == [("20", 120)]


@pytest.mark.asyncio
async def test_send_success_then_mark_failure_retries_with_same_nonce_and_reference() -> None:
    class MarkFailingStore(FakeStore):
        def __init__(self) -> None:
            super().__init__()
            self.fail_mark = True
            self.discord_message_ids = []

        def mark_delivered(self, outbox_id: int, *, discord_message_id=None) -> None:
            if self.fail_mark:
                raise RuntimeError("database unavailable after Discord accepted the message")
            self.delivered.append(outbox_id)
            self.discord_message_ids.append(discord_message_id)

    row = {
        "id": 1,
        "channel_id": "20",
        "content": "answer",
        "kind": "response",
        "source_event_id": "101",
        "source_author_id": "30",
        "source_guild_id": "7",
        "discord_nonce": "stable-nonce",
    }
    store = MarkFailingStore()
    sent = []

    class Channel:
        guild = SimpleNamespace(id=7)

        async def send(self, **options):
            sent.append(options)
            return SimpleNamespace(id=9001)

    client = DiscordIngressClient(store=store, orchestrator=SimpleNamespace())
    client.get_channel = lambda _channel_id: Channel()  # type: ignore[method-assign]
    store.outbox = [row]

    with pytest.raises(RuntimeError, match="database unavailable"):
        await client._publish_outbox()
    assert store.released == [[1]]

    store.fail_mark = False
    store.outbox = [row]
    await client._publish_outbox()

    assert [options["nonce"] for options in sent] == ["stable-nonce", "stable-nonce"]
    assert all(options["reference"].message_id == 101 for options in sent)
    assert all(options["reference"].channel_id == 20 for options in sent)
    assert store.delivered == [1]
    assert store.discord_message_ids == ["9001"]
