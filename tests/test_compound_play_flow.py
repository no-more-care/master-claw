import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from masterclaw.app.i18n import tr
from masterclaw.app.message_handler import MessageApplication
from masterclaw.context.assembler import ContextAssembler
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.mechanics import CharacterSheet, Flag, FlagType, Trait
from masterclaw.domain.models import GameLifecycle, HandlerResponse, IncomingMessage
from masterclaw.domain.state import GameState, WorldState
from masterclaw.pipelines.action import create_action_pipeline
from masterclaw.pipelines.base import CompletionResult, TransientProviderError
from masterclaw.pipelines.compound_play import create_compound_play_pipeline
from masterclaw.pipelines.consequence import create_consequence_pipeline
from masterclaw.pipelines.conversation import (
    create_roleplay_reply_pipeline,
    create_scene_question_pipeline,
)
from masterclaw.pipelines.reserve_recovery import create_reserve_recovery_pipeline
from masterclaw.pipelines.state_decision import StateDecisionRouter
from masterclaw.storage.sqlite import SQLiteStore


class Completion:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    async def complete(self, **kwargs) -> CompletionResult:
        self.calls += 1
        return CompletionResult(self.response, used_tool=True)


class SequenceCompletion:
    def __init__(self, *responses: str) -> None:
        self.responses = iter(responses)
        self.calls = 0

    async def complete(self, **kwargs) -> CompletionResult:
        self.calls += 1
        return CompletionResult(next(self.responses), used_tool=True)


class MustNotRun:
    async def complete(self, **kwargs) -> CompletionResult:
        raise AssertionError("conditional action must not execute before player confirmation")


def setup_store(tmp_path, *, reserve_current: int = 7) -> SQLiteStore:
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
                reserve_current=reserve_current,
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


def test_compound_help_fails_closed_and_requires_standalone_assist(
    tmp_path,
    monkeypatch,
) -> None:
    store = setup_store(tmp_path, reserve_current=3)
    original = store.character_for_player(game_id="game", player_id="alice")
    assert original is not None
    offer_help_calls = 0

    def forbidden_offer_help(**kwargs):
        nonlocal offer_help_calls
        offer_help_calls += 1
        raise AssertionError("compound HELP must not mutate an open roll")

    monkeypatch.setattr(store, "offer_help", forbidden_offer_help)
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"compound_play","argument":null,"confidence":1,'
                '"evidence":"help plus status"}'
            )
        ),
        compound_play_pipeline=create_compound_play_pipeline(
            SequenceCompletion(
                '{"parts":[{"kind":"help","text":"I help Bob",'
                '"conditional_on_previous":false},'
                '{"kind":"scene_status","text":"What is happening?",'
                '"conditional_on_previous":false}],'
                '"clarification_question":null}',
                '{"parts":[],"clarification_question":'
                '"Please submit the help offer as a standalone request."}',
            )
        ),
    )

    result = asyncio.run(app(message("compound-help", "I help Bob; what is happening?")))

    current = store.character_for_player(game_id="game", player_id="alice")
    scene = store.scene_projection(game_id="game", player_id="alice")
    assert "Please submit the help offer as a standalone request." in result
    assert offer_help_calls == 0
    assert current == original
    assert scene is not None
    assert scene["scene_revision"] == 0
    pending = store.open_pending(game_id="game", player_id="alice")
    assert pending is not None
    assert pending.prompt == "Please submit the help offer as a standalone request."


