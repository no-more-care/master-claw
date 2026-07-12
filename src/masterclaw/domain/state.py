from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .models import GameLifecycle


class PendingKind(StrEnum):
    POOL_CONFIRMATION = "pool_confirmation"
    PLAYER_NARRATION = "player_narration"
    CHOICE = "choice"
    CLARIFICATION = "clarification"


class PendingStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class NarratorRightsLevel(StrEnum):
    DISABLED = "disabled"
    MINOR = "minor"
    SIGNIFICANT = "significant"
    MADNESS = "madness"


@dataclass(frozen=True, slots=True)
class SceneRef:
    scene_id: str
    revision: int


@dataclass(frozen=True, slots=True)
class WorldState:
    world_id: str
    title: str
    status: str = "draft"
    revision: int = 0


@dataclass(frozen=True, slots=True)
class GameState:
    game_id: str
    world_id: str
    lifecycle: GameLifecycle
    progression_enabled: bool = False
    narrator_rights_level: NarratorRightsLevel = NarratorRightsLevel.MINOR
    locale: str = "ru"
    narrative_channel_id: str | None = None
    revision: int = 0


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    ready: bool
    missing: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PendingInteraction:
    interaction_id: str
    game_id: str
    player_id: str
    scene_id: str | None
    kind: PendingKind
    prompt: str
    payload: dict[str, object] = field(default_factory=dict)
    status: PendingStatus = PendingStatus.OPEN
    revision: int = 0


class InvalidTransition(ValueError):
    pass


ALLOWED_LIFECYCLE_TRANSITIONS: dict[GameLifecycle, frozenset[GameLifecycle]] = {
    GameLifecycle.DRAFT: frozenset({GameLifecycle.PREPARING}),
    GameLifecycle.PREPARING: frozenset({GameLifecycle.ACTIVE}),
    GameLifecycle.ACTIVE: frozenset({GameLifecycle.PAUSED, GameLifecycle.FINISHED}),
    GameLifecycle.PAUSED: frozenset({GameLifecycle.ACTIVE, GameLifecycle.FINISHED}),
    GameLifecycle.FINISHED: frozenset(),
}


def transition_game(game: GameState, target: GameLifecycle) -> GameState:
    if target not in ALLOWED_LIFECYCLE_TRANSITIONS[game.lifecycle]:
        raise InvalidTransition(f"cannot transition game from {game.lifecycle} to {target}")
    return GameState(
        game_id=game.game_id,
        world_id=game.world_id,
        lifecycle=target,
        progression_enabled=game.progression_enabled,
        narrator_rights_level=game.narrator_rights_level,
        locale=game.locale,
        narrative_channel_id=game.narrative_channel_id,
        revision=game.revision + 1,
    )


def actions_conflict(
    *,
    scenes_a: frozenset[str],
    entities_a: frozenset[str],
    scenes_b: frozenset[str],
    entities_b: frozenset[str],
) -> bool:
    """Independent scenes may progress independently unless entities overlap."""
    return bool(scenes_a & scenes_b or entities_a & entities_b)
