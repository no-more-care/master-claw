import pytest

from masterclaw.domain.characters import Condition, PlotItem
from masterclaw.domain.mechanics import (
    OutcomeAuthority,
    TemporaryBonus,
    TemporaryBonusType,
)
from masterclaw.domain.outcomes import (
    CanonicalOutcomePatch,
    OutcomePolicyError,
    SceneNpcState,
    enforce_narrator_rights_policy,
)
from masterclaw.domain.state import NarratorRightsLevel


def test_minor_player_rights_allow_one_fact_and_one_small_reward() -> None:
    enforce_narrator_rights_policy(
        CanonicalOutcomePatch(
            summary="The hero regains their footing.",
            add_facts=("The loose bridge is now known to be unstable.",),
            remove_actor_conditions=("Pinned",),
        ),
        authority=OutcomeAuthority.PLAYER_SUCCESS,
        level=NarratorRightsLevel.MINOR,
    )


@pytest.mark.parametrize(
    "patch",
    [
        CanonicalOutcomePatch(
            summary="Takes an item",
            add_actor_plot_items=(PlotItem("Royal seal"),),
        ),
        CanonicalOutcomePatch(
            summary="Moves",
            move_actor_to_scene_id="vault",
        ),
        CanonicalOutcomePatch(
            summary="Creates NPC",
            upsert_scene_npcs=(SceneNpcState("guard", "Guard", "Friendly"),),
        ),
    ],
)
def test_minor_player_rights_reject_significant_mutations(patch) -> None:
    with pytest.raises(OutcomePolicyError):
        enforce_narrator_rights_policy(
            patch,
            authority=OutcomeAuthority.PLAYER_SUCCESS,
            level=NarratorRightsLevel.MINOR,
        )


def test_disabled_player_rights_reject_persistent_condition() -> None:
    with pytest.raises(OutcomePolicyError, match="disabled"):
        enforce_narrator_rights_policy(
            CanonicalOutcomePatch(
                summary="Condition",
                add_actor_conditions=(Condition("Inspired", "player narration"),),
            ),
            authority=OutcomeAuthority.PLAYER_SUCCESS,
            level=NarratorRightsLevel.DISABLED,
        )


def test_player_failure_cannot_grant_positive_temporary_bonus() -> None:
    with pytest.raises(OutcomePolicyError, match="failed outcome"):
        enforce_narrator_rights_policy(
            CanonicalOutcomePatch(
                summary="Failure",
                grant_temporary_bonus=TemporaryBonus(
                    "lucky-break",
                    TemporaryBonusType.EXTRA_DIE,
                    "The next attempt to open the gate",
                ),
            ),
            authority=OutcomeAuthority.PLAYER_FAILURE,
            level=NarratorRightsLevel.MADNESS,
        )


def test_significant_rights_allow_one_minor_npc_and_item() -> None:
    enforce_narrator_rights_policy(
        CanonicalOutcomePatch(
            summary="A local helper leaves a useful token.",
            upsert_scene_npcs=(SceneNpcState("helper", "Helper", "Offers directions"),),
            add_actor_plot_items=(PlotItem("Brass token"),),
            add_facts=("A side passage is marked with fresh chalk.",),
        ),
        authority=OutcomeAuthority.PLAYER_SUCCESS,
        level=NarratorRightsLevel.SIGNIFICANT,
    )


@pytest.mark.parametrize(
    "patch",
    [
        CanonicalOutcomePatch(summary="Move", move_actor_to_scene_id="vault"),
        CanonicalOutcomePatch(summary="Remove", remove_scene_npc_ids=("guard",)),
        CanonicalOutcomePatch(
            summary="Many NPCs",
            upsert_scene_npcs=(
                SceneNpcState("one", "One", "Arrives"),
                SceneNpcState("two", "Two", "Arrives"),
            ),
        ),
    ],
)
def test_significant_rejects_major_mutations_that_madness_allows(patch) -> None:
    with pytest.raises(OutcomePolicyError, match="significant"):
        enforce_narrator_rights_policy(
            patch,
            authority=OutcomeAuthority.PLAYER_SUCCESS,
            level=NarratorRightsLevel.SIGNIFICANT,
        )
    enforce_narrator_rights_policy(
        patch,
        authority=OutcomeAuthority.PLAYER_SUCCESS,
        level=NarratorRightsLevel.MADNESS,
    )