def test_multiple_roleplay_fragments_are_repaired_and_cannot_multi_award(
    tmp_path,
) -> None:
    store = setup_store(tmp_path, reserve_current=5)
    decomposition = SequenceCompletion(
        '{"parts":[{"kind":"roleplay","text":"I greet the guard."},'
        '{"kind":"roleplay","text":"I thank the keeper."}],'
        '"clarification_question":null}',
        '{"parts":[{"kind":"roleplay",'
        '"text":"I greet the guard and thank the keeper."},'
        '{"kind":"scene_status","text":"Show me the scene."}],'
        '"clarification_question":null}',
    )
    recovery = Completion(
        '{"safe_rest_completed":false,"safe_rest_reason":null,'
        '"awards":[{"player_id":"alice","reason":"Strong in-character roleplay."}]}'
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"compound_play","argument":null,"confidence":1,'
                '"evidence":"two roleplay fragments plus status"}'
            )
        ),
        compound_play_pipeline=create_compound_play_pipeline(decomposition),
        roleplay_reply_pipeline=create_roleplay_reply_pipeline(
            Completion('{"reply":"The guard and keeper acknowledge you."}')
        ),
        reserve_recovery_pipeline=create_reserve_recovery_pipeline(recovery),
    )

    asyncio.run(
        app(
            message(
                "compound-roleplay-merge",
                "I greet the guard, thank the keeper, and look around.",
            )
        )
    )

    character = store.character_for_player(game_id="game", player_id="alice")
    assert character is not None
    assert decomposition.calls == 2
    assert recovery.calls == 1
    assert character.sheet.reserve_current == 6
    interactions = [
        event
        for event in store.recent_domain_events(game_id="game", limit=100)
        if event["event_type"] == "interaction_recorded"
    ]
    assert len(interactions) == 1


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
    assert pending.payload["condition_request"] == "Безопасны ли руны?"
    assert pending.payload["condition_answer"] == ("Руны инертны и не выглядят опасными.")
    assert pending.payload["condition_part_event_id"] == "conditional:part:0"


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
    state = Completion(
        '{"command":"compound_play","argument":null,"confidence":1,'
        '"evidence":"speech followed by action"}'
    )
    compound = Completion(
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
    roleplay = Completion('{"reply":"Страж молча отступает от двери."}')
    action = Completion(
        '{"resolution":"roll","trait_names":["Lore"],'
        '"aspect_names":["Runes"],"flag":null,"bonus_ids":[],'
        '"difficulty":2,"evidence":["sealed door"],'
        '"clarification_question":null}'
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(state),
        compound_play_pipeline=create_compound_play_pipeline(compound),
        roleplay_reply_pipeline=create_roleplay_reply_pipeline(roleplay),
        action_pipeline=create_action_pipeline(action),
    )

    original = message(
        "roleplay-action",
        "Я прошу стража отойти и открываю дверь, сверяясь с рунами.",
    )
    result = asyncio.run(app(original))

    assert isinstance(result, HandlerResponse)
    assert "Сцена продолжается" in result.text
    assert "Пул" in result.text
    assert result.deliveries[0].content == "Страж молча отступает от двери."
    pending = store.open_pending(game_id="game", player_id="alice")
    assert pending is not None
    assert pending.payload["prompt_source_event_id"] == "roleplay-action:part:1"
    assert pending.payload["root_source_event_id"] == "roleplay-action"

    replayed = asyncio.run(app(original))

    assert isinstance(replayed, str)
    assert "Пул" in replayed
    assert state.calls == 1
    assert compound.calls == 1
    assert action.calls == 1
    assert roleplay.calls == 1
    assert (
        store.open_pending(game_id="game", player_id="alice").interaction_id
        == pending.interaction_id
    )


def test_compound_xp_status_uses_the_requesting_character(tmp_path) -> None:
    store = setup_store(tmp_path)
    with store.transaction() as connection:
        connection.execute(
            "UPDATE characters SET experience_earned = 5 WHERE character_id = ?",
            ("hero",),
        )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"compound_play","argument":null,"confidence":1,'
                '"evidence":"two status questions"}'
            )
        ),
        compound_play_pipeline=create_compound_play_pipeline(
            Completion(
                """
                {
                  "parts": [
                    {"kind":"xp_status","text":"Сколько у меня XP?",
                     "conditional_on_previous":false},
                    {"kind":"game_status","text":"Какой статус игры?",
                     "conditional_on_previous":false}
                  ],
                  "clarification_question": null
                }
                """
            )
        ),
    )

    result = asyncio.run(app(message("compound-xp", "Сколько у меня XP и каков статус игры?")))

    assert isinstance(result, str)
    assert "XP персонажа: доступно 5, заработано 5, потрачено 0" in result


