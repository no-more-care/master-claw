import asyncio
from datetime import UTC, datetime
from pathlib import Path

from masterclaw.app.message_handler import MessageApplication
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.mechanics import CharacterSheet, Flag, FlagType, Trait
from masterclaw.domain.models import GameLifecycle, HandlerResponse, IncomingMessage
from masterclaw.domain.state import GameState, WorldState
from masterclaw.pipelines.action import create_action_pipeline
from masterclaw.pipelines.base import CompletionResult
from masterclaw.pipelines.compound_play import create_compound_play_pipeline
from masterclaw.pipelines.conversation import (
    create_roleplay_reply_pipeline,
    create_scene_question_pipeline,
)
from masterclaw.pipelines.state_decision import StateDecisionRouter
from masterclaw.storage.sqlite import SQLiteStore


class Completion:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    async def complete(self, **kwargs) -> CompletionResult:
        self.calls += 1
        return CompletionResult(self.response, used_tool=True)


class MustNotRun:
    async def complete(self, **kwargs) -> CompletionResult:
        raise AssertionError("conditional action must not execute before player confirmation")


def setup_store(tmp_path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(
        GameState(
            "game",
            "world",
            GameLifecycle.ACTIVE,
            narrative_channel_id="narrative",
        )
    )
    store.bind_channel(channel_id="game", game_id="game")
    store.create_scene(
        scene_id="room",
        game_id="game",
        title="Rune Room",
        state={"facts": ["The runes are inert"]},
    )
    store.create_character(
        CharacterState(
            "hero",
            "game",
            "alice",
            "Bio",
            CharacterSheet(
                "Mara",
                (Trait("Lore", 2, ("Runes", "Archives")),),
                (
                    Flag("Protect Dorn", FlagType.RELATIONSHIP),
                    Flag("Knowledge has a price", FlagType.BELIEF),
                    Flag("Open the archive", FlagType.GOAL),
                ),
            ),
        )
    )
    store.place_player(game_id="game", player_id="alice", scene_id="room")
    return store


def message(event_id: str, content: str) -> IncomingMessage:
    return IncomingMessage(
        event_id=event_id,
        channel_id="game",
        author_id="alice",
        content=content,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_question_then_conditional_action_waits_for_confirmation(tmp_path) -> None:
    store = setup_store(tmp_path)
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"compound_play","argument":null,"confidence":1,'
                '"evidence":"question followed by conditional action"}'
            )
        ),
        compound_play_pipeline=create_compound_play_pipeline(
            Completion(
                """
                {
                  "parts": [
                    {
                      "kind": "scene_question",
                      "text": "Безопасны ли руны?",
                      "conditional_on_previous": false
                    },
                    {
                      "kind": "action",
                      "text": "Если руны безопасны, открываю дверь.",
                      "conditional_on_previous": true
                    }
                  ],
                  "clarification_question": null
                }
                """
            )
        ),
        scene_question_pipeline=create_scene_question_pipeline(
            Completion('{"reply":"Руны инертны и не выглядят опасными."}')
        ),
        action_pipeline=create_action_pipeline(MustNotRun()),
    )

    result = asyncio.run(
        app(
            message(
                "conditional",
                "Безопасны ли руны? Если да, открываю дверь.",
            )
        )
    )

    assert isinstance(result, str)
    assert "Руны инертны" in result
    assert "подтвердите действие" in result
    assert "открываю дверь" in result
    pending = store.open_pending(game_id="game", player_id="alice")
    assert pending is not None
    assert pending.payload["compound_action"] == "Если руны безопасны, открываю дверь."


