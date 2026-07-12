from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from masterclaw.pipelines.base import BoundedJsonPipeline, CompletionPort


class AdvancementSafetyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    reason: str = Field(min_length=1, max_length=500)
    evidence: list[str] = Field(default_factory=list, max_length=8)


def create_advancement_safety_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[AdvancementSafetyDecision]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=AdvancementSafetyDecision,
        static_system=(
            "Act as the game master gate for character advancement. Decide whether the "
            "current fictional situation gives this character enough safety and downtime "
            "to train. For a new trait, require plausible supplied learning justification "
            "and opportunity. Do not modify the character or scene."
        ),
    )
