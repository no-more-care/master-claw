"""Publication review contracts: semantic assessment never contains replacement prose."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from masterclaw.context.assembler import AssembledContext
from masterclaw.pipelines.narrative import NarrativeResult

logger = logging.getLogger(__name__)


class NarrativeReviewVerdict(StrEnum):
    PUBLISH = "publish"
    REPAIR = "repair"
    UNCERTAIN = "uncertain"


class NarrativeReviewReason(StrEnum):
    LEGACY_ALWAYS_REVIEW = "legacy_always_review"
    COMPLIANT = "compliant"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True, slots=True)
class OutcomeNarrativeSnapshot:
    task: str
    context: AssembledContext
    raw_narrative: str
    immutable_outcome_json: str

    @property
    def immutable_outcome(self) -> dict[str, object]:
        return json.loads(self.immutable_outcome_json)


@dataclass(frozen=True, slots=True)
class NarrativeReviewAssessment:
    verdict: NarrativeReviewVerdict
    reason: NarrativeReviewReason


class NarrativeDraftGenerator(Protocol):
    async def generate(
        self, *, task: str, context: AssembledContext
    ) -> OutcomeNarrativeSnapshot: ...


class NarrativeSemanticDecider(Protocol):
    async def assess(self, snapshot: OutcomeNarrativeSnapshot) -> NarrativeReviewAssessment: ...


class NarrativeTextEditor(Protocol):
    async def edit(
        self, snapshot: OutcomeNarrativeSnapshot, assessment: NarrativeReviewAssessment
    ) -> NarrativeResult: ...


class OutcomeNarrativeObserver(Protocol):
    async def observe(self, snapshot: OutcomeNarrativeSnapshot) -> object: ...


class OutcomeNarrativePipeline:
    """Outer-checkpoint-compatible facade; every draft still passes through the text editor."""

    def __init__(
        self,
        *,
        draft: NarrativeDraftGenerator,
        decider: NarrativeSemanticDecider,
        editor: NarrativeTextEditor,
        observer: OutcomeNarrativeObserver | None = None,
    ) -> None:
        self._draft = draft
        self._decider = decider
        self._editor = editor
        self._observer = observer

    @property
    def output_type(self) -> type[NarrativeResult]:
        return NarrativeResult

    async def run(self, *, task: str, context: AssembledContext) -> NarrativeResult:
        snapshot = await self._draft.generate(task=task, context=context)
        assessment = await self._decider.assess(snapshot)
        # No publish shortcut in D1: the raw draft can never become the returned result.
        result = await self._editor.edit(snapshot, assessment)
        if self._observer is not None:
            try:
                await self._observer.observe(snapshot)
            except Exception:
                logger.warning("outcome_narrative_shadow_failed category=internal")
        return result
