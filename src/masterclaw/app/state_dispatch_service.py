from __future__ import annotations

from masterclaw.app.decision_checkpoints import DecisionPipeline, run_checkpointed_decision
from masterclaw.app.scenarios import Scenario
from masterclaw.app.state_dispatch_classifier import StateDispatchClassifier
from masterclaw.app.state_dispatch_contracts import StateDispatchDecision, StateDispatchProjection
from masterclaw.classifiers.observations import ClassifierObservation
from masterclaw.context.assembler import AssembledContext
from masterclaw.domain.models import IncomingMessage
from masterclaw.pipelines.state_decision import StateDecisionBase, StateDecisionRouter, command_of
from masterclaw.storage.sqlite import SQLiteStore


class _StateDispatchStage:
    """Application orchestration within the original typed decision checkpoint."""

    def __init__(
        self,
        *,
        baseline: DecisionPipeline[StateDecisionBase],
        scenario: Scenario,
        projection: StateDispatchProjection,
        classifier: StateDispatchClassifier | None,
    ) -> None:
        self._baseline = baseline
        self._scenario = scenario
        self._projection = projection
        self._classifier = classifier
        self.executed = False
        self.observation: ClassifierObservation | None = None

    @property
    def output_type(self) -> type[StateDecisionBase]:
        # Preserve the baseline class/module/schema identity, including existing checkpoints.
        return self._baseline.output_type

    async def run(self, *, task: str, context: AssembledContext) -> StateDecisionBase:
        self.executed = True
        decision = await self._baseline.run(task=task, context=context)
        if self._classifier is not None and self._classifier.enabled:
            evaluation = await self._classifier.observe(
                scenario=self._scenario,
                projection=self._projection,
                reference=command_of(decision),
            )
            self.observation = evaluation.observation
        return decision


class StateDispatchDecisionService:
    """Coordinates the authoritative baseline, optional shadow, and durable replay."""

    def __init__(
        self,
        *,
        store: SQLiteStore,
        baseline: StateDecisionRouter,
        classifier: StateDispatchClassifier | None = None,
    ) -> None:
        self._store = store
        self._baseline = baseline
        self._classifier = classifier

    async def decide(
        self,
        *,
        message: IncomingMessage,
        scenario: Scenario,
        context: AssembledContext,
        game_id: str | None,
        pending_kind: str | None = None,
        workspace_stage: str | None = None,
    ) -> StateDispatchDecision:
        stage = _StateDispatchStage(
            baseline=self._baseline.pipeline_for(scenario),
            scenario=scenario,
            projection=StateDispatchProjection(
                message=message.content,
                pending_kind=pending_kind,
                workspace_stage=workspace_stage,
            ),
            classifier=self._classifier,
        )
        decision = await run_checkpointed_decision(
            store=self._store,
            event_id=message.event_id,
            pipeline_key="state_dispatch",
            pipeline=stage,
            task=f"Choose exactly one scenario command for:\n{message.content}",
            context=context,
            game_id=game_id,
        )
        return StateDispatchDecision(
            command=command_of(decision),
            argument=decision.argument,
            confidence=decision.confidence,
            evidence=decision.evidence,
            replayed=not stage.executed,
            shadow_observation=stage.observation,
        )
