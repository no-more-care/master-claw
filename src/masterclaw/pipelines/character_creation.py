from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from masterclaw.domain.mechanics import FlagType
from masterclaw.pipelines.base import BoundedJsonPipeline, CompletionPort


class TraitDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    level: int = Field(ge=2, le=6)
    aspects: list[str] = Field(min_length=2, max_length=6)

    @model_validator(mode="after")
    def require_one_aspect_per_level(self) -> TraitDraft:
        if len(self.aspects) != self.level:
            raise ValueError("aspect count must equal trait level")
        normalized_aspects = [item.strip().casefold() for item in self.aspects]
        if len(set(normalized_aspects)) != len(normalized_aspects):
            raise ValueError("trait aspects must be unique")
        return self


class FlagDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=300)
    type: FlagType
    is_positive: bool

    @model_validator(mode="after")
    def positive_only_describes_relationships(self) -> FlagDraft:
        if self.is_positive and self.type is not FlagType.RELATIONSHIP:
            raise ValueError("only a relationship flag can be marked positive")
        return self


class CharacterDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    biography: str = Field(min_length=1, max_length=3000)
    traits: list[TraitDraft] = Field(min_length=3, max_length=9)
    flags: list[FlagDraft] = Field(min_length=3, max_length=12)

    @model_validator(mode="after")
    def require_starting_sheet_rules(self) -> CharacterDraft:
        if sum(trait.level for trait in self.traits) != 18:
            raise ValueError("starting trait levels must total exactly 18")
        normalized_traits = [trait.name.strip().casefold() for trait in self.traits]
        if len(set(normalized_traits)) != len(normalized_traits):
            raise ValueError("trait names must be unique")
        normalized_aspects = [
            aspect.strip().casefold() for trait in self.traits for aspect in trait.aspects
        ]
        if len(set(normalized_aspects)) != len(normalized_aspects):
            raise ValueError("aspect names must be unique across the character")
        if not any(flag.type is FlagType.RELATIONSHIP and flag.is_positive for flag in self.flags):
            raise ValueError("starting character requires a positive relationship flag")
        return self


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
            "relationship flag, marking every flag with is_positive. Trait names and aspect "
            "names must be unique after trimming and case folding. Do not use or reveal secret "
            "plot information."
        ),
    )
