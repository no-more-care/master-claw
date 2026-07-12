from datetime import UTC, datetime, timedelta

import pytest

from masterclaw.app.progression_service import ProgressionService
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.mechanics import CharacterSheet, Flag, FlagType, Trait
from masterclaw.domain.models import GameLifecycle
from masterclaw.domain.progression import AdvancementPermit, credited_activity_seconds
from masterclaw.domain.state import GameState, WorldState
from masterclaw.storage.sqlite import SQLiteStore


def character_sheet() -> CharacterSheet:
    return CharacterSheet(
        "Hero",
        tuple(Trait(f"Trait {i}", 3, tuple(f"Aspect {i}.{n}" for n in range(3))) for i in range(6)),
        (
            Flag("Friend", FlagType.RELATIONSHIP),
            Flag("Goal", FlagType.GOAL),
            Flag("Belief", FlagType.BELIEF),
        ),
    )


def setup_game(
    tmp_path,
    *,
    earned: int = 0,
    progression_enabled: bool = True,
) -> tuple[SQLiteStore, ProgressionService]:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(
        GameState(
            "game",
            "world",
            GameLifecycle.ACTIVE,
            progression_enabled=progression_enabled,
        )
    )
    store.create_scene(
        scene_id="city",
        game_id="game",
        title="City",
        state={"location": "city"},
    )
    store.place_player(game_id="game", player_id="alice", scene_id="city")
    store.create_character(
        CharacterState("hero", "game", "alice", "Bio", character_sheet(), experience_earned=earned)
    )
    return store, ProgressionService(store)


def permit(store: SQLiteStore) -> AdvancementPermit:
    scene = store.scene_projection(game_id="game", player_id="alice")
    return AdvancementPermit(
        "game", "alice", str(scene["scene_id"]), int(scene["scene_revision"]), "allowed"
    )


def test_activity_interval_is_capped_at_five_minutes() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    assert credited_activity_seconds(start, start + timedelta(minutes=2)) == 120
    assert credited_activity_seconds(start, start + timedelta(days=1)) == 300
    assert credited_activity_seconds(start, start - timedelta(seconds=1)) == 0


def test_xp_is_awarded_automatically_at_each_new_full_half_hour(tmp_path) -> None:
    store, service = setup_game(tmp_path)
    current = datetime(2026, 1, 1, tzinfo=UTC)
    service.record_player_event(game_id="game", occurred_at=current)
    updates = []
    for _ in range(6):
        current += timedelta(minutes=5)
        updates.append(service.record_player_event(game_id="game", occurred_at=current))
    assert updates[-1].xp_awarded_each == 1
    character = store.character_for_player(game_id="game", player_id="alice")
    assert character.experience_earned == 1


def test_disabled_progression_tracks_time_but_awards_no_xp(tmp_path) -> None:
    store, service = setup_game(tmp_path, progression_enabled=False)
    current = datetime(2026, 1, 1, tzinfo=UTC)
    service.record_player_event(game_id="game", occurred_at=current)
    for _ in range(6):
        current += timedelta(minutes=5)
        update = service.record_player_event(game_id="game", occurred_at=current)
    assert update.total_active_seconds == 1800
    assert update.xp_awarded_each == 0
    assert store.character_for_player(game_id="game", player_id="alice").experience_earned == 0


def test_raise_trait_costs_the_new_level_and_can_exceed_six(tmp_path) -> None:
    store, service = setup_game(tmp_path, earned=7)
    # Replace one level-3 trait with level 6 to exercise the decided 6→7 rule.
    character = store.character_for_player(game_id="game", player_id="alice")
    traits = list(character.sheet.traits)
    traits[0] = Trait("Trait 0", 6, tuple(f"Advanced {n}" for n in range(6)))
    store.update_character_progression(
        character_id="hero",
        expected_revision=character.revision,
        sheet=CharacterSheet(character.sheet.name, tuple(traits), character.sheet.flags),
        xp_cost=1,
        description="test fixture adjustment",
    )
    # Fixture spent one XP, so add a fresh character is clearer than hiding the cost.
    with store.transaction() as connection:
        connection.execute(
            "UPDATE characters SET experience_earned = 8 WHERE character_id = 'hero'"
        )
    updated = service.raise_character_trait(
        game_id="game",
        player_id="alice",
        trait_name="Trait 0",
        new_aspect="Mastery",
        permit=permit(store),
    )
    assert updated.sheet.traits[0].level == 7
    assert updated.experience_spent == 8


def test_new_trait_requires_justification_after_authorization(tmp_path) -> None:
    store, safe_service = setup_game(tmp_path, earned=3)
    with pytest.raises(ValueError, match="justification"):
        safe_service.learn_character_trait(
            game_id="game",
            player_id="alice",
            trait_name="Alchemy",
            aspects=("Potions", "Poisons"),
            justification="",
            permit=permit(store),
        )
    updated = safe_service.learn_character_trait(
        game_id="game",
        player_id="alice",
        trait_name="Alchemy",
        aspects=("Potions", "Poisons"),
        justification="Studied with a city mentor",
        permit=permit(store),
    )
    assert updated.sheet.traits[-1].level == 2
    assert updated.experience_available == 0
