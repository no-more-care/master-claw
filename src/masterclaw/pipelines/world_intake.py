from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    # Intake preserves an explicitly requested unsupported count so the application can explain
    # the 3-6 world contract without asking the model to silently coerce the player's request.
    pregenerated_character_count: int | None = Field(default=None, ge=0, le=1000)
    pregenerated_character_briefs: list[str] = Field(default_factory=list, max_length=6)
    specified_fields: list[WorldSpecifiedField] = Field(
        default_factory=list,
        max_length=len(WORLD_SPECIFIED_FIELDS),
    )

    @field_validator("title", "brief", mode="before")
    @classmethod
    def strip_required_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator(
        "genre",
        "tone",
        "scale",
        "player_role",
        "narrative_style",
        "narrative_perspective",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None

    @field_validator(
        "themes",
        "content_constraints",
        "pregenerated_character_briefs",
        mode="after",
    )
    @classmethod
    def require_meaningful_list_items(cls, values: list[str]) -> list[str]:
        normalized = [item.strip() for item in values]
        if any(not item for item in normalized):
            raise ValueError("world setting lists must not contain blank items")
        return normalized

    @model_validator(mode="after")
    def require_explicit_provenance(self) -> WorldCreationBrief:
        specified = set(self.specified_fields)
        if len(specified) != len(self.specified_fields):
            raise ValueError("specified_fields must not contain duplicates")

        scalar_fields = (
            "genre",
            "tone",
            "scale",
            "player_role",
            "narrative_style",
            "narrative_perspective",
            "narrative_detail",
            "narrator_rights_level",
            "reserve_recovery_mode",
            "progression_enabled",
            "locale",
            "pregenerated_character_count",
        )
        list_fields = ("themes", "content_constraints", "pregenerated_character_briefs")
        for field_name in scalar_fields:
            if getattr(self, field_name) is not None and field_name not in specified:
                raise ValueError(f"{field_name} has a value but is absent from specified_fields")
        for field_name in list_fields:
            if getattr(self, field_name) and field_name not in specified:
                raise ValueError(f"{field_name} has values but is absent from specified_fields")
        return self


def create_world_intake_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[WorldCreationBrief]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=WorldCreationBrief,
        static_system=(
            "Turn one player's natural-language world or module creation request into a concise "
            "title and a faithful complete brief. Extract genre, tone, scale, player role, themes "
            "and content constraints only when explicitly stated in the player's current message. "
            "Phrases such as 'players are X', 'play as X', 'about X protagonists', or their "
            "locale-equivalent wording explicitly state player_role. Do not leave an explicit "
            "setting only inside brief: populate its typed field and specified_fields entry too. "
            "Also extract requested narrative style, perspective and detail; narrator-rights "
            "level; reserve-recovery mode; progression; locale; and the requested count or "
            "concepts of pregenerated characters. Use only the exact enum values allowed by the "
            "output schema. "
            "Every non-null optional setting scalar and every non-empty list extracted from the "
            "current message must also be named in specified_fields; do not return inferred "
            "setting values. Name title only when the player explicitly supplied or renamed it; "
            "a concise title created by the intake model is not player-specified. The "
            "supported names are: "
            f"{', '.join(WORLD_SPECIFIED_FIELDS)}. Never mark a value supplied only by prior "
            "context or a default. A field named in specified_fields with a null scalar means the "
            "player explicitly asked to restore that setting's default. An empty "
            "content_constraints list named in specified_fields means the player explicitly "
            "removed all additional boundaries; preserve that empty list. Likewise, an explicit "
            "false progression value is a real player value. If the player requests a count "
            "outside the supported three-to-six pregenerated characters, preserve that exact "
            "count instead of coercing it; the application will explain the generation "
            "contract. Preserve every supplied premise and requested motif. When "
            "prior settings are supplied, this is a revision. Return only a concise, faithful "
            "normalization of the new message in brief; do not repeat the existing world brief. "
            "Retain the prior title unless the player explicitly renames the world. The "
            "application appends this revision to the canonical brief, where later revisions "
            "take precedence over conflicting earlier text. Do not invent the module plot, "
            "locations, factions, secrets, characters, game id, or mechanics yet."
        ),
    )
