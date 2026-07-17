from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class MechanicsError(ValueError):
    pass


class NarratorRights(StrEnum):
    PLAYER_SUCCESS = "player_success"
    GM_SUCCESS = "gm_success"
    PLAYER_FAILURE = "player_failure"
    GM_FAILURE = "gm_failure"


class OutcomeAuthority(StrEnum):
    """Narrative authority supplied to consequence planning for any resolved outcome."""

    PLAYER_SUCCESS = NarratorRights.PLAYER_SUCCESS
    GM_SUCCESS = NarratorRights.GM_SUCCESS
    PLAYER_FAILURE = NarratorRights.PLAYER_FAILURE
    GM_FAILURE = NarratorRights.GM_FAILURE
    GM_AUTOMATIC = "gm_automatic"


class SocialRelation(StrEnum):
    FRIEND = "friend"
    NEUTRAL = "neutral"
    OPPONENT = "opponent"
    ENEMY = "enemy"


class DealPosition(StrEnum):
    NEUTRAL = "neutral"
    ADVANTAGEOUS = "advantageous"
    UNFAVOURABLE = "unfavourable"


class FlagType(StrEnum):
    RELATIONSHIP = "relationship"
    PERSONALITY = "personality"
    GOAL = "goal"
    BELIEF = "belief"


class TemporaryBonusType(StrEnum):
    EXTRA_DIE = "extra_die"
    DIFFICULTY_REDUCTION = "difficulty_reduction"


@dataclass(frozen=True, slots=True)
class TemporaryBonus:
    bonus_id: str
    type: TemporaryBonusType
    trigger: str

    def __post_init__(self) -> None:
        if not self.bonus_id.strip():
            raise MechanicsError("temporary bonus id cannot be empty")
        if not self.trigger.strip():
            raise MechanicsError("temporary bonus trigger cannot be empty")


@dataclass(frozen=True, slots=True)
class Flag:
    text: str
    type: FlagType
    locked: bool = True

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise MechanicsError("flag text cannot be empty")


@dataclass(frozen=True, slots=True)
class Trait:
    name: str
    level: int
    aspects: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.level < 1:
            raise MechanicsError(f"trait level must be positive: {self.name}")
        if len(set(self.aspects)) != len(self.aspects):
            raise MechanicsError(f"duplicate aspects in trait: {self.name}")


@dataclass(frozen=True, slots=True)
class CharacterSheet:
    name: str
    traits: tuple[Trait, ...]
    flags: tuple[Flag, ...]
    reserve_current: int = 7
    reserve_maximum: int = 7
    temporary_bonuses: tuple[TemporaryBonus, ...] = ()

    def __post_init__(self) -> None:
        if len({trait.name for trait in self.traits}) != len(self.traits):
            raise MechanicsError("trait names must be unique")
        if not 0 <= self.reserve_current <= self.reserve_maximum <= 7:
            raise MechanicsError("reserve must satisfy 0 <= current <= maximum <= 7")
        if len({bonus.bonus_id for bonus in self.temporary_bonuses}) != len(self.temporary_bonuses):
            raise MechanicsError("temporary bonus ids must be unique")


@dataclass(frozen=True, slots=True)
class CharacterRules:
    """Explicit policy keeps disputed source values out of hidden defaults."""

    minimum_traits: int
    maximum_traits: int
    minimum_trait_level: int
    maximum_trait_level: int
    required_trait_points: int = 18
    minimum_flags: int = 3


STARTING_CHARACTER_RULES = CharacterRules(
    minimum_traits=3,
    maximum_traits=9,
    minimum_trait_level=2,
    maximum_trait_level=6,
    required_trait_points=18,
    minimum_flags=3,
)


def validate_character(sheet: CharacterSheet, rules: CharacterRules) -> None:
    if not rules.minimum_traits <= len(sheet.traits) <= rules.maximum_traits:
        raise MechanicsError("character trait count is outside configured rules")
    if any(
        not rules.minimum_trait_level <= trait.level <= rules.maximum_trait_level
        for trait in sheet.traits
    ):
        raise MechanicsError("character trait level is outside configured rules")
    if sum(trait.level for trait in sheet.traits) != rules.required_trait_points:
        raise MechanicsError("character trait levels have an invalid total")
    if any(len(trait.aspects) != trait.level for trait in sheet.traits):
        raise MechanicsError("each trait must have exactly level aspects")
    if len(sheet.flags) < rules.minimum_flags:
        raise MechanicsError("character has fewer flags than configured minimum")
    if not any(flag.type is FlagType.RELATIONSHIP for flag in sheet.flags):
        raise MechanicsError("starting character requires a relationship flag")
    if any(not flag.locked for flag in sheet.flags):
        raise MechanicsError("starting character flags must be locked")


@dataclass(frozen=True, slots=True)
class PoolProposal:
    trait_names: tuple[str, ...]
    aspect_names: tuple[str, ...] = ()
    flag: str | None = None
    reserve_spent: int = 0
    difficulty: int = 1
    bonus_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ValidatedPool:
    size: int
    difficulty: int
    reserve_after_spend: int
    components: tuple[str, ...]
    bonus_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RollResult:
    dice: tuple[int, ...]
    hits: int
    difficulty: int
    narrator_rights: NarratorRights