def test_late_transient_compound_failure_leaves_no_earlier_effects_and_can_retry(
    tmp_path,
) -> None:
    class FlakyAction:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, **kwargs) -> CompletionResult:
            self.calls += 1
            if self.calls == 1:
                raise TransientProviderError("temporary action outage")
            return CompletionResult(
                '{"resolution":"roll","trait_names":["Lore"],'
                '"aspect_names":["Runes"],"flag":null,"bonus_ids":[],'
                '"difficulty":2,"evidence":["sealed door"],'
                '"clarification_question":null}',
                used_tool=True,
            )

    store = setup_store(tmp_path)
    roleplay = Completion('{"reply":"Страж молча отступает от двери."}')
    action = FlakyAction()
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
                    {"kind":"roleplay","text":"Я прошу стража отойти.",
                     "conditional_on_previous":false},
                    {"kind":"action","text":"Открываю дверь, сверяясь с рунами.",
                     "conditional_on_previous":false}
                  ],
                  "clarification_question": null
                }
                """
            )
        ),
        roleplay_reply_pipeline=create_roleplay_reply_pipeline(roleplay),
        action_pipeline=create_action_pipeline(action),
    )
    request = message(
        "late-transient",
        "Я прошу стража отойти и открываю дверь, сверяясь с рунами.",
    )
    events_before = store.recent_domain_events(game_id="game", limit=100)
    activity_before = store.activity_state("game")

    with pytest.raises(TransientProviderError):
        asyncio.run(app(request))

    assert store.recent_domain_events(game_id="game", limit=100) == events_before
    assert store.activity_state("game") == activity_before
    assert store.open_pending(game_id="game", player_id="alice") is None

    retried = asyncio.run(app(request))
    assert isinstance(retried, HandlerResponse)
    assert "Пул" in retried.text
    assert roleplay.calls == 1
    assert action.calls == 2
    assert store.open_pending(game_id="game", player_id="alice") is not None


def test_late_schema_failure_in_compound_leaves_no_earlier_effects(tmp_path) -> None:
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
                    {"kind":"roleplay","text":"Я прошу стража отойти.",
                     "conditional_on_previous":false},
                    {"kind":"action","text":"Открываю дверь, сверяясь с рунами.",
                     "conditional_on_previous":false}
                  ],
                  "clarification_question": null
                }
                """
            )
        ),
        roleplay_reply_pipeline=create_roleplay_reply_pipeline(roleplay),
        action_pipeline=create_action_pipeline(Completion('{"resolution":"roll"}')),
    )
    events_before = store.recent_domain_events(game_id="game", limit=100)
    activity_before = store.activity_state("game")

    result = asyncio.run(
        app(
            message(
                "late-schema",
                "Я прошу стража отойти и открываю дверь, сверяясь с рунами.",
            )
        )
    )

    assert isinstance(result, str)
    assert tr("ru", manifest_for(PipelineName.COMPOUND_PLAY).on_invalid.value) in result
    assert roleplay.calls == 1
    assert store.recent_domain_events(game_id="game", limit=100) == events_before
    assert store.activity_state("game") == activity_before
    assert store.open_pending(game_id="game", player_id="alice") is None


def test_late_automatic_consequence_schema_failure_leaves_no_compound_effects(
    tmp_path,
) -> None:
    store = setup_store(tmp_path)
    roleplay = Completion('{"reply":"Страж молча отступает от двери."}')
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"compound_play","argument":null,"confidence":1,'
                '"evidence":"speech followed by routine action"}'
            )
        ),
        compound_play_pipeline=create_compound_play_pipeline(
            Completion(
                """
                {
                  "parts": [
                    {"kind":"roleplay","text":"Я прошу стража отойти.",
                     "conditional_on_previous":false},
                    {"kind":"action","text":"Открываю незапертую дверь.",
                     "conditional_on_previous":false}
                  ],
                  "clarification_question": null
                }
                """
            )
        ),
        roleplay_reply_pipeline=create_roleplay_reply_pipeline(roleplay),
        action_pipeline=create_action_pipeline(
            Completion(
                '{"resolution":"automatic","trait_names":[],"aspect_names":[],'
                '"flag":null,"bonus_ids":[],"difficulty":null,'
                '"evidence":["door is unlocked"],"clarification_question":null}'
            )
        ),
        consequence_pipeline=create_consequence_pipeline(Completion("not valid JSON")),
    )
    events_before = store.recent_domain_events(game_id="game", limit=100)
    activity_before = store.activity_state("game")

    result = asyncio.run(
        app(
            message(
                "late-consequence-schema",
                "Я прошу стража отойти и открываю незапертую дверь.",
            )
        )
    )

    assert isinstance(result, str)
    assert (
        tr(
            "ru",
            manifest_for(PipelineName.CONSEQUENCE_PLANNING).on_invalid.value,
        )
        in result
    )
    assert roleplay.calls == 1
    assert store.recent_domain_events(game_id="game", limit=100) == events_before
    assert store.activity_state("game") == activity_before
    assert store.open_pending(game_id="game", player_id="alice") is None


