import pytest

from masterclaw.domain.models import GameLifecycle
from masterclaw.domain.state import GameState, InvalidTransition, actions_conflict, transition_game


def test_game_lifecycle_follows_explicit_state_machine() -> None:
    draft = GameState("game", "world", GameLifecycle.DRAFT)
    preparing = transition_game(draft, GameLifecycle.PREPARING)
    active = transition_game(preparing, GameLifecycle.ACTIVE)
    assert active.lifecycle is GameLifecycle.ACTIVE
    assert active.revision == 2


def test_game_cannot_skip_preparation() -> None:
    with pytest.raises(InvalidTransition):
        transition_game(GameState("game", "world", GameLifecycle.DRAFT), GameLifecycle.ACTIVE)


def test_different_scenes_are_independent_without_shared_entities() -> None:
    assert not actions_conflict(
        scenes_a=frozenset({"north"}),
        entities_a=frozenset({"alice"}),
        scenes_b=frozenset({"south"}),
        entities_b=frozenset({"bob"}),
    )
    assert actions_conflict(
        scenes_a=frozenset({"north"}),
        entities_a=frozenset({"door"}),
        scenes_b=frozenset({"south"}),
        entities_b=frozenset({"door"}),
    )
