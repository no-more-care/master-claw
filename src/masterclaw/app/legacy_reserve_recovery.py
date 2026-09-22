"""Legacy reserve projections/assembly and generative review, separate from application."""

from __future__ import annotations

from masterclaw.app.context_inputs import ContextInputCapture
from masterclaw.app.decision_checkpoints import DecisionPipeline
from masterclaw.app.fiction_context import actor_character_projection
from masterclaw.app.reserve_recovery import (
    ReserveRecoveryAssessment,
    ReserveRecoveryReason,
    ReserveRecoverySnapshot,
    ReserveRecoveryVerdict,
    RoleplayEligibility,
    SafeRestEligibility,
)
from masterclaw.context.assembler import ContextAssembler
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.state import GameState
from masterclaw.pipelines.reserve_recovery import ReserveRecoveryDecision
from masterclaw.storage.sqlite import SQLiteStore
from masterclaw.telemetry import stage_span


class LegacyReserveRecoverySnapshotCapture:
    def __init__(self, store: SQLiteStore, capture_context: ContextInputCapture) -> None:
        self._store = store
        self._capture_context = capture_context

    def __call__(
        self,
        *,
        game: GameState,
        scene: dict[str, object],
        outcome_source: dict[str, object],
        player_id: str | None,
    ) -> ReserveRecoverySnapshot:
        current_scene = (
            self._store.scene_projection(game_id=game.game_id, player_id=player_id)
            if player_id is not None
            else None
        ) or scene
        character = (
            self._store.character_for_player(game_id=game.game_id, player_id=player_id)
            if player_id is not None
            else None
        )
        inputs = self._capture_context(
            manifest_for(PipelineName.RESERVE_RECOVERY),
            {
                "current_scene": current_scene,
                "outcome_source": outcome_source,
                "reserve_policy": game.reserve_recovery_mode.value,
                "actor_character": (
                    None
                    if character is None
                    else actor_character_projection(character, player_id=player_id)
                ),
                "characters": self._store.reserve_projection(game.game_id),
            },
            game_id=game.game_id,
            player_id=player_id,
        )
        return ReserveRecoverySnapshot(game.game_id, player_id, game.reserve_recovery_mode, inputs)


class LegacyReserveRecoveryDecider:
    def __init__(
        self,
        *,
        context: ContextAssembler,
        pipeline: DecisionPipeline[ReserveRecoveryDecision],
    ) -> None:
        self._context = context
        self._pipeline = pipeline

    async def assess(self, snapshot: ReserveRecoverySnapshot) -> ReserveRecoveryAssessment:
        manifest = manifest_for(PipelineName.RESERVE_RECOVERY)
        with stage_span(
            "context.assembly",
            component="context_assembler",
            operation=manifest.pipeline.value,
            attributes={"pipeline": manifest.pipeline.value},
        ):
            context = self._context.assemble(
                manifest,
                snapshot.inputs.projections,
                history=snapshot.inputs.history,
            )
        decision = await self._pipeline.run(
            task="Decide whether this resolved outcome earns reserve recovery.",
            context=context,
        )
        return ReserveRecoveryAssessment(
            safe_rest=SafeRestEligibility(
                ReserveRecoveryVerdict.ALLOW
                if decision.safe_rest_completed
                else ReserveRecoveryVerdict.DENY,
                ReserveRecoveryReason.SAFE_REST_COMPLETED
                if decision.safe_rest_completed
                else ReserveRecoveryReason.NO_COMPLETED_SAFE_REST,
                decision.safe_rest_reason,
            ),
            roleplay=tuple(
                RoleplayEligibility(
                    award.player_id,
                    ReserveRecoveryVerdict.ALLOW,
                    ReserveRecoveryReason.OBSERVABLE_ROLEPLAY,
                    award.reason,
                )
                for award in decision.awards
            ),
        )
