import asyncio
import json
from pathlib import Path

import pytest
from test_classifier_state_dispatch import Classifier, Completion, make_service

from masterclaw.app.handlers.world_management import _is_detailed_new_world_request as compatibility
from masterclaw.app.i18n import tr
from masterclaw.app.message_handler import MessageApplication
from masterclaw.app.scenarios import SCENARIOS, CommandId, ScenarioId, is_world_creation_request
from masterclaw.app.state_dispatch_classifier import StateDispatchClassifier
from masterclaw.app.state_dispatch_contracts import StateDispatchProjection
from masterclaw.app.world_intent import _is_detailed_new_world_request
from masterclaw.classifier_calibration import ClassifierSpan, classifier_calibration_report
from masterclaw.classifiers.executor import SemanticClassifierExecutor
from masterclaw.classifiers.policy import ClassifierUseCaseConfig
from masterclaw.context.assembler import AssembledContext, ContextAssembler
from masterclaw.domain.models import IncomingMessage
from masterclaw.domain.state import WorldState
from masterclaw.storage.sqlite import SQLiteStore
from masterclaw.telemetry import bind_trace, reset_trace

CORPUS = json.loads(
    (Path(__file__).parent / "fixtures/world_revision_conflicts.json").read_text(encoding="utf-8")
)


class MixedClassifier(Classifier):
    def __init__(self, probability=0.99):
        super().__init__(choice="revise_world")
        self.probability = probability

    async def classify(self, request):
        result = await super().classify(request)
        answers = dict(result.answers)
        if "new_world_conflict" in request.questions:
            answers["new_world_conflict"] = {
                "type": "noul",
                "noul": self.probability,
                "probabilities": {"true": self.probability, "false": 1 - self.probability},
            }
        return type(result).model_validate({**result.model_dump(), "answers": answers})


@pytest.mark.parametrize("case", CORPUS)
def test_offline_legacy_predicate_and_pre_router_golden(case):
    assert compatibility is _is_detailed_new_world_request
    assert compatibility(case["text"]) is case["legacy"]
    assert is_world_creation_request(case["text"]) is case["pre_router"]
    # Desired semantic labels are offline fixtures, never fabricated runtime gold references.
    assert type(case["semantic"]) is bool


@pytest.mark.parametrize(
    "scenario_id", [key for key, scenario in SCENARIOS.items() if len(scenario.llm_commands) >= 2]
)
def test_only_editing_scenarios_add_noul_to_same_batch(scenario_id):
    scenario = SCENARIOS[scenario_id]
    port = MixedClassifier()
    port.choice = "clarify"
    adapter = StateDispatchClassifier(
        SemanticClassifierExecutor(port, requested_model="alias"),
        ClassifierUseCaseConfig(mode="shadow"),
    )
    result = asyncio.run(
        adapter.observe(
            scenario=scenario,
            projection=StateDispatchProjection(
                message="Design another setting", workspace_stage="review"
            ),
            reference=CommandId.CLARIFY,
        )
    )
    editing = scenario_id in {ScenarioId.WORLD_EDITING_COLLECTING, ScenarioId.WORLD_EDITING_REVIEW}
    assert len(port.requests) == 1
    request = port.requests[0]
    assert request.taxonomy_version == "state_dispatch.v2"
    assert set(request.questions) == {"command"} | ({"new_world_conflict"} if editing else set())
    assert set(request.state) == {"message", "pending_kind", "workspace_stage", "scenario"}
    assert result.observation.outcome != "error"
    assert result.observation.reference["command"] == "clarify"
    if editing:
        assert result.observation.reference["new_world_conflict"] is True
        assert result.observation.reference_kinds == {"new_world_conflict": "legacy_heuristic"}
        assert result.observation.answers["new_world_conflict"].confidence is None
        assert "conditional" in request.questions["new_world_conflict"].instructions
    else:
        assert result.observation.reference_kinds == {}


