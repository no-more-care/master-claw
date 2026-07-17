import asyncio
from pathlib import Path

import pytest

from masterclaw.app.advancement_coordinator import AdvancementCoordinator
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.mechanics import CharacterSheet, Flag, FlagType, Trait
from masterclaw.domain.models import GameLifecycle
from masterclaw.domain.state import GameState, WorldState
from masterclaw.pipelines.advancement import create_advancement_safety_pipeline
from masterclaw.pipelines.base import CompletionResult
from masterclaw.storage.sqlite import SQLiteStore


class FakeCompletion:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed

    async def complete(self, **kwargs) -> CompletionResult:
        value = "true" if self.allowed else "false"
        return CompletionResult(
            f'{{"allowed":{value},"reason":"scene assessment","evidence":["current scene"]}}',
            used_tool=True,
        )


def setup(tmp_path, *, enabled=True):
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE, progression_enabled=enabled))
    store.create_scene(scene_id="camp", game_id="game", title="Camp", state={"danger": "nearby"})
    store.place_player(game_id="game", player_id="alice", scene_id="camp")
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
        CharacterState("hero", "game", "alice", "Bio", sheet, experience_earned=4)
    )
    return store


def coordinator(store, allowed):
    return AdvancementCoordinator(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        safety_pipeline=create_advancement_safety_pipeline(FakeCompletion(allowed)),
    )


def test_gm_pipeline_can_deny_advancement_for_current_scene(tmp_path) -> None:
    store = setup(tmp_path)
    with pytest.raises(ValueError, match="not allowed now"):
        asyncio.run(
            coordinator(store, False).raise_trait(
                game_id="game", player_id="alice", trait_name="T0", new_aspect="New"
            )
        )


def test_allowed_decision_becomes_revision_bound_permit(tmp_path) -> None:
    store = setup(tmp_path)
    updated = asyncio.run(
        coordinator(store, True).raise_trait(
            game_id="game", player_id="alice", trait_name="T0", new_aspect="New"
        )
    )
    assert updated.sheet.traits[0].level == 4
    assert updated.experience_available == 0


def test_disabled_progression_skips_gm_pipeline_and_rejects(tmp_path) -> None:
    store = setup(tmp_path, enabled=False)
    with pytest.raises(ValueError, match="disabled"):
        asyncio.run(
            coordinator(store, True).raise_trait(
                game_id="game", player_id="alice", trait_name="T0", new_aspect="New"
            )
        )
