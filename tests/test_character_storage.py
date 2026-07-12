from masterclaw.domain.characters import CharacterState, Condition, PlotItem
from masterclaw.domain.mechanics import (
    STARTING_CHARACTER_RULES,
    CharacterSheet,
    Flag,
    FlagType,
    Trait,
    validate_character,
)
from masterclaw.domain.models import GameLifecycle
from masterclaw.domain.state import GameState, WorldState
from masterclaw.storage.sqlite import SQLiteStore


def test_character_round_trip_preserves_typed_sheet(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.PREPARING))
    sheet = CharacterSheet(
        name="Hero",
        traits=tuple(
            Trait(f"Trait {index}", 3, tuple(f"Aspect {index}.{n}" for n in range(3)))
            for index in range(6)
        ),
        flags=(
            Flag("Friend of Alex", FlagType.RELATIONSHIP),
            Flag("Never surrender", FlagType.BELIEF),
            Flag("Find the map", FlagType.GOAL),
        ),
    )
    validate_character(sheet, STARTING_CHARACTER_RULES)
    character = CharacterState(
        "hero",
        "game",
        "alice",
        "Biography",
        sheet,
        conditions=(Condition("Wounded", "failed roll"),),
        plot_items=(PlotItem("Ancient key", "Opens the observatory"),),
    )
    store.create_character(character)
    assert store.character_for_player(game_id="game", player_id="alice") == character