@pytest.mark.parametrize("case", [case for case in CORPUS if case["pre_router"]])
def test_pre_router_positive_bypass_has_zero_runtime_provider_calls(tmp_path, case):
    store = SQLiteStore(tmp_path / "world.sqlite")
    store.initialize()
    store.create_world(WorldState("world", "Existing"))
    store.save_world_workspace(
        channel_id="channel",
        world_id="world",
        stage="collecting",
        brief="Existing draft",
        settings={},
        sources={},
    )
    baseline, port = Completion("revise_world"), MixedClassifier()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_decisions=make_service(tmp_path, store=store, completion=baseline, classifier=port),
    )
    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="direct", channel_id="channel", author_id="player", content=case["text"]
            )
        )
    )
    assert any(tr(locale, "world_workspace_active") in response for locale in ("en", "ru"))
    assert baseline.calls == len(port.requests) == 0
    assert store.world_workspace("channel")["brief"] == "Existing draft"


def test_world_editing_checkpoint_replay_and_v2_report_preserve_command(tmp_path):
    store = SQLiteStore(tmp_path / "world.sqlite")
    store.initialize()
    baseline, port = Completion("revise_world"), MixedClassifier(0.01)
    service = make_service(tmp_path, store=store, completion=baseline, classifier=port)
    records = []

    class Sink:
        def record_stage_spans(self, rows):
            records.extend(rows)

        def classifier_spans(self, *, since_hours):
            yield from (
                ClassifierSpan(row["stage"], json.dumps(row["attributes"]))
                for row in records
                if row["stage"] == "classifier.state_dispatch"
            )

    args = dict(
        message=IncomingMessage.now(
            event_id="editing",
            channel_id="channel",
            author_id="player",
            content="Do not create a new world; revise this draft.",
        ),
        scenario=SCENARIOS[ScenarioId.WORLD_EDITING_REVIEW],
        context=AssembledContext("", "", (), 0),
        game_id=None,
        workspace_stage="review",
    )
    binding = bind_trace(Sink(), trace_id="private")
    try:
        first = asyncio.run(service.decide(**args))
        replay = asyncio.run(service.decide(**args))
    finally:
        reset_trace(binding)
    assert first.command is replay.command is CommandId.REVISE_WORLD
    assert first.shadow_observation.reference == {
        "command": "revise_world",
        "new_world_conflict": True,
    }
    assert (
        first.shadow_observation.agreement is False
    )  # disagreement with heuristic, not wrong semantics
    assert replay.replayed and replay.shadow_observation is None
    assert baseline.calls == len(port.requests) == 1
    report = classifier_calibration_report(Sink())
    group = report["groups"][0]
    assert group["taxonomy_version"] == "state_dispatch.v2"
    question = next(q for q in group["questions"] if q["question"] == "new_world_conflict")
    assert question["reference_kind"] == "legacy_heuristic"
    assert question["comparable"] == 1 and question["agreements"] == 0
    assert "Do not" not in json.dumps(report)
    legacy = next(row for row in records if row["stage"] == "classifier.state_dispatch")
    legacy = json.loads(json.dumps(legacy))
    legacy["attributes"]["taxonomy_version"] = "state_dispatch.v1"
    legacy["attributes"]["answers"].pop("new_world_conflict")
    legacy["attributes"]["reference"].pop("new_world_conflict")
    legacy["attributes"].pop("reference_kinds")
    records.append(legacy)
    mixed_report = classifier_calibration_report(Sink())
    assert {group["taxonomy_version"] for group in mixed_report["groups"]} == {
        "state_dispatch.v1",
        "state_dispatch.v2",
    }


def test_durable_world_revision_replay_skips_state_and_classifier(tmp_path):
    store = SQLiteStore(tmp_path / "world.sqlite")
    store.initialize()
    store.create_world(WorldState("world", "Existing"))
    store.save_world_workspace(
        channel_id="channel",
        world_id="world",
        stage="collecting",
        brief="Existing draft",
        settings={},
        sources={},
    )
    store.apply_world_revision(
        event_id="revision",
        channel_id="channel",
        world_id="world",
        expected_world_revision=0,
        expected_workspace_revision=0,
        title="Revised",
        brief="Revised brief",
        settings={},
        sources={},
    )
    baseline, port = Completion("revise_world"), MixedClassifier()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_decisions=make_service(tmp_path, store=store, completion=baseline, classifier=port),
    )
    message = IncomingMessage.now(
        event_id="revision",
        channel_id="channel",
        author_id="player",
        content="Please revise the current draft with more pirates.",
    )
    response = asyncio.run(app(message))
    assert any(tr(locale, "world_inputs_updated") in response for locale in ("en", "ru"))
    assert baseline.calls == len(port.requests) == 0
    assert store.world_workspace("channel")["brief"] == "Revised brief"
