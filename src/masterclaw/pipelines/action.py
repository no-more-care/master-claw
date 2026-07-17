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
    bonus_ids: list[str] = Field(default_factory=list, max_length=1)
    difficulty: int | None = Field(default=None, ge=1)
    evidence: list[str] = Field(max_length=12)
    clarification_question: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_resolution_fields(self) -> ActionInterpretation:
        if self.resolution is ActionResolution.ROLL:
            if not self.trait_names or self.difficulty is None:
                raise ValueError("roll requires trait_names and difficulty")
            if not self.evidence:
                raise ValueError("roll requires evidence")
            if self.clarification_question is not None:
                raise ValueError("roll cannot contain a clarification question")
        elif self.resolution is ActionResolution.CLARIFICATION:
            if not self.clarification_question:
                raise ValueError("clarification requires a question")
            if self.bonus_ids:
                raise ValueError("clarification cannot select a temporary bonus")
        elif self.bonus_ids:
            raise ValueError("automatic resolution cannot select a temporary bonus")
        elif not self.evidence:
            raise ValueError("automatic resolution requires evidence")
        return self


def create_action_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[ActionInterpretation]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=ActionInterpretation,
        static_system=(
            "Classify one tabletop action declaration as roll, automatic, or clarification. "
            "Choose the resolution path, but never execute or narrate it. Select only "
            "exact trait, aspect, flag and temporary bonus identifiers present in the "
            "character projection. Select at most one temporary bonus, and only when its "
            "trigger directly applies to the declaration. "
            "For roll and automatic resolutions, cite concrete supplied facts in evidence. "
            "Never roll dice, choose reserve dice, narrate an outcome, or mutate state. Use "
            "clarification when required evidence is absent. Treat secret_plot as GM-only "
            "consistency context: never expose it in evidence or a clarification, and never treat "
            "hidden information as available to the acting character unless current public scene "
            "facts establish that knowledge."
        ),
    )
