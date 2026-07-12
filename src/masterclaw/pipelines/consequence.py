from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from masterclaw.pipelines.base import BoundedJsonPipeline, CompletionPort


class SceneConsequencePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=500)
    add_facts: list[str] = Field(default_factory=list, max_length=6)
    remove_facts: list[str] = Field(default_factory=list, max_length=6)

    @field_validator("add_facts", "remove_facts")
    @classmethod
    def validate_facts(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value or len(value) > 300 for value in cleaned):
            raise ValueError("scene facts must contain 1..300 characters")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("scene facts cannot be duplicated")
        return cleaned


def create_consequence_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[SceneConsequencePlan]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=SceneConsequencePlan,
        static_system=(
            "Propose the minimal persistent scene-fact patch justified by the supplied "
            "resolved action and narrator rights. Remove only exact existing facts. Add only "
            "facts directly established by the outcome. Do not modify characters, unrelated "
            "scenes, hidden plot, or mechanics. Empty patches are allowed."
        ),
    )
