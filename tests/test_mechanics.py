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
