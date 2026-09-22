import asyncio
import json

import pytest

from masterclaw.classifiers.base import (
    ChoiceQuestion,
    ClassificationRequest,
    ClassificationResponse,
    ClassifierErrorCategory,
    NoulQuestion,
    ScoreQuestion,
)
from masterclaw.classifiers.executor import SemanticClassifierExecutor, SemanticEvaluationContext
from masterclaw.classifiers.policy import SemanticEvaluationPolicy
from masterclaw.telemetry import bind_trace, reset_trace


def request():
    return ClassificationRequest(
        taxonomy_version="generic.v1",
        state={"text": "private message"},
        questions={
            "route": ChoiceQuestion(instructions="Route", criteria={"a": "A", "b": "B"}),
            "valid": NoulQuestion(instructions="Is this valid?"),
            "risk": ScoreQuestion(instructions="Risk", criteria=["private low", "private high"]),
        },
    )


class Classifier:
    def __init__(self, noul=0.99):
        self.calls = 0
        self.noul = noul

    async def classify(self, req):
        self.calls += 1
        return ClassificationResponse(
            request_key=req.request_key,
            taxonomy_version=req.taxonomy_version,
            provider="test",
            model="resolved-1",
            version="1",
            request_id="req-123",
            answers={
                "route": {
                    "type": "choice",
                    "choice": "a",
                    "confidence": 0.99,
                    "probabilities": {"a": 0.99, "b": 0.01},
                },
                "valid": {
                    "type": "noul",
                    "noul": self.noul,
                    "probabilities": {"true": self.noul, "false": 1 - self.noul},
                },
                "risk": {
                    "type": "score",
                    "score": 0.99,
                    "confidence": 0.99,
                    "legend": {"0": "private low", "1": "private high"},
                    "probabilities": {"0": 0.01, "1": 0.99},
                },
            },
        )


def evaluate(classifier=None, policy=None, context=None):
    return asyncio.run(
        SemanticClassifierExecutor(
            classifier or Classifier(),
            requested_model="alias-latest",
        ).evaluate(
            request(),
            policy=policy or SemanticEvaluationPolicy(mode="shadow"),
            context=context or SemanticEvaluationContext(use_case="advancement"),
        )
    )


def test_generic_executor_returns_all_typed_primitives_without_state_baseline():
    result = evaluate()
    assert result.observation.use_case == "advancement"
    assert result.observation.outcome == "eligible"
    assert result.observation.reference == {}
    assert result.observation.agreement is None
    assert result.observation.answers["route"].choice == "a"
    assert result.observation.answers["valid"].noul == 0.99
    assert result.observation.answers["valid"].confidence is None
    assert result.observation.answers["risk"].score == 0.99
    assert result.response.answers["risk"].legend == {"0": "private low", "1": "private high"}
    assert "private" not in result.observation.model_dump_json()
    assert "baseline_command" not in result.observation.model_dump()


@pytest.mark.parametrize(("noul", "outcome"), [(0.5, "uncertain"), (0.01, "eligible")])
def test_noul_uncertainty_is_not_provider_failure(noul, outcome):
    result = evaluate(Classifier(noul=noul))
    assert result.observation.outcome == outcome
    assert result.observation.error_category is None
    assert result.response is not None


def test_choice_blocking_and_typed_reference_comparison():
    result = evaluate(
        policy=SemanticEvaluationPolicy(mode="shadow", blocked_choices={"route": {"a"}}),
        context=SemanticEvaluationContext(
            use_case="action",
            reference={"route": "a", "valid": True, "risk": 0.99},
        ),
    )
    assert result.observation.outcome == "blocked"
    assert result.observation.answers["route"].outcome == "blocked"
    assert result.observation.agreement is True
    assert (
        evaluate(
            context=SemanticEvaluationContext(
                use_case="action",
                reference={"route": "b"},
            )
        ).observation.agreement
        is False
    )


def test_invalid_reference_is_sanitized_before_provider_or_telemetry():
    classifier = Classifier()
    result = evaluate(
        classifier,
        context=SemanticEvaluationContext(
            use_case="action",
            reference={"route": "private-unlisted-label"},
        ),
    )
    assert classifier.calls == 0
    assert result.response is None
    assert result.observation.error_category is ClassifierErrorCategory.REQUEST
    assert "private" not in result.observation.model_dump_json()


def test_off_does_not_invoke_provider():
    classifier = Classifier()
    result = evaluate(classifier, policy=SemanticEvaluationPolicy(mode="off"))
    assert classifier.calls == 0
    assert result.observation.outcome == "off"


def test_timeout_is_generic_and_cancellation_propagates():
    class Slow:
        async def classify(self, req):
            await asyncio.sleep(60)

    result = evaluate(Slow(), policy=SemanticEvaluationPolicy(mode="shadow", timeout_seconds=0.001))
    assert result.observation.error_category is ClassifierErrorCategory.TIMEOUT
    assert result.observation.error_transient is True

    class Cancelled:
        async def classify(self, req):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        evaluate(Cancelled())


def test_generic_span_contains_safe_typed_answers_not_state_or_rubric_text():
    class Sink:
        records = []

        def record_stage_spans(self, records):
            self.records.extend(records)

    sink = Sink()
    binding = bind_trace(sink, trace_id="generic")
    try:
        evaluate()
    finally:
        reset_trace(binding)
    assert len(sink.records) == 1
    record = sink.records[0]
    assert record["stage"] == "classifier.advancement"
    assert set(record["attributes"]["answers"]) == {"route", "valid", "risk"}
    assert "private" not in json.dumps(record)
