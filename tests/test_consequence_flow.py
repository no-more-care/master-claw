import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from masterclaw.app.message_handler import MessageApplication
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.mechanics import CharacterSheet, Flag, FlagType, Trait
from masterclaw.domain.models import GameLifecycle, HandlerResponse, IncomingMessage
from masterclaw.domain.state import GameState, WorldState
from masterclaw.pipelines.action import create_action_pipeline
from masterclaw.pipelines.consequence import create_consequence_pipeline
from masterclaw.pipelines.intent import create_intent_pipeline
from masterclaw.pipelines.narrative import create_narrative_pipeline
from masterclaw.storage.sqlite import SQLiteStore


class Completion:
    def __init__(self, response):
        self.response = response

    async def complete(self, *, system: str, user: str) -> str:
        return self.response


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
    store.create_character(CharacterState("hero", "game", "alice", "Bio", sheet))
    return store


def test_automatic_action_applies_typed_scene_patch_before_narrative(tmp_path) -> None:
    store = setup(tmp_path)
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        intent_pipeline=create_intent_pipeline(
            Completion('{"intent":"action_declaration","confidence":1,"evidence":"opens door"}')
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
