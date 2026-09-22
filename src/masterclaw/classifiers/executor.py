from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable
from typing import Literal

from pydantic import Field

from masterclaw.classifiers.base import (
    ChoiceAnswer,
    ChoiceQuestion,
    ClassificationRequest,
    ClassificationResponse,
    ClassifierError,
    ClassifierErrorCategory,
    ClassifierPort,
    ClassifierRequestError,
    ClassifierResponseError,
    Contract,
    Identifier,
    NoulAnswer,
    NoulQuestion,
    Probability,
    ScoreAnswer,
)
from masterclaw.classifiers.observations import (
    AnswerObservation,
    ClassifierObservation,
    ReferenceValue,
    metadata_identifier,
    numeric_usage,
)
from masterclaw.classifiers.policy import (
    ClassifierMode,
    SemanticEvaluationPolicy,
    ShadowDisposition,
    assess_choice,
)
from masterclaw.telemetry import stage_span

logger = logging.getLogger(__name__)


class SemanticEvaluationContext(Contract):
    """Only stable application labels and typed reference answers, never request state."""

    use_case: Identifier
    scope: Identifier | None = None
    reference: dict[Identifier, ReferenceValue] = Field(default_factory=dict)
    decision_reference: Identifier | None = None


class SemanticDecisionSummary(Contract):
    """Optional use-case reduction, recorded alongside the unchanged raw distributions."""

    decision: Identifier
    decision_reason: Identifier
    decision_comparison: Identifier | None = None
    outcome: Literal["eligible", "uncertain"]
    decision_thresholds: dict[Identifier, Probability] = Field(default_factory=dict)


class SemanticEvaluation(Contract):
    response: ClassificationResponse | None = None
    observation: ClassifierObservation


