"""Exact legacy compound context and checkpoint adapter, with no execution authority."""

from __future__ import annotations

from typing import Protocol

from masterclaw.app.compound_planning import CompoundPlanningSnapshot, PreparedCompoundPlan
from masterclaw.app.context_inputs import ContextInputCapture
from masterclaw.app.decision_checkpoints import DecisionPipeline, run_checkpointed_decision
from masterclaw.app.scenarios import SCENARIOS, Scenario, ScenarioId
from masterclaw.context.assembler import ContextAssembler
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.models import IncomingMessage
from masterclaw.domain.state import PendingInteraction
from masterclaw.pipelines.compound_play import CompoundPlayPlan
from masterclaw.storage.sqlite import SQLiteStore
from masterclaw.telemetry import stage_span


class PlayScenarioProjection(Protocol):
    def __call__(
        self,
        scenario: Scenario,
        *,
        game_id: str,
        channel_id: str,
        player_id: str,
        workspace: None,
        pending: None,
    ) -> dict[str, object]: ...


class LegacyCompoundPlanningCapture:
    def __init__(
        self,
        *,
        context: ContextAssembler,
        project_scenario: PlayScenarioProjection,
        capture_context: ContextInputCapture,
    ) -> None:
        self._context = context
        self._project_scenario = project_scenario
        self._capture_context = capture_context

    def __call__(
        self,
        *,
        message: IncomingMessage,
        game_id: str,
        replacing_pending: PendingInteraction | None,
        clarification_answer: str | None,
    ) -> CompoundPlanningSnapshot:
        projections = self._project_scenario(
            SCENARIOS[ScenarioId.PLAY],
            game_id=game_id,
            channel_id=message.channel_id,
            player_id=message.author_id,
            workspace=None,
            pending=None,
        )
        original_request = (
            str(replacing_pending.payload.get("original_request") or message.content)
            if replacing_pending is not None
            else message.content
        )
        projections["player_request"] = (
            original_request
            if clarification_answer is None
            else {
                "original_request": original_request,
                "pending_question": replacing_pending.prompt,
                "player_answer": clarification_answer,
                "prior_answers": replacing_pending.payload.get("clarification_answers", []),
            }
        )
        manifest = manifest_for(PipelineName.COMPOUND_PLAY)
        inputs = self._capture_context(
            manifest,
            projections,
            game_id=game_id,
            channel_id=message.channel_id,
            player_id=message.author_id,
        )
        with stage_span(
            "context.assembly",
            component="context_assembler",
            operation=manifest.pipeline.value,
            attributes={"pipeline": manifest.pipeline.value},
        ):
            assembled = self._context.assemble(manifest, inputs.projections, history=inputs.history)
        return CompoundPlanningSnapshot(
            event_id=message.event_id,
            game_id=game_id,
            original_request=original_request,
            continuation=clarification_answer is not None,
            inputs=inputs,
            context=assembled,
        )


class LegacyCompoundPlanDecider:
    def __init__(self, *, store: SQLiteStore, pipeline: DecisionPipeline[CompoundPlayPlan]) -> None:
        self._store = store
        self._pipeline = pipeline

    async def decide(self, snapshot: CompoundPlanningSnapshot) -> PreparedCompoundPlan:
        plan = await run_checkpointed_decision(
            store=self._store,
            event_id=snapshot.event_id,
            pipeline_key="compound_play",
            pipeline=self._pipeline,
            task=snapshot.task,
            context=snapshot.context,
            game_id=snapshot.game_id,
            # NULL is the deployed identity; adding a fingerprint would invalidate old rows.
        )
        return PreparedCompoundPlan(snapshot.original_request, plan.model_dump_json())
