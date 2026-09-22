"""Compound decomposition seam; execution and game mechanics remain in the handler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from masterclaw.app.context_inputs import ContextInputSnapshot
from masterclaw.context.assembler import AssembledContext
from masterclaw.domain.models import IncomingMessage
from masterclaw.domain.state import PendingInteraction
from masterclaw.pipelines.compound_play import CompoundPlayPlan


@dataclass(frozen=True, slots=True)
class CompoundPlanningSnapshot:
    event_id: str
    game_id: str
    original_request: str
    continuation: bool
    inputs: ContextInputSnapshot
    context: AssembledContext

    @property
    def task(self) -> str:
        task = "Decompose this compound play request without resolving it."
        if self.continuation:
            task += (
                " Continue the exact original request using the supplied typed pending "
                "question and player answer; do not discard either one."
            )
        return task


@dataclass(frozen=True, slots=True)
class PreparedCompoundPlan:
    original_request: str
    plan_json: str

    @property
    def plan(self) -> CompoundPlayPlan:
        """Return a detached canonical plan, never expose mutable captured state."""
        return CompoundPlayPlan.model_validate_json(self.plan_json)


class CompoundPlanDecider(Protocol):
    async def decide(self, snapshot: CompoundPlanningSnapshot) -> PreparedCompoundPlan: ...


class CompoundPlanningCapture(Protocol):
    def __call__(
        self,
        *,
        message: IncomingMessage,
        game_id: str,
        replacing_pending: PendingInteraction | None,
        clarification_answer: str | None,
    ) -> CompoundPlanningSnapshot: ...


class CompoundPlanningCoordinator:
    def __init__(
        self, *, capture: CompoundPlanningCapture, decider: CompoundPlanDecider | None = None
    ) -> None:
        self._capture = capture
        self._decider = decider

    @property
    def available(self) -> bool:
        return self._decider is not None

    def capture(
        self,
        *,
        message: IncomingMessage,
        game_id: str,
        replacing_pending: PendingInteraction | None = None,
        clarification_answer: str | None = None,
    ) -> CompoundPlanningSnapshot:
        # Synchronous context assembly remains outside the model-validation UX boundary.
        return self._capture(
            message=message,
            game_id=game_id,
            replacing_pending=replacing_pending,
            clarification_answer=clarification_answer,
        )

    async def plan(self, snapshot: CompoundPlanningSnapshot) -> PreparedCompoundPlan:
        if self._decider is None:
            raise RuntimeError("compound plan decider is unavailable")
        return await self._decider.decide(snapshot)