class SemanticClassifierExecutor:
    """Provider-neutral invocation and shadow evaluation for every decision primitive."""

    def __init__(self, port: ClassifierPort, *, requested_model: str) -> None:
        self._port = port
        self._requested_model = requested_model

    async def evaluate(
        self,
        request: ClassificationRequest,
        *,
        policy: SemanticEvaluationPolicy,
        context: SemanticEvaluationContext,
        reducer: Callable[[ClassificationResponse], SemanticDecisionSummary] | None = None,
    ) -> SemanticEvaluation:
        started = time.perf_counter()
        attributes: dict[str, object] = {
            "use_case": context.use_case,
            "mode": policy.mode,
            "scope": context.scope,
            "taxonomy_version": request.taxonomy_version,
            "requested_model": metadata_identifier(self._requested_model),
        }
        if policy.mode is ClassifierMode.OFF:
            return SemanticEvaluation(
                observation=ClassifierObservation(
                    **attributes,
                    outcome="off",
                    latency_ms=0,
                )
            )
        response = None
        with stage_span(
            f"classifier.{context.use_case}", component="classifier", attributes=attributes
        ):
            try:
                try:
                    request = ClassificationRequest.model_validate(request.model_dump(mode="json"))
                    self._validate_policy_context(request, policy, context)
                except ValueError:
                    raise ClassifierRequestError("invalid semantic evaluation request") from None
                attributes.update(request_key=request.request_key, reference=context.reference)
                async with asyncio.timeout(policy.timeout_seconds):
                    response = await self._port.classify(request)
                    try:
                        response = ClassificationResponse.model_validate(
                            response.model_dump(mode="json")
                        ).validate_for(request)
                    except ValueError:
                        raise ClassifierResponseError(
                            "invalid semantic evaluation response"
                        ) from None
                answers = self._assess_answers(response, request, policy)
                outcomes = {answer.outcome for answer in answers.values()}
                outcome = (
                    "blocked"
                    if "blocked" in outcomes
                    else "uncertain"
                    if "uncertain" in outcomes
                    else "eligible"
                )
                agreement = (
                    all(
                        self._agrees(response.answers[key], reference)
                        for key, reference in context.reference.items()
                    )
                    if context.reference
                    else None
                )
                attributes.update(
                    outcome=outcome,
                    answers=answers,
                    agreement=agreement,
                    resolved_model=metadata_identifier(response.model),
                    provider=metadata_identifier(response.provider),
                    upstream_provider=metadata_identifier(response.upstream_provider),
                    version=metadata_identifier(response.version),
                    request_id=metadata_identifier(response.request_id),
                    usage=numeric_usage(response.usage),
                    cost=response.cost,
                )
                if reducer is not None:
                    summary = reducer(response)
                    attributes.update(summary.model_dump(mode="json"))
                    attributes.update(
                        decision_reference=context.decision_reference,
                        agreement=(
                            (summary.decision_comparison or summary.decision)
                            == context.decision_reference
                            if context.decision_reference is not None
                            else None
                        ),
                    )
            except Exception as error:
                response = None
                category, transient = ClassifierErrorCategory.INTERNAL, False
                if isinstance(error, ClassifierError):
                    category, transient = error.category, error.transient
                elif isinstance(error, TimeoutError):
                    category, transient = ClassifierErrorCategory.TIMEOUT, True
                attributes.update(
                    outcome="error", error_category=category, error_transient=transient
                )
                logger.warning(
                    "classifier_failed use_case=%s category=%s", context.use_case, category.value
                )
            attributes["latency_ms"] = (time.perf_counter() - started) * 1000
            observation = ClassifierObservation.model_validate(attributes)
            attributes.update(observation.model_dump(mode="json"))
        return SemanticEvaluation(response=response, observation=observation)

    @staticmethod
    def _validate_policy_context(
        request: ClassificationRequest,
        policy: SemanticEvaluationPolicy,
        context: SemanticEvaluationContext,
    ) -> None:
        if not set(context.reference) <= request.questions.keys():
            raise ValueError("reference question IDs mismatch")
        for key, reference in context.reference.items():
            question = request.questions[key]
            if isinstance(question, ChoiceQuestion):
                if not isinstance(reference, str) or reference not in question.criteria:
                    raise ValueError("reference choice mismatch")
            elif isinstance(question, NoulQuestion):
                if not isinstance(reference, bool):
                    raise ValueError("reference noul must be boolean")
            elif isinstance(reference, bool) or not isinstance(reference, (int, float)):
                raise ValueError("reference score must be numeric")
        for key, blocked in policy.blocked_choices.items():
            question = request.questions.get(key)
            if not isinstance(question, ChoiceQuestion) or not blocked <= question.criteria.keys():
                raise ValueError("blocked choice labels mismatch")

    @staticmethod
    def _assess_answers(
        response: ClassificationResponse,
        request: ClassificationRequest,
        policy: SemanticEvaluationPolicy,
    ) -> dict[str, AnswerObservation]:
        observations = {}
        for key, answer in response.answers.items():
            values = {
                "type": answer.type,
                "confidence": answer.confidence,
                "probabilities": answer.probabilities,
            }
            if isinstance(answer, ChoiceAnswer):
                disposition = assess_choice(
                    answer,
                    allowed=set(request.questions[key].criteria),
                    blocked=set(policy.blocked_choices.get(key, ())),
                    threshold=policy.threshold,
                )
                values["choice"] = answer.choice
            elif isinstance(answer, NoulAnswer):
                # Noul is a yes-probability, not confidence. Either decisive polarity is useful.
                disposition = (
                    ShadowDisposition.ELIGIBLE
                    if max(answer.noul, 1 - answer.noul) >= policy.threshold
                    else ShadowDisposition.UNCERTAIN
                )
                values["noul"] = answer.noul
            else:
                disposition = (
                    ShadowDisposition.ELIGIBLE
                    if answer.confidence >= policy.threshold
                    else ShadowDisposition.UNCERTAIN
                )
                values["score"] = answer.score
            observations[key] = AnswerObservation(**values, outcome=disposition.value)
        return observations

    @staticmethod
    def _agrees(answer: ChoiceAnswer | NoulAnswer | ScoreAnswer, reference: ReferenceValue) -> bool:
        if isinstance(answer, ChoiceAnswer):
            return answer.choice == reference
        if isinstance(answer, NoulAnswer):
            return (answer.noul >= 0.5) == reference
        return math.isclose(answer.score, float(reference), abs_tol=1e-6)
