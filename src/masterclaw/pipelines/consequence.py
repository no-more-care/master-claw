from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from masterclaw.domain.characters import Condition, PlotItem
from masterclaw.domain.mechanics import TemporaryBonus, TemporaryBonusType
from masterclaw.domain.outcomes import CanonicalOutcomePatch, SceneNpcState
from masterclaw.pipelines.base import BoundedJsonPipeline, CompletionPort


def _clean_unique(values: list[str], *, label: str, maximum_length: int = 300) -> list[str]:
    cleaned = [value.strip() for value in values]
    if any(not value or len(value) > maximum_length for value in cleaned):
        raise ValueError(f"{label} must contain 1..{maximum_length} characters")
    if len(set(cleaned)) != len(cleaned):
        raise ValueError(f"{label} cannot be duplicated")
    return cleaned


class ConditionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=200)


class PlotItemDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)


class SceneNpcDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    npc_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    state: str = Field(min_length=1, max_length=500)


class TemporaryBonusDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bonus_id: str = Field(min_length=1, max_length=120)
    type: TemporaryBonusType
    trigger: str = Field(min_length=1, max_length=300)


class OutcomePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=500)
    add_facts: list[str] = Field(default_factory=list, max_length=6)
    remove_facts: list[str] = Field(default_factory=list, max_length=6)
    add_actor_conditions: list[ConditionDraft] = Field(default_factory=list, max_length=3)
    remove_actor_conditions: list[str] = Field(default_factory=list, max_length=3)
    add_actor_plot_items: list[PlotItemDraft] = Field(default_factory=list, max_length=3)
    remove_actor_plot_items: list[str] = Field(default_factory=list, max_length=3)
    move_actor_to_scene_id: str | None = Field(default=None, min_length=1, max_length=120)
    upsert_scene_npcs: list[SceneNpcDraft] = Field(default_factory=list, max_length=4)
    remove_scene_npc_ids: list[str] = Field(default_factory=list, max_length=4)
    open_threads: list[str] = Field(default_factory=list, max_length=4)
    close_threads: list[str] = Field(default_factory=list, max_length=4)
    grant_temporary_bonus: TemporaryBonusDraft | None = None

    @field_validator(
        "add_facts",
        "remove_facts",
        "remove_actor_conditions",
        "remove_actor_plot_items",
        "remove_scene_npc_ids",
        "open_threads",
        "close_threads",
    )
    @classmethod
    def validate_string_lists(cls, values: list[str]) -> list[str]:
        return _clean_unique(values, label="patch values")

    @model_validator(mode="after")
    def validate_non_conflicting_patch(self) -> OutcomePatch:
        if set(self.add_facts) & set(self.remove_facts):
            raise ValueError("the same scene fact cannot be added and removed")
        if set(self.open_threads) & set(self.close_threads):
            raise ValueError("the same thread cannot be opened and closed")
        npc_ids = [npc.npc_id for npc in self.upsert_scene_npcs]
        if len(set(npc_ids)) != len(npc_ids):
            raise ValueError("scene NPC ids cannot be duplicated")
        if set(npc_ids) & set(self.remove_scene_npc_ids):
            raise ValueError("the same scene NPC cannot be upserted and removed")
        condition_names = [condition.text for condition in self.add_actor_conditions]
        if len(set(condition_names)) != len(condition_names):
            raise ValueError("actor conditions cannot be duplicated")
        if set(condition_names) & set(self.remove_actor_conditions):
            raise ValueError("the same actor condition cannot be added and removed")
        item_names = [item.name for item in self.add_actor_plot_items]
        if len(set(item_names)) != len(item_names):
            raise ValueError("actor plot items cannot be duplicated")
        if set(item_names) & set(self.remove_actor_plot_items):
            raise ValueError("the same actor plot item cannot be added and removed")
        return self

    def to_domain(self) -> CanonicalOutcomePatch:
        return CanonicalOutcomePatch(
            summary=self.summary,
            add_facts=tuple(self.add_facts),
            remove_facts=tuple(self.remove_facts),
            add_actor_conditions=tuple(
                Condition(condition.text, condition.source)
                for condition in self.add_actor_conditions
            ),
            remove_actor_conditions=tuple(self.remove_actor_conditions),
            add_actor_plot_items=tuple(
                PlotItem(item.name, item.description) for item in self.add_actor_plot_items
            ),
            remove_actor_plot_items=tuple(self.remove_actor_plot_items),
            move_actor_to_scene_id=self.move_actor_to_scene_id,
            upsert_scene_npcs=tuple(
                SceneNpcState(npc.npc_id, npc.name, npc.state) for npc in self.upsert_scene_npcs
            ),
            remove_scene_npc_ids=tuple(self.remove_scene_npc_ids),
            open_threads=tuple(self.open_threads),
            close_threads=tuple(self.close_threads),
            grant_temporary_bonus=(
                None
                if self.grant_temporary_bonus is None
                else TemporaryBonus(
                    self.grant_temporary_bonus.bonus_id,
                    self.grant_temporary_bonus.type,
                    self.grant_temporary_bonus.trigger,
                )
            ),
        )


# Compatibility while application call sites migrate from the original facts-only contract.
SceneConsequencePlan = OutcomePatch


def create_consequence_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[OutcomePatch]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=OutcomePatch,
        static_system=(
            "Propose the minimal persistent OutcomePatch justified by the supplied resolved "
            "action, canonical targets and narrator-rights policy. Use only exact existing ids "
            "and exact values supplied in context. You may mutate only the acting character and "
            "the current scene; never control another player character. Remove only exact "
            "existing facts, conditions, items, NPC ids or threads. Movement may target only an "
            "existing allowed scene id. A temporary bonus must be concrete, bounded and usable "
            "once on a later matching roll. Never recalculate mechanics or expose hidden plot. "
            "Treat secret_plot as GM-only consistency context and never copy it into public scene "
            "facts or threads unless the resolved outcome explicitly establishes that revelation. "
            "Empty mutations are allowed when the outcome establishes no persistent change."
        ),
    )
