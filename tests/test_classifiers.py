import pytest
from pydantic import ValidationError

from masterclaw.classifiers.base import (
    ChoiceAnswer,
    ChoiceQuestion,
    ClassificationRequest,
    ClassificationResponse,
    NoulQuestion,
    ScoreQuestion,
)
from masterclaw.classifiers.policy import (
    ClassifierConfig,
    ClassifierMode,
    ShadowDisposition,
    assess_choice,
)


def request() -> ClassificationRequest:
    return ClassificationRequest(
        taxonomy_version="test.v1",
        state={"message": "hello"},
        questions={"route": ChoiceQuestion(instructions="Route", criteria={"a": "A", "b": "B"})},
    )


def response_for(req, **overrides) -> ClassificationResponse:
    values = dict(
        request_key=req.request_key,
        taxonomy_version=req.taxonomy_version,
        provider="test",
        model="model",
        answers={
            "route": {
                "type": "choice",
                "choice": "a",
                "confidence": 0.98,
                "probabilities": {"a": 0.98, "b": 0.02},
            },
        },
    )
    return ClassificationResponse(**(values | overrides))


def test_request_key_is_canonical_and_taxonomy_sensitive():
    req = request()
    reordered = ClassificationRequest.model_validate(
        {
            "questions": req.model_dump()["questions"],
            "state": req.state,
            "taxonomy_version": req.taxonomy_version,
        }
    )
    assert req.request_key == reordered.request_key
    assert req.request_key != req.model_copy(update={"taxonomy_version": "test.v2"}).request_key


@pytest.mark.parametrize(
    "changes",
    [
        {"questions": {}},
        {"state": float("nan")},
        {"state": {"secret": object()}},
        {"state": "x" * 128_000},
        {"questions": {"route": {"type": "unknown", "instructions": "Route"}}},
    ],
)
def test_invalid_requests_are_rejected(changes):
    with pytest.raises((ValueError, ValidationError)):
        ClassificationRequest.model_validate(request().model_dump() | changes)


@pytest.mark.parametrize(
    "question",
    [
        lambda: ChoiceQuestion(instructions=" ", criteria={"a": "A", "b": "B"}),
        lambda: ChoiceQuestion(instructions="Route", criteria={"a": "A"}),
        lambda: ScoreQuestion(instructions="Risk", criteria=["low", "low"]),
        lambda: NoulQuestion(instructions="Is valid?", criteria={"true": "Yes"}),
    ],
)
def test_question_contracts(question):
    with pytest.raises(ValidationError):
        question()


@pytest.mark.parametrize("probability", [-0.1, 1.1, float("nan"), "0.95", True])
def test_invalid_probabilities(probability):
    with pytest.raises(ValidationError):
        ChoiceAnswer(choice="a", confidence=probability, probabilities={"a": 0.9, "b": 0.1})


@pytest.mark.parametrize(
    "changes",
    [
        {"request_key": "wrong"},
        {"taxonomy_version": "wrong"},
        {
            "answers": {
                "unknown": {
                    "type": "choice",
                    "choice": "a",
                    "confidence": 0.9,
                    "probabilities": {"a": 0.9, "b": 0.1},
                }
            }
        },
        {
            "answers": {
                "route": {
                    "type": "choice",
                    "choice": "a",
                    "confidence": 0.9,
                    "probabilities": {"a": 0.9, "alien": 0.1},
                }
            }
        },
        {
            "answers": {
                "route": {
                    "type": "choice",
                    "choice": "a",
                    "confidence": 0.9,
                    "probabilities": {"a": 0.9, "b": 0.9},
                }
            }
        },
        {
            "answers": {
                "route": {
                    "type": "choice",
                    "choice": "b",
                    "confidence": 0.9,
                    "probabilities": {"a": 0.9, "b": 0.1},
                }
            }
        },
    ],
)
def test_response_must_match_request(changes):
    req = request()
    with pytest.raises(ValueError):
        response_for(req, **changes).validate_for(req)


def test_score_and_noul_preserve_full_distributions():
    req = ClassificationRequest(
        taxonomy_version="rubric.v1",
        state="message",
        questions={
            "risk": ScoreQuestion(instructions="Risk", criteria=["low", "high"]),
            "valid": NoulQuestion(instructions="Is valid?"),
        },
    )
    response = response_for(
        req,
        answers={
            "risk": {
                "type": "score",
                "score": 0.8,
                "confidence": 0.7,
                "legend": {"0": "low", "1": "high"},
                "probabilities": {"0": 0.2, "1": 0.8},
            },
            "valid": {"type": "noul", "noul": 0.5, "probabilities": {"true": 0.5, "false": 0.5}},
        },
    )
    assert response.validate_for(req) is response
    bad = response.model_dump()
    bad["answers"]["risk"]["legend"] = {"0": "high", "1": "low"}
    with pytest.raises(ValueError, match="legend"):
        ClassificationResponse.model_validate(bad).validate_for(req)


@pytest.mark.parametrize(
    ("confidence", "p", "blocked", "expected"),
    [
        (0.95, 0.95, set(), ShadowDisposition.ELIGIBLE),
        (0.949, 0.99, set(), ShadowDisposition.UNCERTAIN),
        (0.99, 0.949, set(), ShadowDisposition.UNCERTAIN),
        (1.0, 1.0, {"a"}, ShadowDisposition.BLOCKED),
    ],
)
def test_shadow_threshold_and_hard_policy(confidence, p, blocked, expected):
    answer = ChoiceAnswer(choice="a", confidence=confidence, probabilities={"a": p, "b": 1 - p})
    assert assess_choice(answer, allowed={"a", "b"}, blocked=blocked, threshold=0.95) == expected


def test_configuration_is_off_by_default_and_disallows_authoritative_mode():
    assert ClassifierConfig().mode is ClassifierMode.OFF
    with pytest.raises(ValidationError):
        ClassifierConfig(mode="production")
    with pytest.raises(ValidationError):
        ClassifierConfig(provider="unknown")
