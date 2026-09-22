import ast
import asyncio
import hashlib
import json
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest
from test_compound_play_flow import Completion, message, setup_store
from test_player_narration import NeverIntent

from masterclaw.app.compound_planning import CompoundPlanningSnapshot, PreparedCompoundPlan
from masterclaw.app.decision_checkpoints import decision_output_type_name
from masterclaw.app.i18n import tr
from masterclaw.app.legacy_compound_planning import LegacyCompoundPlanDecider
from masterclaw.app.message_handler import MessageApplication
from masterclaw.app.scenarios import SCENARIOS, CommandId, ScenarioId
from masterclaw.app.state_dispatch_classifier import StateDispatchClassifier
from masterclaw.app.state_dispatch_service import StateDispatchDecisionService
from masterclaw.classifiers.base import ClassificationResponse
from masterclaw.classifiers.executor import SemanticClassifierExecutor
from masterclaw.classifiers.policy import ClassifierUseCaseConfig
from masterclaw.context.assembler import ContextAssembler
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.state import PendingInteraction, PendingKind
from masterclaw.pipelines.base import PipelineValidationError, TransientProviderError
from masterclaw.pipelines.compound_play import CompoundPlayPlan
from masterclaw.pipelines.state_decision import StateDecisionRouter

ROOT = Path(__file__).parents[1]
IDENTITY = "masterclaw.pipelines.compound_play.CompoundPlayPlan:v1:be3c94493785178a"


class Pipeline:
    output_type = CompoundPlayPlan

    def __init__(self, error=None):
        self.error = error
        self.calls = []
        self.result = CompoundPlayPlan(clarification_question="Что сделать сначала?")

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


def application(store, pipeline=None, **kwargs):
    return MessageApplication(
        store=store,
        context=ContextAssembler(ROOT / "prompts"),
        state_router=StateDecisionRouter(NeverIntent()),
        compound_play_pipeline=pipeline,
        **kwargs,
    )


@pytest.mark.parametrize("continuation", [False, True])
def test_task_context_history_and_enrichment_are_byte_equivalent(
    tmp_path, monkeypatch, continuation
):
    store = setup_store(tmp_path)
    pipeline = Pipeline()
    app = application(store, pipeline)
    incoming = message("compound", "Осмотрюсь, а потом открою дверь. I ask and act.")
    pending = (
        PendingInteraction(
            "pending",
            "game",
            "alice",
            "room",
            PendingKind.CLARIFICATION,
            "Which door?",
            {
                "original_request": "Original request",
                "clarification_answers": [{"question": "Where?", "answer": "Here"}],
            },
        )
        if continuation
        else None
    )
    answer = "The north door" if continuation else None
    calls = []

    def events(**kwargs):
        calls.append(("events", kwargs))
        return [{"event_type": "scene_patched", "payload": {"facts": ["Public fact"]}}]

    def chats(**kwargs):
        calls.append(("chats", kwargs))
        return [{"content": "Реплика в этом канале"}]

    monkeypatch.setattr(store, "recent_domain_events", events)
    monkeypatch.setattr(store, "recent_chat_messages", chats)
    # Literal old planning block, independently assembled via the retained shared wrapper.
    projections = app._scenario_context_projections(
        SCENARIOS[ScenarioId.PLAY],
        game_id="game",
        channel_id="game",
        player_id="alice",
        workspace=None,
        pending=None,
    )
    original = incoming.content if pending is None else "Original request"
    projections["player_request"] = (
        original
        if answer is None
        else {
            "original_request": original,
            "pending_question": pending.prompt,
            "player_answer": answer,
            "prior_answers": pending.payload["clarification_answers"],
        }
    )
    expected = app._assemble_context(
        manifest_for(PipelineName.COMPOUND_PLAY),
        projections,
        game_id="game",
        channel_id="game",
        player_id="alice",
    )
    expected_calls = list(calls)
    calls.clear()
    snapshot = app._compound_planning.capture(
        message=incoming,
        game_id="game",
        replacing_pending=pending,
        clarification_answer=answer,
    )
    prepared = asyncio.run(app._compound_planning.plan(snapshot))
    expected_task = "Decompose this compound play request without resolving it."
    if continuation:
        expected_task += (
            " Continue the exact original request using the supplied typed pending "
            "question and player answer; do not discard either one."
        )
    assert (
        calls
        == expected_calls
        == [
            ("events", {"game_id": "game", "limit": 2}),
            ("chats", {"game_id": "game", "channel_id": "game", "player_id": "alice", "limit": 2}),
        ]
    )
    assert pipeline.calls == [{"task": expected_task, "context": expected}]
    assert snapshot.context == expected
    assert prepared.original_request == original
    assert snapshot.inputs.projections["current_scene"]["participant_characters"] == [
        {"player_id": "alice", "name": "Mara"}
    ]
    assert "Реплика в этом канале" in expected.dynamic_context
    assert "Public fact" in expected.dynamic_context


