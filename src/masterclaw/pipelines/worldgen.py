from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from masterclaw.pipelines.base import BoundedJsonPipeline, CompletionPort


class WorldLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)


class WorldDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    premise: str = Field(min_length=20, max_length=3000)
    themes: list[str] = Field(min_length=1, max_length=8)
    locations: list[WorldLocation] = Field(min_length=1, max_length=20)
    factions: list[str] = Field(default_factory=list, max_length=12)
    tensions: list[str] = Field(default_factory=list, max_length=12)
    secret_plot: str = Field(min_length=1, max_length=4000)


def create_worldgen_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[WorldDraft]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=WorldDraft,
        static_system=(
            "Create one internally consistent tabletop world draft from the supplied brief. "
            "Use stable snake-case identifiers. Keep secret_plot separate from public premise. "
            "Do not create game sessions, player characters, or Discord configuration."
        ),
    )
