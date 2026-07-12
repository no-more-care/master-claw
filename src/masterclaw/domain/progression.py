from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from masterclaw.domain.mechanics import CharacterSheet, MechanicsError, Trait

ACTIVE_INTERVAL_CAP_SECONDS = 5 * 60
XP_INTERVAL_SECONDS = 30 * 60


@dataclass(frozen=True, slots=True)
class AdvancementResult:
    sheet: CharacterSheet
    xp_cost: int
    description: str


@dataclass(frozen=True, slots=True)
class ActivityUpdate:
    credited_seconds: int
    total_active_seconds: int
    xp_awarded_each: int


@dataclass(frozen=True, slots=True)
class AdvancementPermit:
    game_id: str
    player_id: str
    scene_id: str
    scene_revision: int
    reason: str


def credited_activity_seconds(
    previous: datetime | None,
    current: datetime,
    *,
    cap_seconds: int = ACTIVE_INTERVAL_CAP_SECONDS,
) -> int:
    if previous is None:
        return 0
    if previous.tzinfo is None or current.tzinfo is None:
        raise ValueError("activity timestamps must be timezone-aware")
    elapsed = int((current - previous).total_seconds())
    return min(max(elapsed, 0), cap_seconds)


def raise_trait(
    sheet: CharacterSheet,
    *,
    trait_name: str,
    new_aspect: str,
    available_xp: int,
) -> AdvancementResult:
    if not new_aspect.strip():
        raise MechanicsError("trait advancement requires a new aspect")
    traits = list(sheet.traits)
    matches = [index for index, trait in enumerate(traits) if trait.name == trait_name]
    if not matches:
        raise MechanicsError(f"unknown trait: {trait_name}")
    index = matches[0]
    trait = traits[index]
    if new_aspect in trait.aspects:
        raise MechanicsError("new aspect duplicates an existing aspect")
    new_level = trait.level + 1
    if available_xp < new_level:
        raise MechanicsError(f"raising trait to level {new_level} costs {new_level} XP")
    traits[index] = Trait(trait.name, new_level, trait.aspects + (new_aspect.strip(),))
    updated = replace(sheet, traits=tuple(traits))
    return AdvancementResult(updated, new_level, f"{trait.name}: {trait.level}→{new_level}")


def learn_trait(
    sheet: CharacterSheet,
    *,
    trait_name: str,
    aspects: tuple[str, str],
    justification: str,
    available_xp: int,
) -> AdvancementResult:
    if available_xp < 3:
        raise MechanicsError("learning a new trait costs 3 XP")
    if not trait_name.strip() or any(trait.name == trait_name for trait in sheet.traits):
        raise MechanicsError("new trait name is empty or already exists")
    if not justification.strip():
        raise MechanicsError("learning a new trait requires a justification")
    if len(set(aspects)) != 2 or any(not aspect.strip() for aspect in aspects):
        raise MechanicsError("a new level-2 trait requires two distinct aspects")
    trait = Trait(trait_name.strip(), 2, tuple(aspect.strip() for aspect in aspects))
    updated = replace(sheet, traits=sheet.traits + (trait,))
    return AdvancementResult(updated, 3, f"learned {trait.name} at level 2")
