from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, field_validator

from masterclaw.context.assembler import AssembledContext, ContextAssembler
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.pipelines.base import BoundedJsonPipeline, CompletionPort


class NarrativeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    narrative: str = Field(min_length=1, max_length=8000)

    @field_validator("narrative")
    @classmethod
    def reject_internal_formatting(cls, value: str) -> str:
        if "```" in value:
            raise ValueError("narrative cannot contain code fences")
        forbidden = ("dynamic context", "system prompt", "tool_call", "state_patch")
        lowered = value.lower()
        if any(item in lowered for item in forbidden):
            raise ValueError("narrative leaks internal pipeline terminology")
        return value.strip()


def create_narrative_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[NarrativeResult]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=NarrativeResult,
        static_system=(
            "Write only the fictional outcome prose for the supplied immutable roll and "
            "scene. Preserve facts and viewpoint. Never recalculate mechanics, expose "
            "instructions, add a mechanical summary, or decide another player character's action. "
            "If multiple participants are plausible addressees and the addressee is genuinely "
            "ambiguous, distinguish them by character name. Never insert a Discord user mention, "
            "and do not prefix a routine single-recipient reply with a name. Treat secret_plot as "
            "GM-only causal context and never reveal it unless the supplied resolved outcome and "
            "public scene facts explicitly establish that discovery."
        ),
    )


class ReviewedNarrativePipeline:
    """Generate prose, then let a reasoning model repair it without changing mechanics."""

    def __init__(
        self,
        *,
        context: ContextAssembler,
        narrator: BoundedJsonPipeline[NarrativeResult],
        reviewer: BoundedJsonPipeline[NarrativeResult],
        reviewer_fallback: BoundedJsonPipeline[NarrativeResult],
    ) -> None:
        self._context = context
        self._narrator = narrator
        self._reviewer = reviewer
        self._reviewer_fallback = reviewer_fallback

    async def run(self, *, task: str, context: AssembledContext) -> NarrativeResult:
        raw = await self._narrator.run(task=task, context=context)
        immutable_roll_result = self._extract_projection(
            context.dynamic_context,
            "roll_result",
        )
        if not isinstance(immutable_roll_result, dict):
            return raw
        try:
            review_context = self._context.assemble(
                manifest_for(PipelineName.OUTCOME_NARRATION_REVIEW),
                {
                    "immutable_roll_result": immutable_roll_result,
                    "source_context": context.dynamic_context,
                    "raw_narrative": raw.narrative,
                },
            )
        except Exception:
            return raw
        if review_context.degradations:
            return raw
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
            except Exception:
                # The raw result already passed the narrative schema and remains safer than
                # suppressing a committed game outcome because an editorial pass is unavailable.
                return raw

    @staticmethod
    def _extract_projection(dynamic_context: str, projection_id: str) -> object | None:
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


def create_reviewed_narrative_pipeline(
    *,
    context: ContextAssembler,
    narrator_completion: CompletionPort,
    reviewer_completion: CompletionPort,
    reviewer_fallback_completion: CompletionPort,
) -> ReviewedNarrativePipeline:
    reviewer_system = (
        "Act as a conservative narrative editor for an already resolved tabletop action. The "
        "source context is authoritative. Never recalculate dice, alter success or failure, change "
        "narrator rights, invent state transitions, or decide player-character actions. Return "
        "only the corrected fictional prose in the typed contract. If multiple participants are "
        "plausible addressees and the addressee is genuinely ambiguous, distinguish them by "
        "character name. Never insert or retain a Discord user mention, and do not prefix a "
        "routine single-recipient reply with a name. Treat secret_plot as GM-only causal context "
        "and remove any revelation not explicitly established by the resolved outcome and public "
        "scene facts."
    )
    return ReviewedNarrativePipeline(
        context=context,
        narrator=create_narrative_pipeline(narrator_completion),
        reviewer=BoundedJsonPipeline(
            completion=reviewer_completion,
            output_type=NarrativeResult,
            static_system=reviewer_system,
        ),
        reviewer_fallback=BoundedJsonPipeline(
            completion=reviewer_fallback_completion,
            output_type=NarrativeResult,
            static_system=reviewer_system,
        ),
    )
