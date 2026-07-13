import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from masterclaw.app.message_handler import MessageApplication
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.models import GameLifecycle, IncomingMessage
from masterclaw.domain.state import GameState, WorldState
from masterclaw.pipelines.intent import create_intent_pipeline
from masterclaw.storage.sqlite import SQLiteStore
from masterclaw.telemetry import current_game_id


class FakeCompletion:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, *, system: str, user: str) -> str:
        self.calls += 1
        assert '"value":"world_management"' in user
        return '{"intent":"command","confidence":1,"evidence":"explicit request"}'


def test_unbound_message_gets_world_mode_before_llm_classification(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        intent_pipeline=create_intent_pipeline(FakeCompletion()),
    )
    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="1", channel_id="channel", author_id="alice", content="Создай мир"
            )
        )
    )
    assert "управление миром" in response
    assert "command" in response


def test_slash_command_does_not_spend_an_llm_call(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    completion = FakeCompletion()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        intent_pipeline=create_intent_pipeline(completion),
    )
    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="1", channel_id="channel", author_id="alice", content="/world create"
            )
        )
    )
    assert "/world create" in response
    assert completion.calls == 0


def test_active_game_message_updates_code_driven_activity_clock(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="game")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    store.start_activity_clock(game_id="game", started_at=start)
    completion = FakeCompletion()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        intent_pipeline=create_intent_pipeline(completion),
    )
    asyncio.run(
        app(
            IncomingMessage(
                event_id="1",
                channel_id="channel",
                author_id="alice",
                content="/status",
                created_at=start + timedelta(minutes=9),
            )
        )
    )
    assert store.activity_state("game")["active_seconds"] == 300
    assert completion.calls == 0


def test_llm_call_is_bound_to_the_channel_game_for_telemetry(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="game")

    class SessionAwareCompletion:
        async def complete(self, *, system: str, user: str) -> str:
            assert current_game_id() == "game"
            return '{"intent":"ambiguous","confidence":1,"evidence":"test"}'

    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        intent_pipeline=create_intent_pipeline(SessionAwareCompletion()),
    )
    asyncio.run(
        app(
            IncomingMessage.now(
                event_id="telemetry",
                channel_id="channel",
                author_id="alice",
                content="Осматриваюсь",
            )
        )
    )
    assert current_game_id() is None
