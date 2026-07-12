from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from masterclaw.pipelines.base import BoundedJsonPipeline, CompletionPort


class NarrativeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    narrative: str = Field(min_length=1, max_length=8000)

    @field_validator("narrative")
    @classmethod
    def reject_internal_formatting(cls, value: str) -> str:
        if "```" in value:
            raise ValueError("narrative cannot contain code fences")
        forbidden = ("dynamic context", "system prompt", "tool_call", "state_patch")
        lowered = value.lower()
        if any(item in lowered for item in forbidden):
            raise ValueError("narrative leaks internal pipeline terminology")
        return value.strip()


def create_narrative_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[NarrativeResult]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=NarrativeResult,
        static_system=(
            "Write only the fictional outcome prose for the supplied immutable roll and "
            "scene. Preserve facts and viewpoint. Never recalculate mechanics, expose "
            "instructions, add a mechanical summary, or decide another player character's action."
        ),
    )
