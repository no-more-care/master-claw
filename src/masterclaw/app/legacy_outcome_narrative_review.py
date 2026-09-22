"""Exact legacy narrative review preparation, ordering and failure normalization."""

from __future__ import annotations

import json

from masterclaw.app.decision_checkpoints import DecisionPipeline
from masterclaw.app.outcome_narrative_review import (
    NarrativeReviewAssessment,
    NarrativeReviewReason,
    NarrativeReviewVerdict,
    OutcomeNarrativeSnapshot,
)
from masterclaw.context.assembler import AssembledContext, ContextAssembler
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.pipelines.base import PipelineValidationError
from masterclaw.pipelines.narrative import NarrativeResult


def extract_projection(dynamic_context: str, projection_id: str) -> object | None:
    marker = f"## STATE {projection_id}\n"
    start = dynamic_context.find(marker)
    if start < 0:
        return None
    start += len(marker)
    end = dynamic_context.find("\n\n## ", start)
    payload = dynamic_context[start:] if end < 0 else dynamic_context[start:end]
    try:
        return json.loads(payload)
    except (TypeError, ValueError):
        return None


class LegacyNarrativeDraftGenerator:
    def __init__(self, pipeline: DecisionPipeline[NarrativeResult]) -> None:
        self._pipeline = pipeline

    async def generate(self, *, task: str, context: AssembledContext) -> OutcomeNarrativeSnapshot:
        raw = await self._pipeline.run(task=task, context=context)
        immutable_outcome = extract_projection(context.dynamic_context, "roll_result")
        if not isinstance(immutable_outcome, dict):
            raise PipelineValidationError("narrative review lacks immutable roll context")
        return OutcomeNarrativeSnapshot(
            task=task,
            context=context,
            raw_narrative=raw.narrative,
            immutable_outcome_json=json.dumps(immutable_outcome, ensure_ascii=False),
        )


class LegacyAlwaysReviewDecider:
    async def assess(self, snapshot: OutcomeNarrativeSnapshot) -> NarrativeReviewAssessment:
        return NarrativeReviewAssessment(
            NarrativeReviewVerdict.REPAIR, NarrativeReviewReason.LEGACY_ALWAYS_REVIEW
        )


class LegacyNarrativeTextEditor:
    def __init__(
        self,
        *,
        context: ContextAssembler,
        reviewer: DecisionPipeline[NarrativeResult],
        reviewer_fallback: DecisionPipeline[NarrativeResult],
    ) -> None:
        self._context = context
        self._reviewer = reviewer
        self._reviewer_fallback = reviewer_fallback

    async def edit(
        self, snapshot: OutcomeNarrativeSnapshot, assessment: NarrativeReviewAssessment
    ) -> NarrativeResult:
        try:
            review_context = self._context.assemble(
                manifest_for(PipelineName.OUTCOME_NARRATION_REVIEW),
                {
                    "immutable_roll_result": snapshot.immutable_outcome,
                    "source_context": snapshot.context.dynamic_context,
                    "raw_narrative": snapshot.raw_narrative,
                },
            )
        except Exception as error:
            raise PipelineValidationError("narrative review context is unavailable") from error
        if review_context.degradations:
            raise PipelineValidationError("narrative review context was degraded")
        review_task = (
            "Return publication-ready prose. Preserve every immutable mechanical outcome and "
            "established fact. Fix only contradictions, accidental state invention, viewpoint "
            "violations, internal terminology, and weak or confusing phrasing."
        )
        try:
            return await self._reviewer.run(task=review_task, context=review_context)
        except Exception:
            try:
                return await self._reviewer_fallback.run(task=review_task, context=review_context)
            except Exception as error:
                raise PipelineValidationError("narrative review is unavailable") from error
