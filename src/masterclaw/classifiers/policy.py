from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from masterclaw.classifiers.base import ChoiceAnswer, Contract, Identifier, Probability


class ClassifierMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"


class ClassifierUseCase(StrEnum):
    STATE_DISPATCH = "state_dispatch"
    ADVANCEMENT = "advancement"
    ACTION = "action"
    ACTION_CAPABILITY = "action_capability"
    PLAYER_NARRATION_RIGHTS = "player_narration_rights"
    RESERVE_RECOVERY = "reserve_recovery"
    OUTCOME_NARRATIVE_REVIEW = "outcome_narrative_review"
    WORLDGEN_SEMANTICS = "worldgen_semantics"


class ClassifierUseCaseConfig(Contract):
    mode: ClassifierMode = ClassifierMode.OFF
    threshold: float = Field(default=0.95, ge=0, le=1, allow_inf_nan=False)
    timeout_seconds: float = Field(default=5.0, gt=0, le=60, allow_inf_nan=False)


class AdvancementClassifierConfig(ClassifierUseCaseConfig):
    allow_threshold: float = Field(default=0.95, ge=0, le=1, allow_inf_nan=False)
    deny_threshold: float = Field(default=0.05, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered_thresholds(self) -> AdvancementClassifierConfig:
        if self.deny_threshold >= self.allow_threshold:
            raise ValueError("advancement deny_threshold must be below allow_threshold")
        return self


class ActionCapabilityClassifierConfig(ClassifierUseCaseConfig):
    capable_threshold: float = Field(default=0.95, ge=0.5, le=1, allow_inf_nan=False)
    blocked_threshold: float = Field(default=0.98, ge=0.5, le=1, allow_inf_nan=False)


class NarrationRightsClassifierConfig(ClassifierUseCaseConfig):
    allow_threshold: float = Field(default=0.95, ge=0, le=1, allow_inf_nan=False)
    deny_threshold: float = Field(default=0.05, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered_thresholds(self) -> NarrationRightsClassifierConfig:
        if self.deny_threshold >= self.allow_threshold:
            raise ValueError("narration deny_threshold must be below allow_threshold")
        return self


class ReserveRecoveryClassifierConfig(ClassifierUseCaseConfig):
    allow_threshold: float = Field(default=0.95, ge=0, le=1, allow_inf_nan=False)
    deny_threshold: float = Field(default=0.05, ge=0, le=1, allow_inf_nan=False)
    max_candidates: int = Field(default=8, ge=1, le=31)

    @model_validator(mode="after")
    def ordered_thresholds(self) -> ReserveRecoveryClassifierConfig:
        if self.deny_threshold >= self.allow_threshold:
            raise ValueError("reserve recovery deny_threshold must be below allow_threshold")
        return self


class OutcomeNarrativeClassifierConfig(ClassifierUseCaseConfig):
    allow_threshold: float = Field(default=0.95, ge=0, le=1, allow_inf_nan=False)
    deny_threshold: float = Field(default=0.05, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered_thresholds(self) -> OutcomeNarrativeClassifierConfig:
        if self.deny_threshold >= self.allow_threshold:
            raise ValueError("outcome narrative deny_threshold must be below allow_threshold")
        return self


class WorldSemanticClassifierConfig(ClassifierUseCaseConfig):
    allow_threshold: float = Field(default=0.95, ge=0, le=1, allow_inf_nan=False)
    deny_threshold: float = Field(default=0.05, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered_thresholds(self) -> WorldSemanticClassifierConfig:
        if self.deny_threshold >= self.allow_threshold:
            raise ValueError("world semantic deny_threshold must be below allow_threshold")
        return self


class ClassifierConfig(ClassifierUseCaseConfig):
    provider: Literal["jev"] = "jev"
    model: str = Field(default="~typesafe/jev-latest", min_length=1, max_length=200)
    max_concurrency: int = Field(default=4, ge=1, le=64)
    state_dispatch: ClassifierUseCaseConfig | None = None
    advancement: AdvancementClassifierConfig = AdvancementClassifierConfig()
    action: ClassifierUseCaseConfig = ClassifierUseCaseConfig()
    action_capability: ActionCapabilityClassifierConfig = ActionCapabilityClassifierConfig()
    player_narration_rights: NarrationRightsClassifierConfig = NarrationRightsClassifierConfig()
    reserve_recovery: ReserveRecoveryClassifierConfig = ReserveRecoveryClassifierConfig()
    outcome_narrative_review: OutcomeNarrativeClassifierConfig = OutcomeNarrativeClassifierConfig()
    worldgen_semantics: WorldSemanticClassifierConfig = WorldSemanticClassifierConfig()

    def for_use_case(self, use_case: ClassifierUseCase) -> ClassifierUseCaseConfig:
        if use_case is ClassifierUseCase.STATE_DISPATCH:
            # The original flat env settings remain aliases for state dispatch only.
            values = {
                "mode": self.mode,
                "threshold": self.threshold,
                "timeout_seconds": self.timeout_seconds,
            }
            if self.state_dispatch is not None:
                values.update(self.state_dispatch.model_dump(exclude_unset=True))
            return ClassifierUseCaseConfig.model_validate(values)
        return {
            ClassifierUseCase.ADVANCEMENT: self.advancement,
            ClassifierUseCase.ACTION: self.action,
            ClassifierUseCase.ACTION_CAPABILITY: self.action_capability,
            ClassifierUseCase.PLAYER_NARRATION_RIGHTS: self.player_narration_rights,
            ClassifierUseCase.RESERVE_RECOVERY: self.reserve_recovery,
            ClassifierUseCase.OUTCOME_NARRATIVE_REVIEW: self.outcome_narrative_review,
            ClassifierUseCase.WORLDGEN_SEMANTICS: self.worldgen_semantics,
        }[use_case]

    def enabled_use_cases(self) -> tuple[ClassifierUseCaseConfig, ...]:
        return tuple(
            config
            for use_case in ClassifierUseCase
            if (config := self.for_use_case(use_case)).mode is not ClassifierMode.OFF
        )


class SemanticEvaluationPolicy(ClassifierUseCaseConfig):
    blocked_choices: dict[Identifier, frozenset[Identifier]] = Field(default_factory=dict)
    noul_allow_threshold: Probability | None = None
    noul_deny_threshold: Probability | None = None


class ShadowDisposition(StrEnum):
    ELIGIBLE = "eligible"
    UNCERTAIN = "uncertain"
    BLOCKED = "blocked"


def assess_choice(
    answer: ChoiceAnswer, *, allowed: set[str], blocked: set[str], threshold: float
) -> ShadowDisposition:
    """Assess a shadow signal only; this never authorizes executing a command."""
    if answer.choice not in allowed or answer.choice in blocked:
        return ShadowDisposition.BLOCKED
    if min(answer.confidence, answer.probabilities[answer.choice]) < threshold:
        return ShadowDisposition.UNCERTAIN
    return ShadowDisposition.ELIGIBLE
