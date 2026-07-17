import asyncio
from datetime import UTC, datetime
from pathlib import Path

from masterclaw.app.message_handler import MessageApplication
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.mechanics import CharacterSheet, Flag, FlagType, Trait
from masterclaw.domain.models import GameLifecycle, IncomingMessage
from masterclaw.domain.state import GameState, WorldState
from masterclaw.pipelines.base import CompletionResult
from masterclaw.pipelines.conversation import create_rules_question_pipeline
from masterclaw.pipelines.state_decision import StateDecisionRouter
from masterclaw.pipelines.world_intake import create_world_intake_pipeline
from masterclaw.storage.sqlite import SQLiteStore


def test_scene_information_request_skips_intent_llm(tmp_path) -> None:
    class NeverCompletion:
        async def complete(self, **kwargs):
            raise AssertionError("deterministic information request must not call an LLM")

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "Тихий город"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="game")
    store.create_scene(
        scene_id="room",
        game_id="game",
        title="Читальный зал",
        state={"description": "Высокие стеллажи скрываются в полумраке."},
    )
    store.place_player(game_id="game", player_id="alice", scene_id="room")
    sheet = CharacterSheet(
        "Мира",
        tuple(Trait(f"Черта {i}", 3, tuple(f"Аспект {i}.{n}" for n in range(3))) for i in range(6)),
        (
            Flag("Друг", FlagType.RELATIONSHIP),
            Flag("Цель", FlagType.GOAL),
            Flag("Убеждение", FlagType.BELIEF),
        ),
    )
    store.create_character(CharacterState("hero", "game", "alice", "Следователь", sheet))
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverCompletion()),
    )

    response = asyncio.run(
        app(
            IncomingMessage(
                event_id="info",
                channel_id="channel",
                author_id="alice",
                content="Что вокруг?",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    assert "Читальный зал" in response
    assert "Высокие стеллажи" in response
    assert "Мира" in response
    with store.connect() as connection:
        stages = {row["stage"] for row in connection.execute("SELECT stage FROM stage_spans")}
        llm_calls = connection.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
    assert {"message.total", "application.dispatch", "response.status_panel"} <= stages
    assert llm_calls == 0


def test_empty_world_catalog_skips_intent_llm(tmp_path) -> None:
    class NeverCompletion:
        async def complete(self, **kwargs):
            raise AssertionError("world catalogue must not call an LLM")

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverCompletion()),
    )
    response = asyncio.run(
        app(
            IncomingMessage(
                event_id="worlds",
                channel_id="channel",
                author_id="alice",
                content="Какие миры доступны?",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )
    assert "Готовых миров пока нет" in response


def test_exact_world_list_phrase_skips_intent_llm(tmp_path) -> None:
    class NeverCompletion:
        async def complete(self, **kwargs):
            raise AssertionError("world catalogue must not call an LLM")

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverCompletion()),
    )
    response = asyncio.run(
        app(
            IncomingMessage(
                event_id="worlds-exact",
                channel_id="channel",
                author_id="alice",
                content="Какие есть миры?",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )
    assert "Готовых миров пока нет" in response


def test_world_list_phrase_does_not_escape_or_revise_active_editor(tmp_path) -> None:
    class NeverCompletion:
        async def complete(self, **kwargs):
            raise AssertionError("world catalogue must not call the revision LLM")

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("draft", "Черновик"))
    store.save_world_workspace(
        channel_id="channel",
        world_id="draft",
        stage="collecting",
        brief="Достаточно подробное описание временного черновика мира.",
        settings={},
        sources={},
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverCompletion()),
        world_intake_pipeline=create_world_intake_pipeline(NeverCompletion()),
    )
    response = asyncio.run(
        app(
            IncomingMessage(
                event_id="worlds-during-draft",
                channel_id="channel",
                author_id="alice",
                content="Покажи список миров",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )
    assert "не уверен" in response
    assert "ДОСТУПНЫЕ МИРЫ" not in response
    assert store.world_workspace("channel")["brief"] == (
        "Достаточно подробное описание временного черновика мира."
    )


def test_new_world_request_cannot_corrupt_active_review_workspace(tmp_path) -> None:
    class NeverCompletion:
        async def complete(self, **kwargs):
            raise AssertionError("active workspace conflict must be resolved without an LLM")

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("draft", "Черновик"))
    store.save_world_workspace(
        channel_id="channel",
        world_id="draft",
        stage="review",
        brief="Проверяемый черновик мира.",
        settings={},
        sources={},
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverCompletion()),
        world_intake_pipeline=create_world_intake_pipeline(NeverCompletion()),
    )

    response = asyncio.run(
        app(
            IncomingMessage(
                event_id="conflicting-world",
                channel_id="channel",
                author_id="alice",
                content="Создай новый мир",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    assert "уже открыт черновик" in response
    assert store.world_workspace("channel")["world_id"] == "draft"
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM world_projects").fetchone()[0] == 1


def test_rules_question_in_collecting_is_routed_by_closed_state_model(tmp_path) -> None:
    class StateCompletion:
        async def complete(self, **kwargs) -> CompletionResult:
            assert kwargs["output_type"].__name__ == "WorldEditingCollectingStateDecision"
            return CompletionResult(
                '{"command":"show_rules","argument":null,"confidence":1,'
                '"evidence":"rules question"}',
                used_tool=True,
            )

    class RulesCompletion:
        async def complete(self, **kwargs) -> CompletionResult:
            return CompletionResult(
                '{"reply":"Для проверки соберите пул и сравните успехи со сложностью."}',
                used_tool=True,
            )

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("draft", "Черновик"))
    store.save_world_workspace(
        channel_id="channel",
        world_id="draft",
        stage="collecting",
        brief="Исходный бриф без изменений.",
        settings={},
        sources={},
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(StateCompletion()),
        rules_question_pipeline=create_rules_question_pipeline(RulesCompletion()),
    )

    response = asyncio.run(
        app(
            IncomingMessage(
                event_id="rules-in-editor",
                channel_id="channel",
                author_id="alice",
                content="Как работает бросок кубов?",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    assert "соберите пул" in response
    assert store.world_workspace("channel")["brief"] == "Исходный бриф без изменений."
