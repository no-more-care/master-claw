from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import Field, JsonValue

from masterclaw.classifiers.base import (
    ClassifierErrorCategory,
    Contract,
    Identifier,
    Probability,
)

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


class ClassifierObservation(Contract):
    """Persisted as existing stage-span attributes; contains no state, arguments or player IDs."""

    mode: Literal["shadow"] = "shadow"
    scenario: Identifier
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
    outcome: Literal["eligible", "uncertain", "blocked", "error"]
    command: Identifier | None = None
    baseline_command: Identifier
    confidence: Probability | None = None
    probability: Probability | None = None
    probabilities: dict[Identifier, Probability] = Field(default_factory=dict)
    agreement: bool | None = None
    error_category: ClassifierErrorCategory | None = None
    error_transient: bool | None = None
    latency_ms: float = Field(ge=0)
