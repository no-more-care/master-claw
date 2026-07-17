import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from masterclaw.app.message_handler import MessageApplication
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.models import GameLifecycle, IncomingMessage
from masterclaw.domain.state import (
    GameState,
    PendingInteraction,
    PendingKind,
    PendingStatus,
    WorldState,
)
from masterclaw.pipelines.base import CompletionResult
from masterclaw.pipelines.state_decision import StateDecisionRouter
from masterclaw.storage.sqlite import SQLiteStore
from masterclaw.telemetry import current_game_id


class FakeCompletion:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, **kwargs) -> CompletionResult:
        self.calls += 1
        assert '"value":"world_management"' in kwargs["context"]
        return CompletionResult(
            '{"command":"clarify","argument":null,"confidence":1,"evidence":"needs clarification"}',
            used_tool=True,
        )


def test_unbound_message_gets_world_mode_before_llm_classification(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(FakeCompletion()),
    )
    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="1", channel_id="channel", author_id="alice", content="Создай мир"
            )
        )
    )
    assert "управление мирами" in response.lower()
    assert "пайплайн генерации миров не настроен" in response.lower()


def test_slash_command_does_not_spend_an_llm_call(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    completion = FakeCompletion()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(completion),
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
        state_router=StateDecisionRouter(completion),
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
        async def complete(self, **kwargs) -> CompletionResult:
            assert current_game_id() == "game"
            return CompletionResult(
                '{"command":"clarify","argument":null,"confidence":1,"evidence":"test"}',
                used_tool=True,
            )

    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(SessionAwareCompletion()),
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


def test_invalid_state_decision_uses_scenario_fallback(tmp_path) -> None:
    class InvalidStateCompletion:
        async def complete(self, **kwargs) -> CompletionResult:
            return CompletionResult("not-json", used_tool=True)

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(InvalidStateCompletion()),
    )

    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="invalid-state",
                channel_id="channel",
                author_id="alice",
                content="Не вполне понимаю, куда двигаться дальше",
            )
        )
    )

    assert "не уверен" in response


def test_invalid_collecting_decision_clarifies_without_revising_world(tmp_path) -> None:
    class InvalidStateCompletion:
        async def complete(self, **kwargs) -> CompletionResult:
            return CompletionResult("not-json", used_tool=True)

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.save_world_workspace(
        channel_id="channel",
        world_id="world",
        stage="collecting",
        brief="Original brief",
        settings={},
        sources={},
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(InvalidStateCompletion()),
    )

    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="invalid-collecting",
                channel_id="channel",
                author_id="alice",
                content="Не вполне понимаю, что делать дальше",
            )
        )
    )

    assert "не уверен" in response
    assert store.world_workspace("channel")["brief"] == "Original brief"


def test_low_confidence_world_revision_clarifies_without_mutation(tmp_path) -> None:
    class LowConfidenceRevision:
        async def complete(self, **kwargs) -> CompletionResult:
            return CompletionResult(
                '{"command":"revise_world","argument":null,"confidence":0.01,'
                '"evidence":"uncertain revision"}',
                used_tool=True,
            )

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.save_world_workspace(
        channel_id="channel",
        world_id="world",
        stage="collecting",
        brief="Original brief",
        settings={},
        sources={},
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(LowConfidenceRevision()),
    )

    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="uncertain-revision",
                channel_id="channel",
                author_id="alice",
                content="Может быть, стоит что-то поменять",
            )
        )
    )

    assert "не уверен" in response
    assert store.world_workspace("channel")["brief"] == "Original brief"


@pytest.mark.parametrize("confidence", [0.01, 0.99])
def test_llm_decision_cannot_publish_world_without_explicit_phrase(tmp_path, confidence) -> None:
    class ImplicitWorldApproval:
        def __init__(self, value: float) -> None:
            self.value = value

        async def complete(self, **kwargs) -> CompletionResult:
            return CompletionResult(
                '{"command":"confirm_world","argument":null,'
                f'"confidence":{self.value},"evidence":"positive review"}}',
                used_tool=True,
            )

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    world = store.world_state("world")
    store.update_world_content(
        world_id="world",
        expected_revision=world.revision,
        content={"locations": [{"location_id": "start", "name": "Start"}]},
    )
    store.save_world_workspace(
        channel_id="channel",
        world_id="world",
        stage="review",
        brief="Review brief",
        settings={},
        sources={},
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(ImplicitWorldApproval(confidence)),
    )

    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="implicit-approval",
                channel_id="channel",
                author_id="alice",
                content="Выглядит здорово, мне нравится",
            )
        )
    )

    assert "не уверен" in response
    assert store.world_state("world").status == "draft"
    assert store.world_workspace("channel") is not None


