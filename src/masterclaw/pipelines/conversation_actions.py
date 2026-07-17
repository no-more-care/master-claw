from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from masterclaw.domain.state import NarratorRightsLevel, ReserveRecoveryMode
from masterclaw.pipelines.base import BoundedJsonPipeline, CompletionPort


class AdvancementKind(StrEnum):
    RAISE = "raise"
    LEARN = "learn"


class AdvancementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: AdvancementKind
    trait_name: str = Field(min_length=1, max_length=100)
    aspects: list[str] = Field(default_factory=list, max_length=2)
    justification: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_request(self) -> AdvancementRequest:
        expected = 1 if self.kind is AdvancementKind.RAISE else 2
        if len(self.aspects) != expected:
            raise ValueError(f"{self.kind.value} requires exactly {expected} aspect(s)")
        if self.kind is AdvancementKind.LEARN and not self.justification:
            raise ValueError("learning a trait requires justification")
        return self


def create_advancement_intake_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[AdvancementRequest]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=AdvancementRequest,
        static_system=(
            "Extract one natural-language character advancement request. Raising an existing trait "
            "adds exactly one named aspect. Learning a new trait requires exactly two aspects and "
            "a concrete in-fiction learning justification. Preserve player wording; do not "
            "approve, price, or apply advancement."
        ),
    )


class GameConfigurationKind(StrEnum):
    PROGRESSION = "progression"
    NARRATOR_RIGHTS = "narrator_rights"
    NARRATIVE_CHANNEL = "narrative_channel"
    CREATE_SCENE = "create_scene"
    RESERVE_RECOVERY = "reserve_recovery"


class GameConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: GameConfigurationKind
    enabled: bool | None = None
    narrator_rights: NarratorRightsLevel | None = None
    channel_id: str | None = Field(default=None, pattern=r"^[0-9]{1,20}$")
    scene_title: str | None = Field(default=None, max_length=200)
    reserve_recovery_mode: ReserveRecoveryMode | None = None

    @model_validator(mode="after")
    def require_exactly_relevant_value(self) -> GameConfigurationRequest:
        fields = {
            GameConfigurationKind.PROGRESSION: "enabled",
            GameConfigurationKind.NARRATOR_RIGHTS: "narrator_rights",
            GameConfigurationKind.NARRATIVE_CHANNEL: "channel_id",
            GameConfigurationKind.CREATE_SCENE: "scene_title",
            GameConfigurationKind.RESERVE_RECOVERY: "reserve_recovery_mode",
        }
        expected = fields[self.kind]
        values = {
            "enabled": self.enabled,
            "narrator_rights": self.narrator_rights,
            "channel_id": self.channel_id,
            "scene_title": self.scene_title,
            "reserve_recovery_mode": self.reserve_recovery_mode,
        }
        if values[expected] is None:
            raise ValueError(f"{self.kind.value} requires {expected}")
        populated = [name for name, value in values.items() if value is not None]
        if populated != [expected]:
            raise ValueError(f"{self.kind.value} only accepts {expected}")
        return self


def create_game_configuration_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[GameConfigurationRequest]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=GameConfigurationRequest,
        static_system=(
            "Extract one natural-language game preparation setting. Supported operations are "
            "progression on/off, narrator-rights level, narrative Discord channel id, and creating "
            "a named scene, or reserve recovery mode (safe_rest, roleplay_award, or both). "
            "Discord channel mentions look like <#123>. Do not change state."
        ),
    )


class RollConfirmationKind(StrEnum):
    CONFIRM = "confirm"
    CANCEL = "cancel"


class RollConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: RollConfirmationKind
    reserve_spent: int = Field(default=0, ge=0, le=7)


def create_roll_confirmation_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[RollConfirmationRequest]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=RollConfirmationRequest,
        static_system=(
            "Extract the player's response to a proposed dice pool. They may confirm with zero "
            "or a stated number of reserve dice, or cancel in ordinary language. Convert number "
            "words to an integer. Do not alter the proposed pool and do not resolve or roll dice."
        ),
    )
