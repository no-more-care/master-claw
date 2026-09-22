from __future__ import annotations

from dataclasses import dataclass

from masterclaw.app.scenarios import CommandId
from masterclaw.classifiers.observations import ClassifierObservation


@dataclass(frozen=True, slots=True)
class StateDispatchProjection:
    """Minimal semantic input; deliberately excludes identifiers, history and GM context."""

    message: str
    pending_kind: str | None = None
    workspace_stage: str | None = None


@dataclass(frozen=True, slots=True)
class StateDispatchDecision:
    """Normalized authoritative result; metadata never substitutes a classifier command."""

    command: CommandId
    argument: str | None
    confidence: float
    evidence: str
    replayed: bool
    shadow_observation: ClassifierObservation | None = None
