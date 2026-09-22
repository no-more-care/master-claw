from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from masterclaw.classifiers.base import ChoiceAnswer, Contract


class ClassifierMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"


class ClassifierConfig(Contract):
    provider: Literal["jev"] = "jev"
    model: str = Field(default="~typesafe/jev-latest", min_length=1, max_length=200)
    mode: ClassifierMode = ClassifierMode.OFF
    threshold: float = Field(default=0.95, ge=0, le=1, allow_inf_nan=False)
    timeout_seconds: float = Field(default=5.0, gt=0, le=60, allow_inf_nan=False)


class ShadowDisposition(StrEnum):
    ELIGIBLE = "eligible"
    UNCERTAIN = "uncertain"
    BLOCKED = "blocked"


def assess_choice(
    answer: ChoiceAnswer, *, allowed: set[str], blocked: set[str], threshold: float
) -> ShadowDisposition:
    """Assess a shadow signal only; this never authorizes executing a command."""
    if answer.choice not in allowed or answer.choice in blocked:
        return ShadowDisposition.BLOCKED
    if min(answer.confidence, answer.probabilities[answer.choice]) < threshold:
        return ShadowDisposition.UNCERTAIN
    return ShadowDisposition.ELIGIBLE
