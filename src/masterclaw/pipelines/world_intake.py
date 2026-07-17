from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from masterclaw.pipelines.base import BoundedJsonPipeline, CompletionPort

WorldSpecifiedField = Literal[
    "title",
    "genre",
    "tone",
    "scale",
    "player_role",
    "themes",
    "content_constraints",
    "narrative_style",
    "narrative_perspective",
    "narrative_detail",
    "narrator_rights_level",
    "reserve_recovery_mode",
    "progression_enabled",
    "locale",
    "pregenerated_character_count",
    "pregenerated_character_briefs",
]
WORLD_SPECIFIED_FIELDS = get_args(WorldSpecifiedField)


class WorldCreationBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=120)
    brief: str = Field(min_length=20, max_length=12000)
    genre: str | None = Field(default=None, max_length=120)
    tone: str | None = Field(default=None, max_length=200)
    scale: str | None = Field(default=None, max_length=120)
    player_role: str | None = Field(default=None, max_length=300)
    themes: list[str] = Field(default_factory=list, max_length=8)
    content_constraints: list[str] = Field(default_factory=list, max_length=12)
    narrative_style: str | None = Field(default=None, max_length=300)
    narrative_perspective: str | None = Field(default=None, max_length=200)
    narrative_detail: Literal["concise", "balanced", "detailed"] | None = None
    narrator_rights_level: Literal["disabled", "minor", "significant", "madness"] | None = None
    reserve_recovery_mode: Literal["safe_rest", "roleplay_award", "both"] | None = None
    progression_enabled: bool | None = None
    locale: Literal["ru", "en"] | None = None
    pregenerated_character_count: int | None = Field(default=None, ge=3, le=6)
    pregenerated_character_briefs: list[str] = Field(default_factory=list, max_length=6)
    specified_fields: list[WorldSpecifiedField] = Field(
        default_factory=list,
        max_length=len(WORLD_SPECIFIED_FIELDS),
    )


def create_world_intake_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[WorldCreationBrief]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=WorldCreationBrief,
        static_system=(
            "Turn one player's natural-language world or module creation request into a concise "
            "title and a faithful complete brief. Extract genre, tone, scale, player role, themes "
            "and content constraints only when stated or clearly implied by the player's words. "
            "Also extract requested narrative style, perspective and detail; narrator-rights "
            "level; reserve-recovery mode; progression; locale; and the requested count or "
            "concepts of pregenerated characters. Use only the exact enum values allowed by the "
            "output schema. "
            "List only explicitly supplied field names in specified_fields. The supported names "
            f"are: {', '.join(WORLD_SPECIFIED_FIELDS)}. Never mark a value supplied only by prior "
            "context or a default. Preserve every supplied premise and requested motif. When "
            "prior settings are supplied, this is a revision. Return only a concise, faithful "
            "normalization of the new message in brief; do not repeat the existing world brief. "
            "Retain the prior title unless the player explicitly renames the world. The "
            "application appends this revision to the canonical brief, where later revisions "
            "take precedence over conflicting earlier text. Do not invent the module plot, "
            "locations, factions, secrets, characters, game id, or mechanics yet."
        ),
    )
