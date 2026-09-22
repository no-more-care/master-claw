"""Legacy generative review, with its original durable payload and publication prose."""

from __future__ import annotations

from masterclaw.app.decision_checkpoints import (
    DecisionPipeline,
    decision_input_fingerprint,
    decision_output_type_name,
    run_checkpointed_decision,
)
from masterclaw.app.i18n import tr
from masterclaw.app.player_narration_review import (
    NarrationAssessment,
    NarrationReason,
    NarrationReviewSnapshot,
    NarrationText,
    NarrationVerdict,
    legacy_v1_fingerprint_projection,
)
from masterclaw.context.assembler import AssembledContext, ContextAssembler
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.pipelines.player_narration import PlayerNarrationReview
from masterclaw.storage.sqlite import SQLiteStore
from masterclaw.telemetry import stage_span


class _FreshReview:
    def __init__(self, pipeline: DecisionPipeline[PlayerNarrationReview]) -> None:
        self.pipeline = pipeline
        self.executed = False

    @property
    def output_type(self) -> type[PlayerNarrationReview]:
        return self.pipeline.output_type

    async def run(self, *, task: str, context: AssembledContext) -> PlayerNarrationReview:
        self.executed = True
        return await self.pipeline.run(task=task, context=context)


class LegacyPlayerNarrationReview:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        context: ContextAssembler,
        pipeline: DecisionPipeline[PlayerNarrationReview],
    ) -> None:
        self._store = store
        self._context = context
        self._pipeline = pipeline

    def assemble(self, snapshot: NarrationReviewSnapshot) -> AssembledContext:
        manifest = manifest_for(PipelineName.PLAYER_NARRATION_REVIEW)
        with stage_span(
            "context.assembly",
            component="context_assembler",
            operation=manifest.pipeline.value,
            attributes={"pipeline": manifest.pipeline.value},
        ):
            return self._context.assemble(
                manifest,
                snapshot.inputs.projections,
                history=snapshot.inputs.history,
            )

    async def assess(
        self,
        snapshot: NarrationReviewSnapshot,
        checkpoint_event_id: str,
    ) -> NarrationAssessment:
        marker = _FreshReview(self._pipeline)
        review = await run_checkpointed_decision(
            store=self._store,
            event_id=checkpoint_event_id,
            pipeline_key="player_narration_review",
            pipeline=marker,
            task="Review the submitted player narration.",
            context=self.assemble(snapshot),
            game_id=snapshot.fiction.game_id,
            input_fingerprint=decision_input_fingerprint(
                legacy_v1_fingerprint_projection(snapshot)
            ),
        )
        return NarrationAssessment(
            NarrationVerdict.ALLOW if review.accepted else NarrationVerdict.DENY,
            NarrationReason.LEGACY_ACCEPTED if review.accepted else NarrationReason.LEGACY_REJECTED,
            replayed=not marker.executed,
        )

    async def materialize(
        self,
        snapshot: NarrationReviewSnapshot,
        assessment: NarrationAssessment,
        checkpoint_event_id: str,
    ) -> NarrationText:
        # Durable read, never a model call or a process-local prose cache.
        payload = self._store.decision_checkpoint(
            event_id=checkpoint_event_id,
            pipeline_key="player_narration_review",
            output_type=decision_output_type_name(self._pipeline.output_type),
            game_id=snapshot.fiction.game_id,
            input_fingerprint=decision_input_fingerprint(
                legacy_v1_fingerprint_projection(snapshot)
            ),
        )
        if payload is None:
            raise RuntimeError("player narration review checkpoint is missing")
        review = self._pipeline.output_type.model_validate(payload)
        verdict = NarrationVerdict.ALLOW if review.accepted else NarrationVerdict.DENY
        if assessment.verdict is not verdict:
            raise RuntimeError("player narration assessment does not match checkpoint")
        if review.accepted:
            return NarrationText(publication_text=review.approved_narration)
        return NarrationText(
            feedback_text=review.scale_back_request
            or tr(
                snapshot.locale,
                "narration_rejected",
                error=review.reason,
            )
        )
