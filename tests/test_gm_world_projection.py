import json
from pathlib import Path

from masterclaw.app.handlers.support import HandlerSupport
from masterclaw.app.scenarios import SCENARIOS, ScenarioId
from masterclaw.app.status_panels import render_status_panel
from masterclaw.context.assembler import AssembledContext, ContextAssembler
from masterclaw.context.manifests import (
    PipelineName,
    manifest_for,
    state_decision_manifest,
)
from masterclaw.domain.models import GameLifecycle
from masterclaw.domain.state import GameState, WorldState
from masterclaw.storage.sqlite import SQLiteStore

PROMPTS = Path(__file__).parents[1] / "prompts"
SECRET = "The bell is secretly feeding the buried storm."


def _state_payload(context: AssembledContext, projection: str) -> object:
    section = context.dynamic_context.split(f"## STATE {projection}\n", 1)[1]
    return json.loads(section.split("\n\n", 1)[0])


def _store_with_world_context(tmp_path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "Storm City"))
    store.update_world_content(
        world_id="world",
        expected_revision=0,
        status="approved",
        content={
            "premise": "A chained city survives above an intelligent storm.",
            "themes": ["memory", "sacrifice"],
            "locations": [
                {
                    "id": "bell_tower",
                    "name": "Bell Tower",
                    "description": "A cracked bronze bell overlooks the lower city.",
                    "exits": [{"scene_id": "market", "label": "Stairs to the market"}],
                }
            ],
            "factions": ["Chain Keepers", "Storm Readers"],
            "tensions": ["The bell must ring, but every toll wakes the storm."],
            "active_threads": ["Learn who altered the bell."],
            "threats": ["The oldest chain is close to breaking."],
            "secret_plot": SECRET,
            "character_templates": [],
        },
    )
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="game")
    store.create_scene(
        scene_id="tower",
        game_id="game",
        title="Bell Tower",
        state={
            "world_location_id": "bell_tower",
            "description": "Wind shakes the tower.",
            "facts": ["The bell is cracked."],
            "available_directions": [{"scene_id": "market", "label": "Descend to the market"}],
            "active_threads": ["Find the missing bell keeper."],
            "active_threats": ["The tower sways in the storm."],
        },
    )
    store.place_player(game_id="game", player_id="alice", scene_id="tower")
    return store


def _support(store: SQLiteStore) -> HandlerSupport:
    support = HandlerSupport()
    support._store = store
    support._context = ContextAssembler(PROMPTS)
    return support


def test_public_world_slice_contains_canonical_scene_neighborhood_without_secret(
    tmp_path,
) -> None:
    store = _store_with_world_context(tmp_path)
    support = _support(store)
    scene = store.scene_projection(game_id="game", player_id="alice")

    assembled = support._assemble_context(
        manifest_for(PipelineName.SCENE_QUESTION),
        {
            "session_brief": {"locale": "en"},
            "current_scene": scene,
            "player_question": "Where can I go?",
        },
        game_id="game",
        channel_id="channel",
        player_id="alice",
    )
    public_world = _state_payload(assembled, "public_world_context")

    assert public_world["premise"] == "A chained city survives above an intelligent storm."
    assert public_world["themes"] == ["memory", "sacrifice"]
    assert public_world["factions"] == ["Chain Keepers", "Storm Readers"]
    assert public_world["tensions"] == ["The bell must ring, but every toll wakes the storm."]
    assert public_world["current_location"] == {
        "scene_id": "tower",
        "world_location_id": "bell_tower",
        "name": "Bell Tower",
        "description": "A cracked bronze bell overlooks the lower city.",
    }
    assert public_world["available_directions"] == [
        {"scene_id": "market", "label": "Descend to the market"}
    ]
    assert public_world["active_threads"] == [
        "Find the missing bell keeper.",
        "Learn who altered the bell.",
    ]
    assert public_world["active_threats"] == [
        "The tower sways in the storm.",
        "The oldest chain is close to breaking.",
    ]
    assert "secret_plot" not in public_world
    assert SECRET not in assembled.dynamic_context


