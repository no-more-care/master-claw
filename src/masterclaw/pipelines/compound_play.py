from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from masterclaw.pipelines.base import BoundedJsonPipeline, CompletionPort


class PlayRequestPartKind(StrEnum):
    ROLEPLAY = "roleplay"
    SCENE_QUESTION = "scene_question"
    ACTION = "action"


class PlayRequestPart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: PlayRequestPartKind
    text: str = Field(min_length=1, max_length=4000)
    conditional_on_previous: bool = False


class CompoundPlayPlan(BaseModel):
    """Ordered, non-resolving decomposition of one compound player message."""

    model_config = ConfigDict(extra="forbid")

    parts: list[PlayRequestPart] = Field(default_factory=list, max_length=4)
    clarification_question: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_plan(self) -> CompoundPlayPlan:
        if self.clarification_question is not None:
            if self.parts:
                raise ValueError("a clarification plan cannot also execute request parts")
            return self
        if len(self.parts) < 2:
            raise ValueError("a compound plan requires at least two request parts")
        action_indexes = [
            index
            for index, part in enumerate(self.parts)
            if part.kind is PlayRequestPartKind.ACTION
        ]
        if len(action_indexes) > 1:
            raise ValueError("a compound plan may contain at most one action")
        if action_indexes and action_indexes[0] != len(self.parts) - 1:
            raise ValueError("an action must be the final compound request part")
        for index, part in enumerate(self.parts):
            if index == 0 and part.conditional_on_previous:
                raise ValueError("the first request part cannot depend on a prior answer")
            if part.conditional_on_previous and part.kind is not PlayRequestPartKind.ACTION:
                raise ValueError("only the final action may depend on a prior answer")
        return self


def create_compound_play_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[CompoundPlayPlan]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=CompoundPlayPlan,
        static_system=(
            "Decompose one compound tabletop-player message into its ordered semantic parts. "
            "Use roleplay for in-character speech or harmless description, scene_question for a "
            "request for perceivable information, and action for something the character tries "
            "to accomplish. Preserve the player's wording and conditions. Mark the final action "
            "conditional_on_previous when it should happen only if an earlier answer establishes "
            "a condition. Never answer the question, resolve the action, roll dice, narrate an "
            "outcome, or mutate state. If the order or ownership is unsafe or genuinely unclear, "
            "return one concrete clarification question and no parts."
        ),
    )