@pytest.mark.parametrize("preexisting", [False, True])
def test_exact_schema_key_null_fingerprint_and_replay_zero_model_calls(tmp_path, preexisting):
    store = setup_store(tmp_path)
    pipeline = Pipeline()
    app = application(store, pipeline)
    snapshot = app._compound_planning.capture(
        message=message("compound", "One; two"), game_id="game"
    )
    assert decision_output_type_name(CompoundPlayPlan) == IDENTITY
    if preexisting:
        store.checkpoint_decision(
            event_id="compound",
            game_id="game",
            pipeline_key="compound_play",
            output_type=IDENTITY,
            payload=pipeline.result.model_dump(mode="json"),
        )
    first = asyncio.run(app._compound_planning.plan(snapshot))
    pipeline.error = AssertionError("replay cannot invoke a model")
    replay = asyncio.run(app._compound_planning.plan(snapshot))
    assert first == replay
    assert len(pipeline.calls) == (0 if preexisting else 1)
    with store.connect() as connection:
        row = connection.execute("SELECT * FROM decision_checkpoints").fetchone()
    assert row["pipeline_key"] == "compound_play"
    assert row["output_type"] == IDENTITY and row["input_fingerprint"] is None
    assert json.loads(row["payload_json"]) == pipeline.result.model_dump(mode="json")
    assert first.plan is not first.plan
    detached = first.plan
    detached.clarification_question = "mutated"
    assert first.plan.clarification_question == "Что сделать сначала?"
    with pytest.raises(FrozenInstanceError):
        first.original_request = "mutated"
    with pytest.raises(FrozenInstanceError):
        snapshot.game_id = "mutated"
    assert not (
        {"store", "callback", "decider", "pipeline"}
        & {
            field.name
            for cls in (CompoundPlanningSnapshot, PreparedCompoundPlan)
            for field in fields(cls)
        }
    )


def test_custom_assembler_pipeline_validation_error_stays_outside_model_catch(tmp_path):
    store = setup_store(tmp_path)
    pipeline = Pipeline()

    class BrokenAssembler:
        def assemble(self, *args, **kwargs):
            raise PipelineValidationError("custom assembly failure")

    app = MessageApplication(
        store=store,
        context=BrokenAssembler(),
        state_router=StateDecisionRouter(NeverIntent()),
        compound_play_pipeline=pipeline,
    )
    with pytest.raises(PipelineValidationError, match="custom assembly failure"):
        asyncio.run(
            app._handle_compound_play(message=message("compound", "One; two"), game_id="game")
        )
    assert not pipeline.calls


def test_model_validation_fallback_and_transient_exception_preserve_ux(tmp_path):
    store = setup_store(tmp_path)
    pipeline = Pipeline(PipelineValidationError("invalid model plan"))
    app = application(store, pipeline)
    args = {"message": message("compound", "One; two"), "game_id": "game"}
    assert asyncio.run(app._handle_compound_play(**args)) == tr(
        app._locale("game"),
        manifest_for(PipelineName.COMPOUND_PLAY).on_invalid.value,
    )
    pipeline.error = TransientProviderError("retry later")
    with pytest.raises(TransientProviderError):
        asyncio.run(app._handle_compound_play(**args))
    assert len(pipeline.calls) == 2


