import ast
import asyncio
import json
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest
from test_advancement_coordinator import setup

from masterclaw.app.advancement_coordinator import AdvancementCoordinator
from masterclaw.app.advancement_safety import (
    AdvancementSafetyAssessment,
    SafetyReason,
    SafetyVerdict,
    capture_advancement_safety_snapshot,
    legacy_v1_fingerprint_projection,
)
from masterclaw.app.decision_checkpoints import (
    decision_input_fingerprint,
    decision_output_type_name,
)
from masterclaw.app.legacy_advancement_safety import (
    AdvancementAuthorizationCheckpoint,
    LegacyAdvancementSafetyDecider,
)
from masterclaw.context.assembler import ContextAssembler
from masterclaw.pipelines.advancement import AdvancementSafetyDecision

LEGACY_OUTPUT_TYPE = (
    "masterclaw.app.advancement_coordinator.AdvancementAuthorizationCheckpoint:v1:28dbb06fd13c2bee"
)
REQUEST = {"kind": "raise", "trait": "T0", "new_aspect": "New"}


def snapshot_for(store, *, request=None):
    return capture_advancement_safety_snapshot(
        store=store,
        game=store.game_state("game"),
        player_id="alice",
        character=store.character_for_player(game_id="game", player_id="alice"),
        scene=store.scene_projection(game_id="game", player_id="alice"),
        request=REQUEST if request is None else request,
    )


class RecordingPipeline:
    def __init__(self, *, allowed=True, error=None):
        self.allowed = allowed
        self.error = error
        self.contexts = []

    async def run(self, *, task, context):
        assert task == "Decide whether advancement is currently fictionally allowed."
        self.contexts.append(context)
        if self.error:
            raise self.error
        return AdvancementSafetyDecision(
            allowed=self.allowed, reason="Свободный текст GM", evidence=["camp"]
        )


def decider_for(store, pipeline):
    return LegacyAdvancementSafetyDecider(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        safety_pipeline=pipeline,
    )


def test_snapshot_is_detached_immutable_and_decider_uses_only_captured_inputs(
    tmp_path, monkeypatch
):
    store = setup(tmp_path)
    history = [{"event_type": "rested", "payload": {"detail": "captured history"}}]
    chats = [{"content": "captured chat"}]
    limits = []

    def events(**kwargs):
        limits.append(kwargs["limit"])
        return history

    monkeypatch.setattr(store, "recent_domain_events", events)
    monkeypatch.setattr(store, "recent_chat_messages", lambda **kwargs: chats)
    request = dict(REQUEST)
    snapshot = snapshot_for(store, request=request)
    assert limits == [4]
    history[0]["payload"]["detail"] = "changed history"
    chats[0]["content"] = "changed chat"
    request["new_aspect"] = "changed request"
    with pytest.raises(FrozenInstanceError):
        snapshot.scene_revision = 9

    pipeline = RecordingPipeline()
    assessment = asyncio.run(decider_for(store, pipeline).assess(snapshot))
    dynamic = pipeline.contexts[0].dynamic_context
    assert "captured history" in dynamic
    assert "captured chat" in dynamic
    assert "changed" not in dynamic
    assert json.loads(snapshot.projections_json)["advancement_request"] == REQUEST
    assert limits == [4]  # No history reread during model invocation.
    assert assessment == AdvancementSafetyAssessment(
        verdict=SafetyVerdict.ALLOW,
        reason=SafetyReason.LEGACY_ALLOWED,
        display_detail="Свободный текст GM",
        evidence=("camp",),
    )


def test_legacy_fingerprint_algorithm_and_schema_identity_are_exact(tmp_path):
    store = setup(tmp_path)
    snapshot = snapshot_for(store)
    expected = {
        "game_id": "game",
        "player_id": "alice",
        "scene_id": "camp",
        "scene_revision": 0,
        "character_id": "hero",
        "traits": [
            {"name": f"T{i}", "level": 3, "aspects": [f"A{i}.{n}" for n in range(3)]}
            for i in range(6)
        ],
        "request": REQUEST,
    }
    assert legacy_v1_fingerprint_projection(snapshot) == expected
    assert decision_output_type_name(AdvancementAuthorizationCheckpoint) == LEGACY_OUTPUT_TYPE
    fingerprint = decision_input_fingerprint(legacy_v1_fingerprint_projection(snapshot))
    assert fingerprint == decision_input_fingerprint(expected)
    # Intentional v1 compatibility debt: semantic history is NOT checkpoint identity.
    changed = replace(snapshot, domain_events_json='[{"event_type":"new danger"}]')
    assert decision_input_fingerprint(legacy_v1_fingerprint_projection(changed)) == fingerprint