def test_confirmed_conditional_action_runs_the_deferred_declaration(tmp_path) -> None:
    store = setup_store(tmp_path)
    first_app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"compound_play","argument":null,"confidence":1,'
                '"evidence":"question followed by conditional action"}'
            )
        ),
        compound_play_pipeline=create_compound_play_pipeline(
            Completion(
                """
                {
                  "parts": [
                    {
                      "kind": "scene_question",
                      "text": "Безопасны ли руны?",
                      "conditional_on_previous": false
                    },
                    {
                      "kind": "action",
                      "text": "Открываю дверь, сверяясь с рунами.",
                      "conditional_on_previous": true
                    }
                  ],
                  "clarification_question": null
                }
                """
            )
        ),
        scene_question_pipeline=create_scene_question_pipeline(
            Completion('{"reply":"Руны инертны и не выглядят опасными."}')
        ),
        action_pipeline=create_action_pipeline(MustNotRun()),
    )
    asyncio.run(first_app(message("conditional", "Безопасны ли руны? Если да, открываю дверь.")))

    confirm_app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(MustNotRun()),
        action_pipeline=create_action_pipeline(
            Completion(
                '{"resolution":"roll","trait_names":["Lore"],'
                '"aspect_names":["Runes"],"flag":null,"bonus_ids":[],'
                '"difficulty":2,"evidence":["sealed door"],'
                '"clarification_question":null}'
            )
        ),
    )
    result = asyncio.run(confirm_app(message("confirm", "да")))

    assert isinstance(result, str)
    assert "Пул" in result
    pending = store.open_pending(game_id="game", player_id="alice")
    assert pending is not None
    assert pending.kind.value == "pool_confirmation"
    assert pending.payload["declaration"] == "Открываю дверь, сверяясь с рунами."


def test_declined_conditional_action_cancels_the_pending(tmp_path) -> None:
    store = setup_store(tmp_path)
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"compound_play","argument":null,"confidence":1,'
                '"evidence":"question followed by conditional action"}'
            )
        ),
        compound_play_pipeline=create_compound_play_pipeline(
            Completion(
                """
                {
                  "parts": [
                    {
                      "kind": "scene_question",
                      "text": "Безопасны ли руны?",
                      "conditional_on_previous": false
                    },
                    {
                      "kind": "action",
                      "text": "Открываю дверь.",
                      "conditional_on_previous": true
                    }
                  ],
                  "clarification_question": null
                }
                """
            )
        ),
        scene_question_pipeline=create_scene_question_pipeline(
            Completion('{"reply":"Руны инертны."}')
        ),
        action_pipeline=create_action_pipeline(MustNotRun()),
    )
    asyncio.run(app(message("conditional", "Безопасны ли руны? Если да, открываю дверь.")))

    result = asyncio.run(app(message("decline", "нет")))

    assert isinstance(result, str)
    assert store.open_pending(game_id="game", player_id="alice") is None


def test_roleplay_then_action_preserves_both_parts_and_opens_one_roll(tmp_path) -> None:
    store = setup_store(tmp_path)
    roleplay = Completion('{"reply":"Страж молча отступает от двери."}')
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"compound_play","argument":null,"confidence":1,'
                '"evidence":"speech followed by action"}'
            )
        ),
        compound_play_pipeline=create_compound_play_pipeline(
            Completion(
                """
                {
                  "parts": [
                    {
                      "kind": "roleplay",
                      "text": "Я говорю стражу: отойди.",
                      "conditional_on_previous": false
                    },
                    {
                      "kind": "action",
                      "text": "Открываю дверь, сверяясь с рунами.",
                      "conditional_on_previous": false
                    }
                  ],
                  "clarification_question": null
                }
                """
            )
        ),
        roleplay_reply_pipeline=create_roleplay_reply_pipeline(roleplay),
        action_pipeline=create_action_pipeline(
            Completion(
                '{"resolution":"roll","trait_names":["Lore"],'
                '"aspect_names":["Runes"],"flag":null,"bonus_ids":[],'
                '"difficulty":2,"evidence":["sealed door"],'
                '"clarification_question":null}'
            )
        ),
    )

    result = asyncio.run(
        app(
            message(
                "roleplay-action",
                "Я прошу стража отойти и открываю дверь, сверяясь с рунами.",
            )
        )
    )

    assert isinstance(result, HandlerResponse)
    assert "Сцена продолжается" in result.text
    assert "Пул" in result.text
    assert result.deliveries[0].content == "Страж молча отступает от двери."
    assert roleplay.calls == 1
    assert store.open_pending(game_id="game", player_id="alice") is not None
