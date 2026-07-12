from datetime import UTC, datetime

import pytest

from masterclaw.app.game_service import GameService
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.mechanics import CharacterSheet, Flag, FlagType, Trait
from masterclaw.domain.models import GameLifecycle
from masterclaw.storage.sqlite import SQLiteStore


def add_character(store, game_id, scene_id):
    sheet = CharacterSheet(
        "Hero",
        tuple(Trait(f"T{i}", 3, tuple(f"A{i}.{n}" for n in range(3))) for i in range(6)),
        (
            Flag("Friend", FlagType.RELATIONSHIP),
            Flag("Goal", FlagType.GOAL),
            Flag("Belief", FlagType.BELIEF),
        ),
    )
    store.create_character(CharacterState("hero", game_id, "alice", "Bio", sheet))
    store.place_player(game_id=game_id, player_id="alice", scene_id=scene_id)


def test_game_cannot_start_until_narrative_channel_and_scene_exist(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    service = GameService(store)
    service.create_world(world_id="my_world", title="My World")
    service.prepare_game(game_id="my_game", world_id="my_world", channel_id="game-channel")
    assert service.readiness("my_game").missing == (
        "narrative_channel",
        "initial_scene",
        "characters",
    )
    with pytest.raises(ValueError, match="not ready"):
        service.start_game("my_game")


def test_ready_game_transitions_to_active(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    service = GameService(store)
    service.create_world(world_id="my_world", title="My World")
    service.prepare_game(game_id="my_game", world_id="my_world", channel_id="game-channel")
    game = store.game_state("my_game")
    store.set_narrative_channel(
        game_id="my_game", channel_id="narrative", expected_revision=game.revision
    )
    store.create_scene(scene_id="opening", game_id="my_game", title="Opening Scene")
    add_character(store, "my_game", "opening")
    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    active = service.start_game("my_game", started_at=started_at)
    assert active.lifecycle is GameLifecycle.ACTIVE
    assert store.activity_state("my_game")["last_event_at"] == started_at.isoformat()


def test_all_participants_share_world_mutation_capability_at_service_boundary(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    service = GameService(store)
    # No operator/role parameter exists by design: Discord participants use the same gate.
    assert service.create_world(world_id="shared", title="Shared").world_id == "shared"


def test_progression_is_configured_only_before_game_start(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    service = GameService(store)
    service.create_world(world_id="world", title="World")
    service.prepare_game(game_id="game", world_id="world", channel_id="game-channel")
    configured = service.configure_progression(game_id="game", enabled=True)
    assert configured.progression_enabled is True
    store.set_narrative_channel(
        game_id="game", channel_id="narrative", expected_revision=configured.revision
    )
    store.create_scene(scene_id="opening", game_id="game", title="Opening")
    add_character(store, "game", "opening")
    service.start_game("game")
    with pytest.raises(RuntimeError, match="only during preparation"):
        service.configure_progression(game_id="game", enabled=False)
