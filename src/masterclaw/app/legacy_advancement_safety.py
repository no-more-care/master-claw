"""Legacy model and durable-payload adapter for the advancement safety seam."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict

from masterclaw.app.advancement_safety import (
    AdvancementSafetyAssessment,
    AdvancementSafetySnapshot,
    SafetyReason,
    SafetyVerdict,
    legacy_v1_fingerprint_projection,
)
from masterclaw.app.decision_checkpoints import (
    decision_input_fingerprint,
    decision_output_type_name,
)
from masterclaw.context.assembler import ContextAssembler, ContextHistory
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.pipelines.advancement import AdvancementSafetyDecision
from masterclaw.pipelines.base import BoundedJsonPipeline
from masterclaw.storage.sqlite import SQLiteStore


class AdvancementAuthorizationCheckpoint(BaseModel):
    # This durable schema predates the seam. Preserve its qualified identity as well
    # as its JSON schema; application assessments are deliberately not persisted here.
    model_config = ConfigDict(extra="forbid")

    decision: AdvancementSafetyDecision
    scene_id: str
    scene_revision: int


AdvancementAuthorizationCheckpoint.__module__ = "masterclaw.app.advancement_coordinator"


class LegacyAdvancementSafetyDecider:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        context: ContextAssembler,
        safety_pipeline: BoundedJsonPipeline[AdvancementSafetyDecision],
    ) -> None:
        self._store = store
        self._context = context
        self._pipeline = safety_pipeline

    async def assess(
        self,
        snapshot: AdvancementSafetySnapshot,
        *,
        checkpoint_event_id: str | None = None,
    ) -> AdvancementSafetyAssessment:
        # Assemble before lookup, as legacy did: preserve context/invalid-output
        # exceptions and repair behavior. All semantic inputs come from the snapshot.
        assembled = self._context.assemble(
            manifest_for(PipelineName.ADVANCEMENT_SAFETY),
            json.loads(snapshot.projections_json),
            history=ContextHistory(
                json.loads(snapshot.domain_events_json), json.loads(snapshot.chat_messages_json)
            ),
        )
        fingerprint = decision_input_fingerprint(legacy_v1_fingerprint_projection(snapshot))
        output_type = decision_output_type_name(AdvancementAuthorizationCheckpoint)
        checkpoint: AdvancementAuthorizationCheckpoint | None = None
        if checkpoint_event_id is not None:
            payload = self._store.decision_checkpoint(
                event_id=checkpoint_event_id,
                pipeline_key="advancement_safety",
                output_type=output_type,
                game_id=snapshot.game_id,
                input_fingerprint=fingerprint,
            )
            if payload is not None:
                checkpoint = AdvancementAuthorizationCheckpoint.model_validate(payload)
        replayed = checkpoint is not None
        if checkpoint is None:
            decision = await self._pipeline.run(
                task="Decide whether advancement is currently fictionally allowed.",
                context=assembled,
            )
            checkpoint = AdvancementAuthorizationCheckpoint(
                decision=decision,
                scene_id=snapshot.scene_id,
                scene_revision=snapshot.scene_revision,
            )
            if checkpoint_event_id is not None:
                canonical = self._store.checkpoint_decision(
                    event_id=checkpoint_event_id,
                    pipeline_key="advancement_safety",
                    output_type=output_type,
                    payload=checkpoint.model_dump(mode="json"),
                    game_id=snapshot.game_id,
                    input_fingerprint=fingerprint,
                )
                checkpoint = AdvancementAuthorizationCheckpoint.model_validate(canonical)
        decision = checkpoint.decision
        return AdvancementSafetyAssessment(
            verdict=SafetyVerdict.ALLOW if decision.allowed else SafetyVerdict.DENY,
            reason=SafetyReason.LEGACY_ALLOWED if decision.allowed else SafetyReason.LEGACY_DENIED,
            display_detail=decision.reason,
            evidence=tuple(decision.evidence),
            replayed=replayed,
        )