def test_unavailable_and_mutually_exclusive_legacy_wiring(tmp_path):
    store = setup_store(tmp_path)
    app = application(store)
    assert asyncio.run(
        app._handle_compound_play(
            message=message("compound", "One; two"),
            game_id="game",
        )
    ) == tr(app._locale("game"), "conversation_clarification")
    decider = LegacyCompoundPlanDecider(store=store, pipeline=Pipeline())
    injected = application(store, compound_plan_decider=decider)
    assert injected._compound_planning.available
    with pytest.raises(ValueError, match="not multiple"):
        application(store, Pipeline(), compound_plan_decider=decider)
    with pytest.raises(ValueError, match="not multiple"):
        application(store, compound_plan_decider=decider, compound_planning=app._compound_planning)
    assert (
        application(store, compound_planning=injected._compound_planning)._compound_planning
        is injected._compound_planning
    )


def test_execution_tail_is_byte_identical_and_no_new_classifier_dependency():
    path = ROOT / "src/masterclaw/app/handlers/compound.py"
    source = path.read_text(encoding="utf-8")
    tail = source[source.index("        if any(part.kind is PlayRequestPartKind.HELP") :].rstrip()
    assert hashlib.sha256(tail.encode()).hexdigest() == (
        "19875d6a1409568b3155f404ef66f3aafc96c7cb39e128b21d57119657d6e80c"
    )
    for name in ("compound_planning.py", "legacy_compound_planning.py"):
        tree = ast.parse((ROOT / "src/masterclaw/app" / name).read_text(encoding="utf-8"))
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any(
            "classifiers" in module or "handlers" in module or "adapters" in module
            for module in imports
        )


def test_compound_routing_shadow_remains_one_state_dispatch_call_and_replay_skips(tmp_path):
    store = setup_store(tmp_path)
    baseline = Completion(
        '{"command":"compound_play","argument":null,"confidence":1,"evidence":"two parts"}'
    )

    class Classifier:
        calls = 0

        async def classify(self, request):
            self.calls += 1
            assert request.taxonomy_version == "state_dispatch.v1"
            return ClassificationResponse(
                request_key=request.request_key,
                taxonomy_version=request.taxonomy_version,
                provider="test",
                model="resolved",
                answers={
                    "command": {
                        "type": "choice",
                        "choice": "compound_play",
                        "confidence": 1,
                        "probabilities": {
                            label: float(label == "compound_play")
                            for label in request.questions["command"].criteria
                        },
                    }
                },
            )

    port = Classifier()
    service = StateDispatchDecisionService(
        store=store,
        baseline=StateDecisionRouter(baseline),
        classifier=StateDispatchClassifier(
            SemanticClassifierExecutor(port, requested_model="alias"),
            ClassifierUseCaseConfig(mode="shadow"),
        ),
    )
    app = application(store, Pipeline())
    snapshot = app._compound_planning.capture(
        message=message("compound", "One; two"), game_id="game"
    )
    args = dict(
        message=message("compound", "One; two"),
        scenario=SCENARIOS[ScenarioId.PLAY],
        context=snapshot.context,
        game_id="game",
    )
    first = asyncio.run(service.decide(**args))
    asyncio.run(app._compound_planning.plan(snapshot))
    replay = asyncio.run(service.decide(**args))
    asyncio.run(app._compound_planning.plan(snapshot))
    assert first.command is replay.command is CommandId.COMPOUND_PLAY
    assert first.shadow_observation.reference == {"command": "compound_play"}
    assert first.shadow_observation.answers["command"].choice == "compound_play"
    assert first.shadow_observation.agreement is True
    assert replay.replayed and replay.shadow_observation is None
    assert baseline.calls == port.calls == 1
