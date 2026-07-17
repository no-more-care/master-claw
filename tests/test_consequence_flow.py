import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from masterclaw.app.action_service import ActionService
from masterclaw.app.message_handler import MessageApplication
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.characters import CharacterState, Condition, PlotItem
from masterclaw.domain.mechanics import CharacterSheet, Flag, FlagType, PoolProposal, Trait
from masterclaw.domain.models import GameLifecycle, HandlerResponse, IncomingMessage
from masterclaw.domain.state import GameState, WorldState
from masterclaw.domain.text_safety import contains_secret_fragment
from masterclaw.pipelines.action import create_action_pipeline
from masterclaw.pipelines.base import CompletionResult
from masterclaw.pipelines.consequence import create_consequence_pipeline
from masterclaw.pipelines.narrative import create_narrative_pipeline
from masterclaw.pipelines.state_decision import StateDecisionRouter
from masterclaw.storage.sqlite import SQLiteStore


class Completion:
    def __init__(self, response):
        self.response = response

    async def complete(self, **kwargs) -> CompletionResult:
        return CompletionResult(self.response, used_tool=True)


class InvalidCompletion:
    async def complete(self, **kwargs) -> CompletionResult:
        return CompletionResult("not valid JSON", used_tool=True)


def setup(tmp_path):
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(
        GameState("game", "world", GameLifecycle.ACTIVE, narrative_channel_id="narrative")
    )
    store.bind_channel(channel_id="game", game_id="game")
    store.create_scene(
        scene_id="room",
        game_id="game",
        title="Room",
        state={"facts": ["The door is closed"]},
    )
    store.place_player(game_id="game", player_id="alice", scene_id="room")
    sheet = CharacterSheet(
        "Hero",
        tuple(Trait(f"T{i}", 3, tuple(f"A{i}.{n}" for n in range(3))) for i in range(6)),
        (
            Flag("Friend", FlagType.RELATIONSHIP),
            Flag("Goal", FlagType.GOAL),
            Flag("Belief", FlagType.BELIEF),
        ),
    )
    store.create_character(
        CharacterState(
            "hero",
            "game",
            "alice",
            "Bio",
            sheet,
            conditions=(Condition("Pinned", "falling stones"),),
            plot_items=(PlotItem("Old token"),),
        )
    )
    return store


