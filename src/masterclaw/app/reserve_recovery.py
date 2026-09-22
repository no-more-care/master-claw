"""Eligibility contracts for reserve recovery, without resource arithmetic or model outputs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from masterclaw.app.context_inputs import ContextInputSnapshot
from masterclaw.domain.state import GameState, ReserveRecoveryMode


class ReserveRecoveryVerdict(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    UNCERTAIN = "uncertain"


class ReserveRecoveryReason(StrEnum):
    SAFE_REST_COMPLETED = "safe_rest_completed"
    NO_COMPLETED_SAFE_REST = "no_completed_safe_rest"
    OBSERVABLE_ROLEPLAY = "observable_roleplay"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True, slots=True)
class SafeRestEligibility:
    verdict: ReserveRecoveryVerdict
    reason: ReserveRecoveryReason
    display_detail: str | None = None


@dataclass(frozen=True, slots=True)
class RoleplayEligibility:
    player_id: str
    verdict: ReserveRecoveryVerdict
    reason: ReserveRecoveryReason
    display_detail: str


@dataclass(frozen=True, slots=True)
class ReserveRecoveryAssessment:
    safe_rest: SafeRestEligibility
    roleplay: tuple[RoleplayEligibility, ...] = ()


@dataclass(frozen=True, slots=True)
class ReserveRecoverySnapshot:
    game_id: str
    player_id: str | None
    mode: ReserveRecoveryMode
    inputs: ContextInputSnapshot


class ReserveRecoveryDecider(Protocol):
    async def assess(self, snapshot: ReserveRecoverySnapshot) -> ReserveRecoveryAssessment: ...


class ReserveRecoveryObserver(Protocol):
    async def observe(
        self, snapshot: ReserveRecoverySnapshot, canonical_assessment: ReserveRecoveryAssessment
    ) -> object: ...


class ReserveRecoverySnapshotCapture(Protocol):
    def __call__(
        self,
        *,
        game: GameState,
        scene: dict[str, object],
        outcome_source: dict[str, object],
        player_id: str | None,
    ) -> ReserveRecoverySnapshot: ...
