from masterclaw.app.action_service import ActionService
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.mechanics import (
    CharacterSheet,
    Flag,
    FlagType,
    NarratorRights,
    PoolProposal,
    Trait,
)
from masterclaw.domain.models import GameLifecycle
from masterclaw.domain.state import GameState, NarratorRightsLevel, WorldState
from masterclaw.storage.sqlite import SQLiteStore


def setup_store(tmp_path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    sheet = CharacterSheet(
        "Hero",
        tuple(Trait(f"Trait {i}", 3, tuple(f"Aspect {i}.{n}" for n in range(3))) for i in range(6)),
        (
            Flag("Friend", FlagType.RELATIONSHIP),
            Flag("Goal", FlagType.GOAL),
            Flag("Belief", FlagType.BELIEF),
        ),
        reserve_current=5,
    )
    store.create_character(CharacterState("hero", "game", "alice", "Bio", sheet))
    return store


def add_helper(store: SQLiteStore) -> None:
    actor = store.character_for_player(game_id="game", player_id="alice")
    helper_sheet = CharacterSheet(
        "Helper",
        actor.sheet.traits,
        actor.sheet.flags,
        reserve_current=7,
    )
    store.create_character(CharacterState("helper", "game", "bob", "Bio", helper_sheet))
    store.create_scene(scene_id="scene", game_id="game", title="Scene")
    store.place_player(game_id="game", player_id="alice", scene_id="scene")
    store.place_player(game_id="game", player_id="bob", scene_id="scene")


def test_confirmed_roll_is_immutable_and_idempotent(tmp_path) -> None:
    store = setup_store(tmp_path)
    service = ActionService(store)
    pending = service.propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="scene",
        proposal=PoolProposal(
            trait_names=("Trait 0",),
            aspect_names=("Aspect 0.0",),
            reserve_spent=0,
            difficulty=2,
        ),
        prompt="Confirm?",
    )
    dice = iter((4, 4, 1, 1))
    first = service.confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        reserve_spent=2,
        die=lambda: next(dice),
    )
    second = service.confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        reserve_spent=2,
        die=lambda: next(dice),
    )
    assert second == first
    assert first.hits == 2
    assert first.reserve_spent == 2
    character = store.character_for_player(game_id="game", player_id="alice")
    assert character is not None
    assert character.sheet.reserve_current == 3


def test_disabled_narrator_rights_keep_outcome_with_pipeline_gm(tmp_path) -> None:
    store = setup_store(tmp_path)
    game = store.game_state("game")
    store.set_narrator_rights_level(
        game_id="game",
        level=NarratorRightsLevel.DISABLED,
        expected_revision=game.revision,
    )
    service = ActionService(store)
    pending = service.propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="scene",
        proposal=PoolProposal(trait_names=("Trait 0",), aspect_names=("Aspect 0.0",), difficulty=1),
        prompt="Confirm",
    )
    result = service.confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        confirmation_event_id="disabled",
        die=lambda: 4,
    )
    assert result.narrator_rights is NarratorRights.GM_SUCCESS
    assert store.open_pending(game_id="game", player_id="alice") is None


def test_help_die_is_spent_on_success_and_added_to_pool(tmp_path) -> None:
    store = setup_store(tmp_path)
    add_helper(store)
    service = ActionService(store)
    pending = service.propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="scene",
        proposal=PoolProposal(trait_names=("Trait 0",), difficulty=1),
        prompt="Confirm",
    )
    store.offer_help(game_id="game", helper_player_id="bob", target_player_id="alice")
    result = service.confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        confirmation_event_id="help-success",
        die=lambda: 4,
    )
    assert result.help_dice == 1
    assert result.pool_size == 2
    helper = store.character_for_player(game_id="game", player_id="bob")
    assert helper.sheet.reserve_current == 6


def test_help_die_is_returned_on_failure_without_penalty(tmp_path) -> None:
    store = setup_store(tmp_path)
    add_helper(store)
    service = ActionService(store)
    pending = service.propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="scene",
        proposal=PoolProposal(trait_names=("Trait 0",), difficulty=3),
        prompt="Confirm",
    )
    store.offer_help(game_id="game", helper_player_id="bob", target_player_id="alice")
    service.confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        confirmation_event_id="help-failure",
        die=lambda: 1,
    )
    helper = store.character_for_player(game_id="game", player_id="bob")
    assert helper.sheet.reserve_current == 7