def test_confidence_point_zero_one_does_not_start_game(tmp_path) -> None:
    class LowConfidenceStart:
        async def complete(self, **kwargs) -> CompletionResult:
            return CompletionResult(
                '{"command":"start_game","argument":null,"confidence":0.01,'
                '"evidence":"uncertain start"}',
                used_tool=True,
            )

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.PREPARING))
    store.bind_channel(channel_id="channel", game_id="game")
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(LowConfidenceStart()),
    )

    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="uncertain-start",
                channel_id="channel",
                author_id="alice",
                content="Наверное, уже можно переходить дальше",
            )
        )
    )

    assert "не уверен" in response
    assert store.game_state("game").lifecycle is GameLifecycle.PREPARING


def test_oversized_message_is_rejected_before_any_model_call(tmp_path) -> None:
    class NeverCompletion:
        async def complete(self, **kwargs):
            raise AssertionError("oversized input must not reach a model")

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverCompletion()),
    )
    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="too-long",
                channel_id="channel",
                author_id="alice",
                content="x" * 12_001,
            )
        )
    )
    assert "слишком длинное" in response


def test_stale_pool_confirmation_is_cancelled_with_a_reminder(tmp_path) -> None:
    class NeverCompletion:
        async def complete(self, **kwargs):
            raise AssertionError("expired pending must not reach a model")

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="game")
    store.put_pending(
        PendingInteraction(
            "old-pool",
            "game",
            "alice",
            None,
            PendingKind.POOL_CONFIRMATION,
            "Подтвердите пул",
        )
    )
    with store.transaction() as connection:
        connection.execute(
            "UPDATE pending_interactions SET created_at = ? WHERE interaction_id = ?",
            ("2026-01-01T00:00:00+00:00", "old-pool"),
        )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverCompletion()),
    )
    response = asyncio.run(
        app(
            IncomingMessage(
                event_id="after-a-day",
                channel_id="channel",
                author_id="alice",
                content="Что происходит?",
                created_at=datetime(2026, 1, 2, 1, tzinfo=UTC),
            )
        )
    )
    assert "подтверждение пула отменено" in response
    assert store.open_pending(game_id="game", player_id="alice") is None
    assert store.pending_by_id("old-pool").status is PendingStatus.EXPIRED


def test_stale_choice_answer_is_recorded_instead_of_being_blocked_by_reminder(
    tmp_path,
) -> None:
    class AnswerPending:
        async def complete(self, **kwargs) -> CompletionResult:
            return CompletionResult(
                '{"command":"answer_pending","argument":null,"confidence":1,'
                '"evidence":"answers the pending choice"}',
                used_tool=True,
            )

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="game")
    store.put_pending(
        PendingInteraction(
            "old-choice",
            "game",
            "alice",
            None,
            PendingKind.CHOICE,
            "Какую дверь выбрать?",
            payload={"options": ["левая", "правая"]},
        )
    )
    with store.transaction() as connection:
        connection.execute(
            "UPDATE pending_interactions SET created_at = ? WHERE interaction_id = ?",
            ("2026-01-01T00:00:00+00:00", "old-choice"),
        )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(AnswerPending()),
    )

    response = asyncio.run(
        app(
            IncomingMessage(
                event_id="answer-old-choice",
                channel_id="channel",
                author_id="alice",
                content="Выбираю левую дверь",
                created_at=datetime(2026, 1, 2, 1, tzinfo=UTC),
            )
        )
    )

    assert "ответ принят" in response.lower()
    resolved = store.pending_by_id("old-choice")
    assert resolved.status is PendingStatus.RESOLVED
    assert resolved.payload["answer"] == "Выбираю левую дверь"