def test_pre_seam_checkpoint_replays_without_model_call_or_payload_rewrite(tmp_path):
    store = setup(tmp_path)
    snapshot = snapshot_for(store)
    payload = {
        "decision": {"allowed": True, "reason": "original reason", "evidence": ["original"]},
        "scene_id": "camp",
        "scene_revision": 0,
    }
    checkpoint_args = dict(
        event_id="old-request",
        pipeline_key="advancement_safety",
        output_type=LEGACY_OUTPUT_TYPE,
        game_id="game",
        input_fingerprint=decision_input_fingerprint(legacy_v1_fingerprint_projection(snapshot)),
    )
    store.checkpoint_decision(**checkpoint_args, payload=payload)
    pipeline = RecordingPipeline(error=AssertionError("must not run on replay"))
    coordinator = AdvancementCoordinator(store=store, decider=decider_for(store, pipeline))
    permit = asyncio.run(
        coordinator.authorize_request(
            game_id="game", player_id="alice", request=REQUEST, checkpoint_event_id="old-request"
        )
    )
    assert permit.scene_id == "camp"
    assert permit.scene_revision == 0
    assert permit.reason == "original reason"
    assert not pipeline.contexts
    assert store.decision_checkpoint(**checkpoint_args) == payload


def test_new_checkpoint_replays_and_keeps_legacy_payload(tmp_path):
    store = setup(tmp_path)
    snapshot = snapshot_for(store)
    pipeline = RecordingPipeline(allowed=False)
    decider = decider_for(store, pipeline)
    first = asyncio.run(decider.assess(snapshot, checkpoint_event_id="new-request"))
    replay = asyncio.run(decider.assess(snapshot, checkpoint_event_id="new-request"))
    assert replay == first
    assert first.verdict is SafetyVerdict.DENY
    assert first.reason is SafetyReason.LEGACY_DENIED
    assert first.display_detail == "Свободный текст GM"
    assert len(pipeline.contexts) == 1
    payload = store.decision_checkpoint(
        event_id="new-request",
        pipeline_key="advancement_safety",
        output_type=LEGACY_OUTPUT_TYPE,
        game_id="game",
        input_fingerprint=decision_input_fingerprint(legacy_v1_fingerprint_projection(snapshot)),
    )
    assert payload == {
        "decision": {
            "allowed": False,
            "reason": "Свободный текст GM",
            "evidence": ["camp"],
        },
        "scene_id": "camp",
        "scene_revision": 0,
    }


def test_legacy_provider_exception_is_not_reclassified(tmp_path):
    store = setup(tmp_path)
    error = RuntimeError("legacy failure")
    pipeline = RecordingPipeline(error=error)
    with pytest.raises(RuntimeError) as raised:
        asyncio.run(decider_for(store, pipeline).assess(snapshot_for(store)))
    assert raised.value is error


@pytest.mark.parametrize("verdict", [SafetyVerdict.DENY, SafetyVerdict.UNCERTAIN])
def test_coordinator_consumes_only_typed_assessment_and_preserves_rejection_detail(
    tmp_path, verdict
):
    store = setup(tmp_path)

    class Decider:
        async def assess(self, snapshot, *, checkpoint_event_id=None):
            return AdvancementSafetyAssessment(
                verdict=verdict,
                reason=SafetyReason.INSUFFICIENT_SIGNAL,
                display_detail="original detail",
            )

    coordinator = AdvancementCoordinator(store=store, decider=Decider())
    with pytest.raises(ValueError, match="^advancement is not allowed now: original detail$"):
        asyncio.run(
            coordinator.authorize_request(game_id="game", player_id="alice", request=REQUEST)
        )
    assert store.character_for_player(game_id="game", player_id="alice").experience_spent == 0


def test_coordinator_has_no_model_or_context_assembly_imports():
    source = Path(__file__).parents[1] / "src/masterclaw/app/advancement_coordinator.py"
    imports = [
        node.module
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.module
    ]
    assert not any(
        name.startswith(("masterclaw.context", "masterclaw.pipelines", "masterclaw.adapters"))
        or name.endswith("legacy_advancement_safety")
        for name in imports
    )


@pytest.mark.parametrize("first", ["advancement_coordinator", "legacy_advancement_safety"])
def test_advancement_import_order_has_no_cycle(first):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import masterclaw.app.{first}; "
            "import masterclaw.cli; "
            "from masterclaw.app.legacy_advancement_safety import "
            "AdvancementAuthorizationCheckpoint; "
            "AdvancementAuthorizationCheckpoint.model_json_schema()",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