def test_automatic_action_applies_typed_scene_patch_before_narrative(tmp_path) -> None:
    store = setup(tmp_path)
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"declare_action","argument":null,"confidence":1,'
                '"evidence":"opens door"}'
            )
        ),
        action_pipeline=create_action_pipeline(
            Completion(
                '{"resolution":"automatic","trait_names":[],"aspect_names":[],'
                '"flag":null,"difficulty":null,"evidence":["door is unlocked"],'
                '"clarification_question":null}'
            )
        ),
        consequence_pipeline=create_consequence_pipeline(
            Completion(
                '{"summary":"The door opens","add_facts":["The door is open"],'
                '"remove_facts":["The door is closed"]}'
            )
        ),
        narrative_pipeline=create_narrative_pipeline(
            Completion('{"narrative":"Дверь бесшумно открывается."}')
        ),
    )
    result = asyncio.run(
        app(
            IncomingMessage(
                event_id="action",
                channel_id="game",
                author_id="alice",
                content="Я открываю незапертую дверь.",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )
    assert isinstance(result, HandlerResponse)
    scene = store.scene_projection(game_id="game", player_id="alice")
    assert scene["state"]["facts"] == ["The door is open"]
    assert scene["scene_revision"] == 1


def test_secret_fragment_in_action_clarification_is_replaced(tmp_path) -> None:
    secret = "The bell keeper is the storm's forgotten name."
    store = setup(tmp_path)
    store.update_world_content(
        world_id="world",
        expected_revision=0,
        content={"secret_plot": secret},
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"declare_action","argument":null,"confidence":1,'
                '"evidence":"ambiguous action"}'
            )
        ),
        action_pipeline=create_action_pipeline(
            Completion(
                '{"resolution":"clarification","trait_names":[],"aspect_names":[],'
                '"flag":null,"bonus_ids":[],"difficulty":null,"evidence":[],'
                '"clarification_question":"THE BELL-KEEPER is the storm’s forgotten NAME"}'
            )
        ),
    )

    result = asyncio.run(
        app(
            IncomingMessage(
                event_id="secret-clarification",
                channel_id="game",
                author_id="alice",
                content="I inspect it.",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    assert not contains_secret_fragment(result, secret)


def test_secret_fragment_in_public_patch_is_rejected_without_mutation(tmp_path) -> None:
    secret = "The bell keeper is the storm's forgotten name."
    store = setup(tmp_path)
    store.update_world_content(
        world_id="world",
        expected_revision=0,
        content={"secret_plot": secret},
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"declare_action","argument":null,"confidence":1,'
                '"evidence":"opens door"}'
            )
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
                '{"summary":"THE BELL-KEEPER is the storm’s forgotten NAME",'
                '"add_facts":["The door is open"],"remove_facts":["The door is closed"]}'
            )
        ),
    )

    result = asyncio.run(
        app(
            IncomingMessage(
                event_id="secret-patch",
                channel_id="game",
                author_id="alice",
                content="I open the unlocked door.",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    assert not store.has_scene_patch("automatic:secret-patch")
    assert store.scene_projection(game_id="game", player_id="alice")["state"]["facts"] == [
        "The door is closed"
    ]
    assert not contains_secret_fragment(result, secret)


def test_scene_patch_cannot_remove_unknown_fact(tmp_path) -> None:
    store = setup(tmp_path)
    with pytest.raises(ValueError, match="unknown scene facts"):
        store.apply_scene_patch(
            game_id="game",
            scene_id="room",
            expected_revision=0,
            causation_id="bad",
            summary="bad",
            add_facts=[],
            remove_facts=["Unknown"],
        )


def test_committed_roll_returns_mechanics_when_consequence_pipeline_fails(tmp_path) -> None:
    store = setup(tmp_path)
    pending = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="room",
        proposal=PoolProposal(
            trait_names=("T0",),
            aspect_names=("A0.0",),
            difficulty=2,
        ),
        prompt="Confirm",
        declaration="Open the door",
    )
    roll = ActionService(store).confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        confirmation_event_id="confirmed-roll",
        die=iter((1, 1)).__next__,
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion('{"command":"clarify","argument":null,"confidence":0,"evidence":"unused"}')
        ),
        consequence_pipeline=create_consequence_pipeline(InvalidCompletion()),
        narrative_pipeline=create_narrative_pipeline(
            Completion('{"narrative":"This must not be reached."}')
        ),
    )

    result = asyncio.run(
        app(
            IncomingMessage(
                event_id="confirmed-roll",
                channel_id="game",
                author_id="alice",
                content="0",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )
    assert isinstance(result, str)
    assert "Бросок" in result
    assert "Успехов" in result
    assert not store.has_scene_patch(f"roll:{roll.roll_id}")


def test_automatic_outcome_patch_changes_actor_scene_and_location(tmp_path) -> None:
    store = setup(tmp_path)
    store.create_scene(scene_id="archive", game_id="game", title="Archive")
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"declare_action","argument":null,"confidence":1,'
                '"evidence":"crosses into the archive"}'
            )
        ),
        action_pipeline=create_action_pipeline(
            Completion(
                '{"resolution":"automatic","trait_names":[],"aspect_names":[],'
                '"flag":null,"bonus_ids":[],"difficulty":null,'
                '"evidence":["the passage is clear"],"clarification_question":null}'
            )
        ),
        consequence_pipeline=create_consequence_pipeline(
            Completion(
                """
                {
                  "summary": "The hero enters the archive with the brass key",
                  "add_facts": ["The archive door is open"],
                  "remove_facts": ["The door is closed"],
                  "add_actor_conditions": [],
                  "remove_actor_conditions": ["Pinned"],
                  "add_actor_plot_items": [
                    {"name": "Brass key", "description": "Opens the archive lift"}
                  ],
                  "remove_actor_plot_items": ["Old token"],
                  "move_actor_to_scene_id": "archive",
                  "upsert_scene_npcs": [
                    {"npc_id": "courier", "name": "Courier", "state": "Escaped"}
                  ],
                  "remove_scene_npc_ids": [],
                  "open_threads": ["Who hired the courier?"],
                  "close_threads": [],
                  "grant_temporary_bonus": {
                    "bonus_id": "courier-route",
                    "type": "extra_die",
                    "trigger": "Following the courier through the archive"
                  }
                }
                """
            )
        ),
        narrative_pipeline=create_narrative_pipeline(
            Completion('{"narrative":"Герой скрывается в архиве."}')
        ),
    )

    result = asyncio.run(
        app(
            IncomingMessage(
                event_id="full-outcome",
                channel_id="game",
                author_id="alice",
                content="Я прохожу в открытый архив.",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    assert isinstance(result, HandlerResponse)
    room = store.scene_by_id(game_id="game", scene_id="room")
    assert room["state"]["facts"] == ["The archive door is open"]
    assert room["state"]["npcs"][0]["id"] == "courier"
    actor = store.character_for_player(game_id="game", player_id="alice")
    assert actor.conditions == ()
    assert [item.name for item in actor.plot_items] == ["Brass key"]
    assert actor.sheet.temporary_bonuses[0].bonus_id == "courier-route"
    assert store.scene_projection(game_id="game", player_id="alice")["scene_id"] == "archive"


def test_automatic_patch_survives_narrative_failure_with_safe_delivery(tmp_path) -> None:
    store = setup(tmp_path)
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"declare_action","argument":null,"confidence":1,'
                '"evidence":"opens door"}'
            )
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
                '{"summary":"The door opens","add_facts":["The door is open"],'
                '"remove_facts":["The door is closed"]}'
            )
        ),
        narrative_pipeline=create_narrative_pipeline(InvalidCompletion()),
    )

    result = asyncio.run(
        app(
            IncomingMessage(
                event_id="automatic-narrative-failure",
                channel_id="game",
                author_id="alice",
                content="Я открываю незапертую дверь.",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    assert isinstance(result, HandlerResponse)
    assert "исход действия зафиксирован" in result.deliveries[0].content.lower()
    assert store.scene_by_id(game_id="game", scene_id="room")["state"]["facts"] == [
        "The door is open"
    ]
