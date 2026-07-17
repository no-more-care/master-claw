from __future__ import annotations

from dataclasses import dataclass

from masterclaw.domain.characters import Condition, PlotItem
from masterclaw.domain.mechanics import OutcomeAuthority, TemporaryBonus
from masterclaw.domain.state import NarratorRightsLevel


class OutcomePolicyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SceneNpcState:
    npc_id: str
    name: str
    state: str


@dataclass(frozen=True, slots=True)
class CanonicalOutcomePatch:
    summary: str
    add_facts: tuple[str, ...] = ()
    remove_facts: tuple[str, ...] = ()
    add_actor_conditions: tuple[Condition, ...] = ()
    remove_actor_conditions: tuple[str, ...] = ()
    add_actor_plot_items: tuple[PlotItem, ...] = ()
    remove_actor_plot_items: tuple[str, ...] = ()
    move_actor_to_scene_id: str | None = None
    upsert_scene_npcs: tuple[SceneNpcState, ...] = ()
    remove_scene_npc_ids: tuple[str, ...] = ()
    open_threads: tuple[str, ...] = ()
    close_threads: tuple[str, ...] = ()
    grant_temporary_bonus: TemporaryBonus | None = None

    @property
    def changes_actor(self) -> bool:
        return bool(
            self.add_actor_conditions
            or self.remove_actor_conditions
            or self.add_actor_plot_items
            or self.remove_actor_plot_items
            or self.grant_temporary_bonus is not None
        )

    @property
    def changes_scene_state(self) -> bool:
        return bool(
            self.add_facts
            or self.remove_facts
            or self.upsert_scene_npcs
            or self.remove_scene_npc_ids
            or self.open_threads
            or self.close_threads
        )


def enforce_narrator_rights_policy(
    patch: CanonicalOutcomePatch,
    *,
    authority: OutcomeAuthority,
    level: NarratorRightsLevel,
) -> None:
    """Reject player-authored mutations beyond the configured narrator-rights scale."""
    if authority in {
        OutcomeAuthority.GM_SUCCESS,
        OutcomeAuthority.GM_FAILURE,
        OutcomeAuthority.GM_AUTOMATIC,
    }:
        return
    if level is NarratorRightsLevel.DISABLED:
        if (
            patch.changes_actor
            or patch.changes_scene_state
            or patch.move_actor_to_scene_id is not None
        ):
            raise OutcomePolicyError("player-owned persistent mutations are disabled")
        return
    if level is NarratorRightsLevel.MINOR:
        if (
            patch.add_actor_plot_items
            or patch.remove_actor_plot_items
            or patch.move_actor_to_scene_id is not None
            or patch.upsert_scene_npcs
            or patch.remove_scene_npc_ids
            or patch.open_threads
            or patch.close_threads
        ):
            raise OutcomePolicyError("minor narrator rights cannot change items, movement, or NPCs")
        if len(patch.add_facts) + len(patch.remove_facts) > 1:
            raise OutcomePolicyError("minor narrator rights allow one immediate scene fact change")
        reward_count = len(patch.remove_actor_conditions) + int(
            patch.grant_temporary_bonus is not None
        )
        if reward_count > 1:
            raise OutcomePolicyError("minor narrator rights allow at most one reward")
    elif level is NarratorRightsLevel.SIGNIFICANT:
        if patch.move_actor_to_scene_id is not None or patch.remove_scene_npc_ids:
            raise OutcomePolicyError(
                "significant narrator rights cannot move actors or remove scene NPCs"
            )
        if len(patch.upsert_scene_npcs) > 1:
            raise OutcomePolicyError(
                "significant narrator rights allow at most one scene NPC change"
            )
        if len(patch.add_facts) + len(patch.remove_facts) > 2:
            raise OutcomePolicyError(
                "significant narrator rights allow at most two scene fact changes"
            )
        if len(patch.add_actor_plot_items) + len(patch.remove_actor_plot_items) > 1:
            raise OutcomePolicyError(
                "significant narrator rights allow at most one plot-item change"
            )
        if len(patch.open_threads) + len(patch.close_threads) > 1:
            raise OutcomePolicyError(
                "significant narrator rights allow at most one plot-thread change"
            )
        reward_count = (
            len(patch.remove_actor_conditions)
            + len(patch.add_actor_plot_items)
            + int(patch.grant_temporary_bonus is not None)
        )
        if reward_count > 2:
            raise OutcomePolicyError("significant narrator rights allow at most two actor rewards")
    if authority is OutcomeAuthority.PLAYER_FAILURE and patch.grant_temporary_bonus is not None:
        raise OutcomePolicyError("a failed outcome cannot grant a positive temporary bonus")
