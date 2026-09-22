from __future__ import annotations

import math
import re
from enum import StrEnum
from typing import Literal

from pydantic import Field, FiniteFloat, JsonValue, StrictBool

from masterclaw.classifiers.base import (
    ClassifierErrorCategory,
    Contract,
    Identifier,
    Probability,
)
from masterclaw.classifiers.policy import ClassifierMode

_METADATA = re.compile(r"^[A-Za-z0-9~][A-Za-z0-9._:/@+\-]{0,199}$")
_USAGE_FIELDS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
        "cached_tokens",
        "reasoning_tokens",
        "cost",
        "cost_details",
        "input_tokens_details",
        "output_tokens_details",
        "prompt_tokens_details",
        "completion_tokens_details",
        "upstream_inference_cost",
        "upstream_inference_input_cost",
        "upstream_inference_output_cost",
    }
)


def metadata_identifier(value: str | None) -> str | None:
    """Only bounded provider identifiers, never free-form provider text, enter telemetry."""
    return value if value is not None and _METADATA.fullmatch(value) else None


def numeric_usage(usage: dict[str, JsonValue]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key in _USAGE_FIELDS & usage.keys():
        value = usage[key]
        if isinstance(value, dict):
            nested = numeric_usage(value)
            if nested:
                result[key] = nested
        elif type(value) in {int, float} and math.isfinite(value) and value >= 0:
            result[key] = value
    return result


ReferenceValue = Identifier | StrictBool | FiniteFloat
ReferenceKind = Literal["legacy_heuristic"]


class ClassifierSkipReason(StrEnum):
    PROJECTION_TRUNCATED = "projection_truncated"
    NO_CANDIDATES = "no_candidates"
    PROJECTION_UNAVAILABLE = "projection_unavailable"


class SkippedClassifierObservation(Contract):
    """An explicit non-evaluation: no answers, references, usage or calibration denominator."""

    observation_schema_version: Literal["v1"] = "v1"
    outcome: Literal["skipped"] = "skipped"
    reason: ClassifierSkipReason
    use_case: Identifier
    mode: ClassifierMode
    scope: Identifier | None = None
    taxonomy_version: Identifier
    requested_model: str | None


class AnswerObservation(Contract):
    type: Literal["choice", "score", "noul"]
    outcome: Literal["eligible", "uncertain", "blocked"]
    choice: Identifier | None = None
    score: FiniteFloat | None = None
    noul: Probability | None = None
    confidence: Probability | None = None
    probabilities: dict[str, Probability]


class ClassifierObservation(Contract):
    """Persisted as existing stage-span attributes; contains no state, arguments or player IDs."""

    observation_schema_version: Literal["v1"] = "v1"
    use_case: Identifier
    mode: ClassifierMode
    scope: Identifier | None = None
    taxonomy_version: Identifier
    requested_model: str | None
    resolved_model: str | None = None
    provider: str | None = None
    upstream_provider: str | None = None
    version: str | None = None
    request_id: str | None = None
    request_key: str | None = None
    usage: dict[str, JsonValue] = Field(default_factory=dict)
    cost: float | None = None
    outcome: Literal["off", "eligible", "uncertain", "blocked", "error"]
    answers: dict[Identifier, AnswerObservation] = Field(default_factory=dict)
    reference: dict[Identifier, ReferenceValue] = Field(default_factory=dict)
    reference_kinds: dict[Identifier, ReferenceKind] = Field(default_factory=dict)
    agreement: bool | None = None
    decision: Identifier | None = None
    decision_reason: Identifier | None = None
    decision_reference: Identifier | None = None
    decision_comparison: Identifier | None = None
    decision_thresholds: dict[Identifier, Probability] = Field(default_factory=dict)
    error_category: ClassifierErrorCategory | None = None
    error_transient: bool | None = None
    latency_ms: float = Field(ge=0)
