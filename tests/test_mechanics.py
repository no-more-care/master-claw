import pytest

from masterclaw.domain.mechanics import (
    STARTING_CHARACTER_RULES,
    CharacterSheet,
    DealPosition,
    Flag,
    FlagType,
    MechanicsError,
    NarratorRights,
    PoolProposal,
    SocialRelation,
    TemporaryBonus,
    TemporaryBonusType,
    Trait,
    narrator_rights,
    reserve_after_roll,
    roll_pool,
    social_difficulty,
    validate_character,
    validate_pool,
)


def sheet() -> CharacterSheet:
    return CharacterSheet(
        name="Hero",
        traits=(
            Trait("Body", 5, ("Strong", "Enduring", "Fast", "Brawler", "Climber")),
            Trait("Mind", 5, ("Observant", "Learned", "Planner", "Memory", "Focus")),
            Trait("Charm", 4, ("Polite", "Empathic", "Deceiver", "Orator")),
            Trait("Craft", 4, ("Locks", "Repair", "Tools", "Traps")),
        ),
        flags=(
            Flag("Protect the weak", FlagType.RELATIONSHIP),
            Flag("Never lie", FlagType.BELIEF),
            Flag("Find home", FlagType.GOAL),
        ),
        reserve_current=5,
    )


def test_starting_character_uses_decided_range_and_total() -> None:
    validate_character(sheet(), STARTING_CHARACTER_RULES)


def test_starting_character_may_have_nine_level_two_traits() -> None:
    nine_traits = CharacterSheet(
        name="Broad Hero",
        traits=tuple(
            Trait(f"Trait {index}", 2, (f"Aspect {index}.1", f"Aspect {index}.2"))
            for index in range(9)
        ),
        flags=(
            Flag("Relationship", FlagType.RELATIONSHIP),
            Flag("Belief", FlagType.BELIEF),
            Flag("Goal", FlagType.GOAL),
        ),
    )
    validate_character(nine_traits, STARTING_CHARACTER_RULES)


def test_trait_level_does_not_increase_dice_count() -> None:
    pool = validate_pool(sheet(), PoolProposal(trait_names=("Body",), difficulty=2))
    assert pool.size == 1


@pytest.mark.parametrize("difficulty", (0, 1, 8, 99))
def test_pool_rejects_difficulty_outside_the_canonical_scale(difficulty: int) -> None:
    with pytest.raises(MechanicsError, match="between 2 and 7"):
        validate_pool(sheet(), PoolProposal(trait_names=("Body",), difficulty=difficulty))


def test_pool_counts_each_selected_source_once() -> None:
    pool = validate_pool(
        sheet(),
        PoolProposal(
            trait_names=("Body", "Mind"),
            aspect_names=("Strong", "Observant"),
            flag="Protect the weak",
            reserve_spent=2,
            difficulty=3,
        ),
    )
    assert pool.size == 7
    assert pool.reserve_after_spend == 3


def test_aspect_requires_its_trait() -> None:
    with pytest.raises(MechanicsError):
        validate_pool(sheet(), PoolProposal(trait_names=("Mind",), aspect_names=("Strong",)))


@pytest.mark.parametrize(
    ("hits", "difficulty", "rights"),
    [
        (4, 3, NarratorRights.PLAYER_SUCCESS),
        (3, 3, NarratorRights.GM_SUCCESS),
        (2, 3, NarratorRights.PLAYER_FAILURE),
        (1, 3, NarratorRights.GM_FAILURE),
    ],
)
def test_narrator_rights_boundaries(hits, difficulty, rights) -> None:
    assert narrator_rights(hits=hits, difficulty=difficulty) is rights


def test_roll_uses_injected_rng_and_counts_four_to_six_as_hits() -> None:
    values = iter((1, 4, 6))
    pool = validate_pool(
        sheet(), PoolProposal(trait_names=("Body",), reserve_spent=2, difficulty=2)
    )
    result = roll_pool(pool, die=lambda: next(values))
    assert result.dice == (1, 4, 6)
    assert result.hits == 2
    assert result.narrator_rights is NarratorRights.GM_SUCCESS


def test_failed_roll_returns_spent_reserve_and_awards_one() -> None:
    assert reserve_after_roll(reserve_after_spend=2, reserve_spent=3, hits=1, difficulty=3) == 6
    assert reserve_after_roll(reserve_after_spend=4, reserve_spent=3, hits=0, difficulty=3) == 7


def test_social_conflict_difficulty_is_computed_without_llm() -> None:
    assert social_difficulty(SocialRelation.FRIEND) == 2
    assert social_difficulty(SocialRelation.ENEMY) == 5
    assert social_difficulty(SocialRelation.OPPONENT, DealPosition.ADVANTAGEOUS) == 5
    assert social_difficulty(SocialRelation.OPPONENT, DealPosition.UNFAVOURABLE) == 3
    assert social_difficulty(SocialRelation.FRIEND, DealPosition.UNFAVOURABLE) == 2
    assert social_difficulty(SocialRelation.ENEMY, DealPosition.ADVANTAGEOUS) == 6
    assert all(
        2 <= social_difficulty(relation, position) <= 7
        for relation in SocialRelation
        for position in DealPosition
    )


def test_extra_die_temporary_bonus_is_an_explicit_pool_component() -> None:
    rewarded = CharacterSheet(
        name=sheet().name,
        traits=sheet().traits,
        flags=sheet().flags,
        reserve_current=sheet().reserve_current,
        temporary_bonuses=(
            TemporaryBonus(
                "archive-route",
                TemporaryBonusType.EXTRA_DIE,
                "Following the courier through the archive",
            ),
        ),
    )

    pool = validate_pool(
        rewarded,
        PoolProposal(
            trait_names=("Body",),
            difficulty=3,
            bonus_ids=("archive-route",),
        ),
    )

    assert pool.size == 2
    assert pool.difficulty == 3
    assert pool.bonus_ids == ("archive-route",)
    assert "bonus:archive-route" in pool.components


def test_difficulty_reduction_temporary_bonus_never_reduces_below_one() -> None:
    rewarded = CharacterSheet(
        name=sheet().name,
        traits=sheet().traits,
        flags=sheet().flags,
        reserve_current=sheet().reserve_current,
        temporary_bonuses=(
            TemporaryBonus(
                "known-weakness",
                TemporaryBonusType.DIFFICULTY_REDUCTION,
                "Exploiting the sentinel's known weakness",
            ),
        ),
    )

    pool = validate_pool(
        rewarded,
        PoolProposal(
            trait_names=("Mind",),
            difficulty=2,
            bonus_ids=("known-weakness",),
        ),
    )

    assert pool.size == 1
    assert pool.difficulty == 1


def test_unknown_or_multiple_temporary_bonuses_fail_closed() -> None:
    rewarded = CharacterSheet(
        name=sheet().name,
        traits=sheet().traits,
        flags=sheet().flags,
        reserve_current=sheet().reserve_current,
        temporary_bonuses=(
            TemporaryBonus("one", TemporaryBonusType.EXTRA_DIE, "First trigger"),
            TemporaryBonus("two", TemporaryBonusType.EXTRA_DIE, "Second trigger"),
        ),
    )

    with pytest.raises(MechanicsError, match="unknown temporary bonus"):
        validate_pool(
            rewarded,
            PoolProposal(trait_names=("Body",), bonus_ids=("missing",)),
        )
    with pytest.raises(MechanicsError, match="at most one"):
        validate_pool(
            rewarded,
            PoolProposal(trait_names=("Body",), bonus_ids=("one", "two")),
        )