def test_roleplay_precedes_preflighted_automatic_action_in_canonical_history(tmp_path) -> None:
    store = setup_store(tmp_path)
    consequence = Completion(
        '{"summary":"The unlocked door opens","add_facts":["The archive door is open"]}'
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"compound_play","argument":null,"confidence":1,'
                '"evidence":"speech followed by routine action"}'
            )
        ),
        compound_play_pipeline=create_compound_play_pipeline(
            Completion(
                """
                {
                  "parts": [
                    {"kind":"roleplay","text":"I ask the guard to step aside.",
                     "conditional_on_previous":false},
                    {"kind":"action","text":"I open the unlocked archive door.",
                     "conditional_on_previous":false}
                  ],
                  "clarification_question": null
                }
                """
            )
        ),
        roleplay_reply_pipeline=create_roleplay_reply_pipeline(
            Completion('{"reply":"The guard steps aside."}')
        ),
        action_pipeline=create_action_pipeline(
            Completion(
                '{"resolution":"automatic","trait_names":[],"aspect_names":[],'
                '"flag":null,"bonus_ids":[],"difficulty":null,'
                '"evidence":["door is unlocked"],"clarification_question":null}'
            )
        ),
        consequence_pipeline=create_consequence_pipeline(consequence),
    )

    result = asyncio.run(
        app(
            message(
                "ordered-automatic",
                "I ask the guard to step aside and open the unlocked archive door.",
            )
        )
    )

    assert isinstance(result, HandlerResponse)
    events = store.recent_domain_events(game_id="game", limit=100)
    relevant = [
        event["causation_id"]
        for event in events
        if event["causation_id"]
        in {
            "roleplay:ordered-automatic:part:0",
            "automatic:ordered-automatic:part:1",
        }
    ]
    assert relevant == [
        "roleplay:ordered-automatic:part:0",
        "automatic:ordered-automatic:part:1",
    ]
    scene = store.scene_projection(game_id="game", player_id="alice")
    assert "The archive door is open" in scene["state"]["facts"]
    assert consequence.calls == 1


def test_roleplay_recovery_is_deferred_until_after_preflighted_automatic_patch(
    tmp_path,
) -> None:
    store = setup_store(tmp_path, reserve_current=6)
    recovery = SequenceCompletion(
        '{"safe_rest_completed":false,"safe_rest_reason":null,"awards":[]}',
        '{"safe_rest_completed":false,"safe_rest_reason":null,'
        '"awards":[{"player_id":"alice","reason":"A costly in-character choice."}]}',
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"compound_play","argument":null,"confidence":1,'
                '"evidence":"speech followed by routine action"}'
            )
        ),
        compound_play_pipeline=create_compound_play_pipeline(
            Completion(
                """
                {
                  "parts": [
                    {"kind":"roleplay","text":"I ask the guard to step aside.",
                     "conditional_on_previous":false},
                    {"kind":"action","text":"I open the unlocked archive door.",
                     "conditional_on_previous":false}
                  ],
                  "clarification_question": null
                }
                """
            )
        ),
        roleplay_reply_pipeline=create_roleplay_reply_pipeline(
            Completion('{"reply":"The guard steps aside."}')
        ),
        action_pipeline=create_action_pipeline(
            Completion(
                '{"resolution":"automatic","trait_names":[],"aspect_names":[],'
                '"flag":null,"bonus_ids":[],"difficulty":null,'
                '"evidence":["door is unlocked"],"clarification_question":null}'
            )
        ),
        consequence_pipeline=create_consequence_pipeline(
            Completion(
                '{"summary":"The unlocked door opens","add_facts":["The archive door is open"]}'
            )
        ),
        reserve_recovery_pipeline=create_reserve_recovery_pipeline(recovery),
    )

    result = asyncio.run(
        app(
            message(
                "ordered-automatic-recovery",
                "I ask the guard to step aside and open the unlocked archive door.",
            )
        )
    )

    assert isinstance(result, HandlerResponse)
    scene = store.scene_projection(game_id="game", player_id="alice")
    character = store.character_for_player(game_id="game", player_id="alice")
    assert scene is not None
    assert character is not None
    assert "The archive door is open" in scene["state"]["facts"]
    assert character.sheet.reserve_current == 7
    assert recovery.calls == 2

    events = store.recent_domain_events(game_id="game", limit=100)
    interaction_index = next(
        index
        for index, event in enumerate(events)
        if event["event_type"] == "interaction_recorded"
        and event["causation_id"] == "roleplay:ordered-automatic-recovery:part:0"
    )
    patch_index = next(
        index
        for index, event in enumerate(events)
        if event["event_type"] == "scene_patched"
        and event["causation_id"] == "automatic:ordered-automatic-recovery:part:1"
    )
    award_index = next(
        index
        for index, event in enumerate(events)
        if event["event_type"] == "reserve_roleplay_award"
        and event["causation_id"]
        == "reserve-award:roleplay:ordered-automatic-recovery:part:0:alice"
    )
    assert interaction_index < patch_index < award_index


