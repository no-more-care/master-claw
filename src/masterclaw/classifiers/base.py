from __future__ import annotations

import hashlib
import json
import math
from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False, strict=True)]
MAX_REQUEST_BYTES = 128_000
MAX_RESPONSE_BYTES = 256_000


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChoiceQuestion(Contract):
    type: Literal["choice"] = "choice"
    instructions: Text
    criteria: dict[Identifier, Text] = Field(min_length=2, max_length=64)


class ScoreQuestion(Contract):
    type: Literal["score"] = "score"
    instructions: Text
    criteria: list[Text] = Field(min_length=2, max_length=32)

    @model_validator(mode="after")
    def unique_levels(self) -> ScoreQuestion:
        if len(set(self.criteria)) != len(self.criteria):
            raise ValueError("score levels must be distinct")
        return self


class NoulQuestion(Contract):
    type: Literal["noul"] = "noul"
    instructions: Text
    criteria: dict[Literal["true", "false"], Text] | None = None

    @model_validator(mode="after")
    def both_outcomes(self) -> NoulQuestion:
        if self.criteria is not None and set(self.criteria) != {"true", "false"}:
            raise ValueError("noul criteria must describe both outcomes")
        return self


Question = Annotated[ChoiceQuestion | ScoreQuestion | NoulQuestion, Field(discriminator="type")]


class ClassificationRequest(Contract):
    taxonomy_version: Identifier
    state: JsonValue
    questions: dict[Identifier, Question] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def bounded_json(self) -> ClassificationRequest:
        if not isinstance(self.state, (str, dict, list)):
            raise ValueError("state must be text, an object, or an array")
        if len(self.canonical_bytes()) > MAX_REQUEST_BYTES:
            raise ValueError("classification request exceeds size limit")
        return self

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @property
    def request_key(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


class ChoiceAnswer(Contract):
    type: Literal["choice"] = "choice"
    choice: Identifier
    probabilities: dict[Identifier, Probability] = Field(min_length=2, max_length=64)
    confidence: Probability


class ScoreAnswer(Contract):
    type: Literal["score"] = "score"
    score: float = Field(allow_inf_nan=False, strict=True)
    legend: dict[str, Text] = Field(min_length=2, max_length=32)
    probabilities: dict[str, Probability] = Field(min_length=2, max_length=32)
    confidence: Probability


class NoulAnswer(Contract):
    type: Literal["noul"] = "noul"
    noul: Probability
    probabilities: dict[Literal["true", "false"], Probability]
    confidence: Probability | None = None


Answer = Annotated[ChoiceAnswer | ScoreAnswer | NoulAnswer, Field(discriminator="type")]


class ClassificationResponse(Contract):
    request_key: Identifier
    taxonomy_version: Identifier
    answers: dict[Identifier, Answer] = Field(min_length=1, max_length=32)
    provider: Identifier
    upstream_provider: Text | None = None
    model: Text
    version: Text | None = None
    request_id: Text | None = None
    usage: dict[str, JsonValue] = Field(default_factory=dict)
    cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    def validate_for(self, request: ClassificationRequest) -> ClassificationResponse:
        if (
            self.request_key != request.request_key
            or self.taxonomy_version != request.taxonomy_version
        ):
            raise ValueError("classification request identity mismatch")
        if set(self.answers) != set(request.questions):
            raise ValueError("classification question IDs mismatch")
        for key, question in request.questions.items():
            answer = self.answers[key]
            if answer.type != question.type:
                raise ValueError("classification answer type mismatch")
            probabilities = answer.probabilities
            if not math.isclose(sum(probabilities.values()), 1.0, abs_tol=0.01):
                raise ValueError("classification distribution must sum to one")
            if isinstance(question, ChoiceQuestion) and isinstance(answer, ChoiceAnswer):
                if (
                    set(probabilities) != set(question.criteria)
                    or answer.choice not in probabilities
                ):
                    raise ValueError("classification choice labels mismatch")
                if probabilities[answer.choice] < max(probabilities.values()) - 1e-6:
                    raise ValueError("classification choice is not a distribution maximum")
            elif isinstance(question, ScoreQuestion) and isinstance(answer, ScoreAnswer):
                if set(probabilities) != set(answer.legend):
                    raise ValueError("classification score legend mismatch")
                try:
                    ordered = sorted(answer.legend, key=float)
                    positions = [float(level) for level in ordered]
                except ValueError:
                    raise ValueError(
                        "classification score legend must have numeric levels"
                    ) from None
                if (
                    not all(math.isfinite(level) for level in positions)
                    or len(set(positions)) != len(positions)
                    or [answer.legend[level] for level in ordered] != question.criteria
                    or not positions[0] <= answer.score <= positions[-1]
                ):
                    raise ValueError("classification score legend or range mismatch")
                expected = sum(float(level) * probabilities[level] for level in ordered)
                if not math.isclose(answer.score, expected, abs_tol=0.02):
                    raise ValueError("classification score disagrees with distribution")
            elif isinstance(answer, NoulAnswer):
                if set(probabilities) != {"true", "false"} or not math.isclose(
                    probabilities["true"], answer.noul, abs_tol=1e-6
                ):
                    raise ValueError("classification noul distribution mismatch")
        return self


class ClassifierErrorCategory(StrEnum):
    CONFIGURATION = "configuration"
    AUTHENTICATION = "authentication"
    REQUEST = "request"
    RESPONSE = "response"
    RATE_LIMIT = "rate_limit"
    SERVER = "server"
    TIMEOUT = "timeout"
    NETWORK = "network"
    INTERNAL = "internal"


class ClassifierError(RuntimeError):
    """Sanitized provider failure; never contains state or HTTP response bodies."""

    category = ClassifierErrorCategory.INTERNAL
    transient = False


class ClassifierConfigurationError(ClassifierError):
    category = ClassifierErrorCategory.CONFIGURATION


class ClassifierAuthenticationError(ClassifierError):
    category = ClassifierErrorCategory.AUTHENTICATION


class ClassifierRequestError(ClassifierError):
    category = ClassifierErrorCategory.REQUEST


class ClassifierResponseError(ClassifierError):
    category = ClassifierErrorCategory.RESPONSE


class TransientClassifierError(ClassifierError):
    transient = True


class ClassifierRateLimitError(TransientClassifierError):
    category = ClassifierErrorCategory.RATE_LIMIT


class ClassifierServerError(TransientClassifierError):
    category = ClassifierErrorCategory.SERVER


class ClassifierTimeoutError(TransientClassifierError):
    category = ClassifierErrorCategory.TIMEOUT


class ClassifierNetworkError(TransientClassifierError):
    category = ClassifierErrorCategory.NETWORK


class ClassifierPort(Protocol):
    async def classify(self, request: ClassificationRequest) -> ClassificationResponse: ...
