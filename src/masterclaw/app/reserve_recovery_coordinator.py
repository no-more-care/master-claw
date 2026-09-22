"""Replay-first custom checkpoint orchestration; GameService retains all arithmetic."""

from __future__ import annotations

import logging

from masterclaw.app.game_service import GameService
from masterclaw.app.reserve_recovery import (
    ReserveRecoveryAssessment,
    ReserveRecoveryDecider,
    ReserveRecoveryObserver,
    ReserveRecoveryReason,
    ReserveRecoverySnapshotCapture,
    ReserveRecoveryVerdict,
    RoleplayEligibility,
    SafeRestEligibility,
)
from masterclaw.storage.sqlite import SQLiteStore

logger = logging.getLogger(__name__)


class ReserveRecoveryCoordinator:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        capture_snapshot: ReserveRecoverySnapshotCapture,
        decider: ReserveRecoveryDecider | None = None,
        games: GameService | None = None,
        observer: ReserveRecoveryObserver | None = None,
    ) -> None:
        self._store = store
        self._capture_snapshot = capture_snapshot
        self._decider = decider
        self._observer = observer
        self._games = games if games is not None else GameService(store)

    async def consider(
        self,
        *,
        game_id: str,
        scene: dict[str, object],
        causation_id: str,
        outcome_source: dict[str, object],
        player_id: str | None,
    ) -> None:
        # Keep the legacy game-existence guard and best-effort boundary unchanged.
        game = self._store.game_state(game_id)
        if game is None:
            return
        try:
            # The store owns the reserve-decision: prefix. Never reconstruct context on replay.
            canonical = self._store.reserve_recovery_decision(causation_id)
            if canonical is None:
                if self._decider is None:
                    return
                snapshot = self._capture_snapshot(
                    game=game,
                    scene=scene,
                    outcome_source=outcome_source,
                    player_id=player_id,
                )
                assessment = await self._decider.assess(snapshot)
                checkpoint = (
                    self._store.checkpoint_reserve_recovery_decision
                    if self._observer is None
                    else self._store.checkpoint_reserve_recovery_decision_with_status
                )
                result = checkpoint(
                    game_id=game_id,
                    causation_id=causation_id,
                    safe_rest_completed=(
                        assessment.safe_rest.verdict is ReserveRecoveryVerdict.ALLOW
                        and game.reserve_recovery_mode.allows_safe_rest
                    ),
                    # Historical behavior: retain this prose even when the mode filters out rest.
                    safe_rest_reason=assessment.safe_rest.display_detail,
                    awards=(
                        tuple(
                            (award.player_id, award.display_detail)
                            for award in assessment.roleplay
                            if award.verdict is ReserveRecoveryVerdict.ALLOW
                        )
                        if game.reserve_recovery_mode.allows_roleplay_award
                        else ()
                    ),
                )
                if self._observer is None:
                    canonical = result
                else:
                    canonical, created = result
                    if created:
                        # Observation is deliberately lossy after commit, never replayed.
                        try:
                            await self._observer.observe(snapshot, _canonical_assessment(canonical))
                        except Exception:
                            logger.warning("reserve recovery shadow observation failed")
            if bool(canonical["safe_rest_completed"]):
                self._games.restore_reserve_for_safe_rest(
                    game_id=game_id,
                    reason=str(canonical["safe_rest_reason"] or "completed safe rest"),
                    causation_id=f"reserve-rest:{causation_id}",
                )
            for award in canonical["awards"]:
                award_payload = dict(award)
                award_player_id = str(award_payload["player_id"])
                self._games.award_reserve_die(
                    game_id=game_id,
                    player_id=award_player_id,
                    reason=str(award_payload["reason"]),
                    causation_id=f"reserve-award:{causation_id}:{award_player_id}",
                )
        except Exception:
            logger.exception("Reserve-recovery adjudication failed for %s", causation_id)


def _canonical_assessment(payload: dict[str, object]) -> ReserveRecoveryAssessment:
    completed = bool(payload["safe_rest_completed"])
    return ReserveRecoveryAssessment(
        safe_rest=SafeRestEligibility(
            verdict=ReserveRecoveryVerdict.ALLOW if completed else ReserveRecoveryVerdict.DENY,
            reason=ReserveRecoveryReason.SAFE_REST_COMPLETED
            if completed
            else ReserveRecoveryReason.NO_COMPLETED_SAFE_REST,
            display_detail=payload["safe_rest_reason"],
        ),
        roleplay=tuple(
            RoleplayEligibility(
                player_id=str(award["player_id"]),
                verdict=ReserveRecoveryVerdict.ALLOW,
                reason=ReserveRecoveryReason.OBSERVABLE_ROLEPLAY,
                display_detail=str(award["reason"]),
            )
            for award in payload["awards"]
        ),
    )
