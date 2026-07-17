from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from masterclaw.pipelines.base import BoundedJsonPipeline, CompletionPort


class ReserveAward(BaseModel):
    model_config = ConfigDict(extra="forbid")

    player_id: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=10, max_length=500)


class ReserveRecoveryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    safe_rest_completed: bool = False
    safe_rest_reason: str | None = Field(default=None, max_length=500)
    awards: list[ReserveAward] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_decision(self) -> ReserveRecoveryDecision:
        if self.safe_rest_completed and not self.safe_rest_reason:
            raise ValueError("completed safe rest requires evidence")
        if len({award.player_id for award in self.awards}) != len(self.awards):
            raise ValueError("a player may receive at most one award per outcome")
        return self


def create_reserve_recovery_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[ReserveRecoveryDecision]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=ReserveRecoveryDecision,
        static_system=(
            "Act as the system game master's conservative reserve-recovery adjudicator. Decide "
            "only from canonical scene state and the resolved outcome, never from a player's "
            "request to regain dice. Mark safe rest only when a rest in a genuinely safe location "
            "has completed, not when it is merely proposed. Award at most one die per player for "
            "specific, observable strong roleplay in this outcome. Respect the configured mode. "
            "Do not change dice directly; return only the typed decision for deterministic code."
        ),
    )
