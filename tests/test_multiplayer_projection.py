import asyncio
import json
from pathlib import Path

import pytest

from masterclaw.app.handlers.support import HandlerSupport
from masterclaw.app.scenarios import SCENARIOS, ScenarioId
from masterclaw.app.status_panels import render_status_panel
from masterclaw.context.assembler import AssembledContext, ContextAssembler
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.mechanics import CharacterSheet
from masterclaw.domain.models import GameLifecycle
from masterclaw.domain.state import GameState, WorldState
from masterclaw.pipelines.base import CompletionResult
from masterclaw.pipelines.conversation import (
    create_roleplay_reply_pipeline,
    create_scene_question_pipeline,
)
from masterclaw.pipelines.narrative import create_narrative_pipeline
from masterclaw.pipelines.state_decision import create_state_decision_pipeline
from masterclaw.storage.sqlite import SQLiteStore


def _create_character(
    store: SQLiteStore,
    *,
    character_id: str,
    player_id: str,
    name: str,
) -> None:
    store.create_character(
        CharacterState(
            character_id,
            "game",
            player_id,
            f"{name} biography",
            CharacterSheet(name=name, traits=(), flags=()),
        )
    )


def _active_store(tmp_path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="game")
    return store


def test_world_selection_projection_uses_compact_catalog_summaries(tmp_path) -> None:
    store = _active_store(tmp_path)
    store.update_world_content(
        world_id="world",
        expected_revision=0,
        content={
            "premise": "P" * 700,
            "themes": [f"theme-{index}" for index in range(12)],
            "locations": [{"id": "vault", "description": "large private section"}],
            "factions": ["Keepers"],
            "tensions": ["The gate is failing"],
            "secret_plot": "The keeper opened the gate.",
            "character_templates": [{"name": "Mara"}],
        },
    )
    support = HandlerSupport()
    support._store = store

    projections = support._scenario_context_projections(
        SCENARIOS[ScenarioId.WORLD_SELECTION],
        game_id=None,
        channel_id="channel",
        player_id="alice",
        workspace=None,
        pending=None,
    )

    item = projections["world_catalog"][0]
    assert set(item) == {"ordinal", "world_id", "title", "status", "premise", "themes"}
    assert len(item["premise"]) == 500
    assert item["themes"] == [f"theme-{index}" for index in range(8)]


def test_play_status_panel_uses_requesting_players_scene(tmp_path) -> None:
    store = _active_store(tmp_path)
    store.create_scene(
        scene_id="north",
        game_id="game",
        title="North Hall",
        state={"description": "Snow reaches the broken windows.", "facts": []},
    )
    store.create_scene(
        scene_id="south",
        game_id="game",
        title="South Vault",
        state={"description": "Rain drums against the iron hatch.", "facts": []},
    )
    _create_character(store, character_id="mara", player_id="alice", name="Mara")
    _create_character(store, character_id="dorn", player_id="bob", name="Dorn")
    store.place_player(game_id="game", player_id="alice", scene_id="north")
    store.place_player(game_id="game", player_id="bob", scene_id="south")

    # Make North the game's latest changed scene. Bob must still receive South.
    store.apply_scene_patch(
        game_id="game",
        scene_id="north",
        expected_revision=0,
        causation_id="north-changed-last",
        summary="North changes",
        add_facts=["The northern bell rings."],
        remove_facts=[],
    )

    alice_panel = render_status_panel(store, channel_id="channel", player_id="alice")
    bob_panel = render_status_panel(store, channel_id="channel", player_id="bob")

    assert "North Hall" in alice_panel
    assert "South Vault" not in alice_panel
    assert "South Vault" in bob_panel
    assert "North Hall" not in bob_panel


def test_state_and_gm_scene_context_include_participant_character_names(tmp_path) -> None:
    store = _active_store(tmp_path)
    store.create_scene(scene_id="room", game_id="game", title="Shared Room")
    _create_character(store, character_id="mara", player_id="alice", name="Mara")
    _create_character(store, character_id="dorn", player_id="bob", name="Dorn")
    store.place_player(game_id="game", player_id="alice", scene_id="room")
    store.place_player(game_id="game", player_id="bob", scene_id="room")

    support = HandlerSupport()
    support._store = store
    projections = support._scenario_context_projections(
        SCENARIOS[ScenarioId.PLAY],
        game_id="game",
        channel_id="channel",
        player_id="alice",
        workspace=None,
        pending=None,
    )
    assert projections["current_scene"]["participant_characters"] == [
        {"player_id": "alice", "name": "Mara"},
        {"player_id": "bob", "name": "Dorn"},
    ]

    support._context = ContextAssembler(Path(__file__).parents[1] / "prompts")
    raw_scene = store.scene_projection(game_id="game", player_id="alice")
    assembled = support._assemble_context(
        manifest_for(PipelineName.SCENE_QUESTION),
        {
            "session_brief": {"locale": "en"},
            "current_scene": raw_scene,
            "player_question": "Who is beside me?",
        },
        game_id="game",
        channel_id="channel",
        player_id="alice",
    )
    scene_section = assembled.dynamic_context.split("## STATE current_scene\n", 1)[1]
    scene_payload = json.loads(scene_section.split("\n\n", 1)[0])
    assert scene_payload["participant_characters"] == [
        {"player_id": "alice", "name": "Mara"},
        {"player_id": "bob", "name": "Dorn"},
    ]


class _PromptCapture:
    def __init__(self, response: str) -> None:
        self.response = response
        self.system = ""

    async def complete(self, **kwargs) -> CompletionResult:
        self.system = kwargs["system"]
        return CompletionResult(self.response, used_tool=True)


@pytest.mark.parametrize(
    ("factory", "response"),
    [
        (create_scene_question_pipeline, '{"reply":"The corridor is empty."}'),
        (create_roleplay_reply_pipeline, '{"reply":"The guard nods."}'),
        (create_narrative_pipeline, '{"narrative":"The gate opens."}'),
    ],
)
def test_player_facing_gm_prompts_use_character_names_only_for_ambiguity(factory, response) -> None:
    completion = _PromptCapture(response)
    asyncio.run(
        factory(completion).run(
            task="Respond.",
            context=AssembledContext("", "", (), 0),
        )
    )
    assert "genuinely ambiguous" in completion.system
    assert "character name" in completion.system
    assert "Never insert a Discord user mention" in completion.system
    assert "do not prefix a routine single-recipient reply" in completion.system
    assert "participant order" in completion.system


def test_state_router_uses_character_mapping_without_discord_mentions() -> None:
    completion = _PromptCapture(
        '{"command":"show_scene","argument":null,"confidence":1,'
        '"evidence":"asks about the current scene"}'
    )
    asyncio.run(
        create_state_decision_pipeline(completion, SCENARIOS[ScenarioId.PLAY]).run(
            task="Choose a command.",
            context=AssembledContext("", "", (), 0),
        )
    )
    assert "current_scene.participant_characters" in completion.system
    assert "character names" in completion.system
    assert "Never emit a Discord user mention" in completion.system