def test_deferred_roleplay_recovery_rejects_external_drift_after_automatic_patch(
    tmp_path,
) -> None:
    store = setup_store(tmp_path, reserve_current=6)

    class PostPatchMutatingRecovery:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, **kwargs) -> CompletionResult:
            self.calls += 1
            if self.calls == 1:
                scene = store.scene_by_id(game_id="game", scene_id="room")
                assert scene is not None
                store.apply_scene_patch(
                    game_id="game",
                    scene_id="room",
                    expected_revision=int(scene["scene_revision"]),
                    causation_id="external-after-compound-automatic",
                    summary="Another event changes the room after the automatic patch.",
                    add_facts=["The alarm begins to ring"],
                    remove_facts=[],
                )
                return CompletionResult(
                    '{"safe_rest_completed":false,"safe_rest_reason":null,"awards":[]}',
                    used_tool=True,
                )
            return CompletionResult(
                '{"safe_rest_completed":false,"safe_rest_reason":null,'
                '"awards":[{"player_id":"alice","reason":"Must not be applied."}]}',
                used_tool=True,
            )

    recovery = PostPatchMutatingRecovery()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"compound_play","argument":null,"confidence":1,'
                '"evidence":"speech followed by routine action"}'
            )
        ),
        compound_play_pipeline=create_compound_play_pipeline(
            Completion(
                '{"parts":[{"kind":"roleplay","text":"I ask the guard to step aside.",'
                '"conditional_on_previous":false},'
                '{"kind":"action","text":"I open the unlocked archive door.",'
                '"conditional_on_previous":false}],"clarification_question":null}'
            )
        ),
        roleplay_reply_pipeline=create_roleplay_reply_pipeline(
            Completion('{"reply":"The guard steps aside."}')
        ),
        action_pipeline=create_action_pipeline(
            Completion(
                '{"resolution":"automatic","trait_names":[],"aspect_names":[],"flag":null,'
                '"bonus_ids":[],"difficulty":null,"evidence":["door is unlocked"],'
                '"clarification_question":null}'
            )
        ),
        consequence_pipeline=create_consequence_pipeline(
            Completion(
                '{"summary":"The unlocked door opens","add_facts":["The archive door is open"]}'
            )
        ),
        reserve_recovery_pipeline=create_reserve_recovery_pipeline(recovery),
    )

    asyncio.run(
        app(
            message(
                "ordered-automatic-external-drift",
                "I ask the guard to step aside and open the unlocked archive door.",
            )
        )
    )

    character = store.character_for_player(game_id="game", player_id="alice")
    scene = store.scene_projection(game_id="game", player_id="alice")
    assert character is not None
    assert scene is not None
    assert recovery.calls == 1
    assert character.sheet.reserve_current == 6
    assert "The alarm begins to ring" in scene["state"]["facts"]
    assert (
        store.reserve_recovery_decision("roleplay:ordered-automatic-external-drift:part:0") is None
    )


