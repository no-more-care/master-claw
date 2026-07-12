from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from masterclaw.pipelines.base import BoundedJsonPipeline, CompletionPort


class ActionResolution(StrEnum):
    ROLL = "roll"
    AUTOMATIC = "automatic"
    CLARIFICATION = "clarification"


class ActionInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: ActionResolution
    trait_names: list[str] = Field(default_factory=list)
    aspect_names: list[str] = Field(default_factory=list)
    flag: str | None = None
    difficulty: int | None = Field(default=None, ge=1)
    evidence: list[str] = Field(default_factory=list, max_length=12)
    clarification_question: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_resolution_fields(self) -> ActionInterpretation:
        if self.resolution is ActionResolution.ROLL:
            if not self.trait_names or self.difficulty is None:
                raise ValueError("roll requires trait_names and difficulty")
            if self.clarification_question is not None:
                raise ValueError("roll cannot contain a clarification question")
        elif self.resolution is ActionResolution.CLARIFICATION:
            if not self.clarification_question:
                raise ValueError("clarification requires a question")
        return self


def create_action_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[ActionInterpretation]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=ActionInterpretation,
        static_system=(
            "Interpret one tabletop action declaration from supplied state. Select only "
            "exact trait, aspect and flag names present in the character projection. "
            "Never roll dice, choose reserve dice, narrate an outcome, or mutate state. "
            "Use clarification when required evidence is absent."
        ),
    )