def test_gm_owned_context_receives_secret_plot_and_public_world_slice(tmp_path) -> None:
    store = _store_with_world_context(tmp_path)
    support = _support(store)
    scene = store.scene_projection(game_id="game", player_id="alice")

    assembled = support._assemble_context(
        manifest_for(PipelineName.ROLEPLAY_REPLY),
        {
            "session_brief": {"locale": "en"},
            "current_scene": scene,
            "player_narration": "I ask why the bell is cracked.",
        },
        game_id="game",
        channel_id="channel",
        player_id="alice",
    )
    gm_world = _state_payload(assembled, "gm_world_context")

    assert gm_world["premise"] == "A chained city survives above an intelligent storm."
    assert gm_world["active_threads"] == [
        "Find the missing bell keeper.",
        "Learn who altered the bell.",
    ]
    assert gm_world["active_threats"] == [
        "The tower sways in the storm.",
        "The oldest chain is close to breaking.",
    ]
    assert gm_world["secret_plot"] == SECRET


def test_player_narration_review_uses_public_context_without_secret(tmp_path) -> None:
    store = _store_with_world_context(tmp_path)
    support = _support(store)
    scene = store.scene_projection(game_id="game", player_id="alice")

    assembled = support._assemble_context(
        manifest_for(PipelineName.PLAYER_NARRATION_REVIEW),
        {
            "session_brief": {"locale": "en"},
            "current_scene": scene,
            "roll_result": {
                "hits": 2,
                "difficulty": 1,
                "narrator_rights": "player_success",
            },
            "submitted_narration": "I ring the cracked bell.",
        },
        game_id="game",
        channel_id="channel",
        player_id="alice",
    )

    assert "public_world_context" in assembled.dynamic_context
    assert "gm_world_context" not in assembled.dynamic_context
    assert SECRET not in assembled.dynamic_context


def test_secret_plot_is_absent_from_state_router_and_public_status(tmp_path) -> None:
    store = _store_with_world_context(tmp_path)
    support = _support(store)
    scenario = SCENARIOS[ScenarioId.PLAY]
    projections = {
        "mode": {"value": "play"},
        "scenario": {
            "id": scenario.id.value,
            "allowed_commands": sorted(command.value for command in scenario.llm_commands),
        },
    }
    projections.update(
        support._scenario_context_projections(
            scenario,
            game_id="game",
            channel_id="channel",
            player_id="alice",
            workspace=None,
            pending=None,
        )
    )

    state_context = support._assemble_context(
        state_decision_manifest(
            context_projections=scenario.context_projections,
            recent_chat_messages=scenario.recent_chat_messages,
        ),
        projections,
        game_id="game",
        channel_id="channel",
        player_id="alice",
    )
    panel = render_status_panel(store, channel_id="channel", player_id="alice")

    assert "gm_world_context" not in state_context.dynamic_context
    assert "public_world_context" not in state_context.dynamic_context
    assert SECRET not in state_context.dynamic_context
    assert SECRET not in panel


def test_only_gm_owned_gameplay_manifests_request_secret_world_context() -> None:
    gm_owned = {
        PipelineName.ACTION_INTERPRETATION,
        PipelineName.CONSEQUENCE_PLANNING,
        PipelineName.OUTCOME_NARRATION,
        PipelineName.ROLEPLAY_REPLY,
    }
    for pipeline in gm_owned:
        assert "gm_world_context" in manifest_for(pipeline).state_projections

    assert "public_world_context" in manifest_for(PipelineName.SCENE_QUESTION).state_projections
    assert (
        "public_world_context"
        in manifest_for(PipelineName.PLAYER_NARRATION_REVIEW).state_projections
    )
    assert (
        "gm_world_context"
        not in state_decision_manifest(
            context_projections=SCENARIOS[ScenarioId.PLAY].context_projections,
            recent_chat_messages=SCENARIOS[ScenarioId.PLAY].recent_chat_messages,
        ).state_projections
    )
