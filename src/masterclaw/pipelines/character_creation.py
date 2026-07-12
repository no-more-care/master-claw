from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from masterclaw.domain.mechanics import FlagType
from masterclaw.pipelines.base import BoundedJsonPipeline, CompletionPort


class TraitDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    level: int = Field(ge=2, le=6)
    aspects: list[str] = Field(min_length=2, max_length=6)


class FlagDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=300)
    type: FlagType


class CharacterDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    biography: str = Field(min_length=1, max_length=3000)
    traits: list[TraitDraft] = Field(min_length=3, max_length=9)
    flags: list[FlagDraft] = Field(min_length=3, max_length=12)


def create_character_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[CharacterDraft]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=CharacterDraft,
        static_system=(
            "Create one starting BlackBirdPie character from the player brief and public "
            "world context. Trait levels must total exactly 18; each trait level is 2..6; "
            "aspect count equals level. Include at least three flags and at least one positive "
            "relationship flag. Do not use or reveal secret plot information."
        ),
    )