def test_preflighted_automatic_action_rejects_scene_revision_change(tmp_path) -> None:
    class SceneChangingApplication(MessageApplication):
        async def _commit_prepared_roleplay(
            self,
            prepared,
            *,
            defer_activity_and_recovery: bool = False,
        ):
            result = await super()._commit_prepared_roleplay(
                prepared,
                defer_activity_and_recovery=defer_activity_and_recovery,
            )
            scene = self._store.scene_by_id(game_id=prepared.game_id, scene_id="room")
            assert scene is not None
            self._store.apply_scene_patch(
                game_id=prepared.game_id,
                scene_id="room",
                expected_revision=int(scene["scene_revision"]),
                causation_id="concurrent-scene-change",
                summary="Another event changes the scene after consequence preflight.",
                add_facts=["The guard barred the archive door"],
                remove_facts=[],
            )
            return result

    store = setup_store(tmp_path)
    app = SceneChangingApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"compound_play","argument":null,"confidence":1,'
                '"evidence":"speech followed by routine action"}'
            )
        ),
        compound_play_pipeline=create_compound_play_pipeline(
            Completion(
                """
                {
                  "parts": [
                    {"kind":"roleplay","text":"I ask the guard to step aside.",
                     "conditional_on_previous":false},
                    {"kind":"action","text":"I open the unlocked archive door.",
                     "conditional_on_previous":false}
                  ],
                  "clarification_question": null
                }
                """
            )
        ),
        roleplay_reply_pipeline=create_roleplay_reply_pipeline(
            Completion('{"reply":"The guard considers the request."}')
        ),
        action_pipeline=create_action_pipeline(
            Completion(
                '{"resolution":"automatic","trait_names":[],"aspect_names":[],'
                '"flag":null,"bonus_ids":[],"difficulty":null,'
                '"evidence":["door is unlocked"],"clarification_question":null}'
            )
        ),
        consequence_pipeline=create_consequence_pipeline(
            Completion('{"summary":"The archive opens","add_facts":["The archive door is open"]}')
        ),
    )

    response = asyncio.run(
        app(
            message(
                "stale-automatic",
                "I ask the guard to step aside and open the unlocked archive door.",
            )
        )
    )

    assert tr("ru", "fiction_context_changed_retry") in response
    scene = store.scene_projection(game_id="game", player_id="alice")
    assert scene is not None
    assert "The guard barred the archive door" in scene["state"]["facts"]
    assert "The archive door is open" not in scene["state"]["facts"]
    assert not store.has_scene_patch("automatic:stale-automatic:part:1")


def test_transient_compound_part_failure_propagates_and_whole_request_can_retry(
    tmp_path,
) -> None:
    class FlakyRoleplay:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, **kwargs) -> CompletionResult:
            self.calls += 1
            if self.calls == 1:
                raise TransientProviderError("temporary roleplay outage")
            return CompletionResult('{"reply":"The guard steps aside."}', used_tool=True)

    store = setup_store(tmp_path)
    plan = Completion(
        """
        {
          "parts": [
            {"kind":"roleplay","text":"I ask the guard to move.",
             "conditional_on_previous":false},
            {"kind":"action","text":"I open the rune door.",
             "conditional_on_previous":false}
          ],
          "clarification_question": null
        }
        """
    )
    flaky = FlakyRoleplay()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"compound_play","argument":null,"confidence":1,'
                '"evidence":"roleplay and action"}'
            )
        ),
        compound_play_pipeline=create_compound_play_pipeline(plan),
        roleplay_reply_pipeline=create_roleplay_reply_pipeline(flaky),
        action_pipeline=create_action_pipeline(
            Completion(
                '{"resolution":"roll","trait_names":["Lore"],'
                '"aspect_names":["Runes"],"flag":null,"bonus_ids":[],'
                '"difficulty":2,"evidence":["sealed door"],'
                '"clarification_question":null}'
            )
        ),
    )
    request = message("compound-retry", "I ask the guard to move and open the rune door.")

    with pytest.raises(TransientProviderError):
        asyncio.run(app(request))
    assert store.open_pending(game_id="game", player_id="alice") is None

    retried = asyncio.run(app(request))
    assert isinstance(retried, HandlerResponse)
    assert "Пул" in retried.text
    assert flaky.calls == 2
    assert store.open_pending(game_id="game", player_id="alice").kind.value == ("pool_confirmation")