def validate_pool(sheet: CharacterSheet, proposal: PoolProposal) -> ValidatedPool:
    if proposal.difficulty < 1:
        raise MechanicsError("difficulty must be positive")
    if not proposal.trait_names:
        raise MechanicsError("at least one trait is required")
    if len(set(proposal.trait_names)) != len(proposal.trait_names):
        raise MechanicsError("a trait cannot be counted more than once")
    trait_by_name = {trait.name: trait for trait in sheet.traits}
    selected_traits: list[Trait] = []
    for name in proposal.trait_names:
        try:
            selected_traits.append(trait_by_name[name])
        except KeyError as error:
            raise MechanicsError(f"unknown trait: {name}") from error

    aspect_owners: dict[str, list[str]] = {}
    for trait in selected_traits:
        for aspect in trait.aspects:
            aspect_owners.setdefault(aspect, []).append(trait.name)
    for aspect in proposal.aspect_names:
        if aspect not in aspect_owners:
            raise MechanicsError(f"aspect does not belong to a selected trait: {aspect}")
    if len(set(proposal.aspect_names)) != len(proposal.aspect_names):
        raise MechanicsError("an aspect cannot be counted more than once")
    flag_texts = {flag.text for flag in sheet.flags}
    if proposal.flag is not None and proposal.flag not in flag_texts:
        raise MechanicsError(f"unknown flag: {proposal.flag}")
    if not 0 <= proposal.reserve_spent <= sheet.reserve_current:
        raise MechanicsError("reserve spent exceeds available reserve")
    if len(set(proposal.bonus_ids)) != len(proposal.bonus_ids):
        raise MechanicsError("a temporary bonus cannot be counted more than once")
    if len(proposal.bonus_ids) > 1:
        raise MechanicsError("at most one temporary bonus may be used on a roll")
    bonus_by_id = {bonus.bonus_id: bonus for bonus in sheet.temporary_bonuses}
    selected_bonuses: list[TemporaryBonus] = []
    for bonus_id in proposal.bonus_ids:
        try:
            selected_bonuses.append(bonus_by_id[bonus_id])
        except KeyError as error:
            raise MechanicsError(f"unknown temporary bonus: {bonus_id}") from error

    components = tuple(
        [f"trait:{name}" for name in proposal.trait_names]
        + [f"aspect:{name}" for name in proposal.aspect_names]
        + ([f"flag:{proposal.flag}"] if proposal.flag else [])
        + ["reserve" for _ in range(proposal.reserve_spent)]
        + [
            f"bonus:{bonus.bonus_id}"
            for bonus in selected_bonuses
            if bonus.type is TemporaryBonusType.EXTRA_DIE
        ]
    )
    difficulty_reductions = sum(
        bonus.type is TemporaryBonusType.DIFFICULTY_REDUCTION for bonus in selected_bonuses
    )
    return ValidatedPool(
        size=len(components),
        difficulty=max(1, proposal.difficulty - difficulty_reductions),
        reserve_after_spend=sheet.reserve_current - proposal.reserve_spent,
        components=components,
        bonus_ids=proposal.bonus_ids,
    )


def narrator_rights(*, hits: int, difficulty: int) -> NarratorRights:
    if hits > difficulty:
        return NarratorRights.PLAYER_SUCCESS
    if hits == difficulty:
        return NarratorRights.GM_SUCCESS
    if hits == difficulty - 1:
        return NarratorRights.PLAYER_FAILURE
    return NarratorRights.GM_FAILURE


def social_difficulty(relation: SocialRelation, deal: DealPosition = DealPosition.NEUTRAL) -> int:
    base = {
        SocialRelation.FRIEND: 2,
        SocialRelation.NEUTRAL: 3,
        SocialRelation.OPPONENT: 4,
        SocialRelation.ENEMY: 5,
    }[relation]
    modifier = {
        DealPosition.NEUTRAL: 0,
        DealPosition.ADVANTAGEOUS: 1,
        DealPosition.UNFAVOURABLE: -1,
    }[deal]
    return max(1, base + modifier)


def roll_pool(pool: ValidatedPool, *, die: Callable[[], int] | None = None) -> RollResult:
    roller = die or (lambda: secrets.randbelow(6) + 1)
    dice = tuple(roller() for _ in range(pool.size))
    if any(value < 1 or value > 6 for value in dice):
        raise MechanicsError("die provider returned a value outside 1..6")
    hits = sum(value >= 4 for value in dice)
    return RollResult(
        dice=dice,
        hits=hits,
        difficulty=pool.difficulty,
        narrator_rights=narrator_rights(hits=hits, difficulty=pool.difficulty),
    )


def reserve_after_roll(
    *,
    reserve_after_spend: int,
    reserve_spent: int,
    hits: int,
    difficulty: int,
    reserve_maximum: int = 7,
) -> int:
    """Success loses spent dice; failure returns them and awards one die."""
    if hits >= difficulty:
        return reserve_after_spend
    return min(reserve_maximum, reserve_after_spend + reserve_spent + 1)
