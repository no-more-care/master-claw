import ast
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from masterclaw.app.decision_checkpoints import decision_output_type_name
from masterclaw.app.scenarios import SCENARIOS, CommandId, ScenarioId
from masterclaw.app.state_dispatch_classifier import StateDispatchClassifier
from masterclaw.app.state_dispatch_service import StateDispatchDecisionService
from masterclaw.classifiers.base import ClassificationResponse
from masterclaw.classifiers.executor import SemanticClassifierExecutor
from masterclaw.classifiers.policy import ClassifierUseCaseConfig
from masterclaw.context.assembler import AssembledContext
from masterclaw.domain.models import IncomingMessage
from masterclaw.pipelines.base import CompletionResult
from masterclaw.pipelines.state_decision import StateDecisionRouter, state_decision_type
from masterclaw.storage.sqlite import SQLiteStore


class Baseline:
    calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        assert kwargs["task"] == "Choose exactly one scenario command for:\nfresh message"
        return CompletionResult(
            json.dumps(
                {
                    "command": "clarify",
                    "argument": None,
                    "confidence": 0.98,
                    "evidence": "authoritative baseline",
                }
            ),
            used_tool=True,
        )


class Classifier:
    def __init__(self):
        self.requests = []

    async def classify(self, request):
        self.requests.append(request)
        candidates = request.questions["command"].criteria
        return ClassificationResponse(
            request_key=request.request_key,
            taxonomy_version=request.taxonomy_version,
            provider="test",
            model="resolved",
            answers={
                "command": {
                    "type": "choice",
                    "choice": "show_help",
                    "confidence": 1,
                    "probabilities": {
                        label: 1.0 if label == "show_help" else 0.0 for label in candidates
                    },
                }
            },
        )


def inputs():
    return dict(
        message=IncomingMessage.now(
            event_id="event",
            channel_id="private-channel",
            author_id="private-player",
            content="fresh message",
        ),
        scenario=SCENARIOS[ScenarioId.WORLD_SELECTION],
        context=AssembledContext("private rules", "private GM state", (), 0),
        game_id=None,
    )


def build_service(store):
    baseline, classifier = Baseline(), Classifier()
    service = StateDispatchDecisionService(
        store=store,
        baseline=StateDecisionRouter(baseline),
        classifier=StateDispatchClassifier(
            SemanticClassifierExecutor(classifier, requested_model="alias"),
            ClassifierUseCaseConfig(mode="shadow"),
        ),
    )
    return service, baseline, classifier


def test_service_preserves_checkpoint_payload_and_replay_skips_both_models(tmp_path):
    store = SQLiteStore(tmp_path / "decisions.sqlite3")
    store.initialize()
    service, baseline, classifier = build_service(store)
    request = inputs()
    first = asyncio.run(service.decide(**request))
    replay = asyncio.run(service.decide(**request))
    assert first.command is replay.command is CommandId.CLARIFY
    assert first.argument is replay.argument is None
    assert first.confidence == replay.confidence == 0.98
    assert first.evidence == replay.evidence == "authoritative baseline"
    assert first.replayed is False
    assert replay.replayed is True
    assert first.shadow_observation.agreement is False
    assert replay.shadow_observation is None
    assert baseline.calls == len(classifier.requests) == 1
    assert classifier.requests[0].state == {
        "message": "fresh message",
        "scenario": "world_selection",
        "pending_kind": None,
        "workspace_stage": None,
    }
    assert "private" not in classifier.requests[0].model_dump_json()
    with store.connect() as connection:
        row = connection.execute("SELECT * FROM decision_checkpoints").fetchone()
    assert row["pipeline_key"] == "state_dispatch"
    assert row["input_fingerprint"] is None
    assert row["output_type"] == (
        "masterclaw.pipelines.state_decision.WorldSelectionStateDecision:v1:924f6a5731815444"
    )
    assert set(json.loads(row["payload_json"])) == {"command", "argument", "confidence", "evidence"}


def test_service_reads_preexisting_baseline_checkpoint_without_reclassification(tmp_path):
    store = SQLiteStore(tmp_path / "decisions.sqlite3")
    store.initialize()
    payload = {
        "command": "select_world",
        "argument": "Original World",
        "confidence": 0.96,
        "evidence": "original selection",
    }
    store.checkpoint_decision(
        event_id="event",
        pipeline_key="state_dispatch",
        game_id=None,
        output_type=(
            "masterclaw.pipelines.state_decision.WorldSelectionStateDecision:v1:924f6a5731815444"
        ),
        payload=payload,
    )
    service, baseline, classifier = build_service(store)
    replay = asyncio.run(service.decide(**inputs()))
    assert replay.command is CommandId.SELECT_WORLD
    assert replay.argument == "Original World"
    assert replay.confidence == 0.96
    assert replay.evidence == "original selection"
    assert replay.replayed and replay.shadow_observation is None
    assert baseline.calls == len(classifier.requests) == 0


@pytest.mark.parametrize(
    ("scenario", "fingerprint"),
    [
        (ScenarioId.WORLD_SELECTION, "924f6a5731815444"),
        (ScenarioId.WORLD_EDITING_COLLECTING, "6e712b28145c3138"),
        (ScenarioId.WORLD_EDITING_REVIEW, "dd7326aa33148322"),
        (ScenarioId.PREPARATION, "671a0997430e2de1"),
        (ScenarioId.PLAY, "2b0b767efe2be2d1"),
        (ScenarioId.PAUSED, "ec1da69adb560008"),
        (ScenarioId.FINISHED, "ea2725366faa0e9d"),
        (ScenarioId.PLAY_PENDING_POOL, "c9000f3756ed4adb"),
        (ScenarioId.PLAY_PENDING_NARRATION, "d3e7a8db2b5c5847"),
        (ScenarioId.PLAY_PENDING_OTHER, "30cded6453f95d8b"),
        (ScenarioId.ROLL_RESUME, "61eae1b81bdf8440"),
    ],
)
def test_all_original_checkpoint_schema_identities_are_preserved(scenario, fingerprint):
    identity = decision_output_type_name(state_decision_type(scenario))
    assert identity.startswith("masterclaw.pipelines.state_decision.")
    assert identity.endswith(f":v1:{fingerprint}")


def test_classifier_and_baseline_import_boundaries_are_acyclic():
    root = Path(__file__).parents[1] / "src" / "masterclaw"
    for path in (root / "classifiers").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any(
            module.startswith(("masterclaw.pipelines", "masterclaw.app")) for module in imports
        ), path
    baseline = ast.parse((root / "pipelines" / "state_decision.py").read_text(encoding="utf-8"))
    imports = [node.module or "" for node in ast.walk(baseline) if isinstance(node, ast.ImportFrom)]
    assert not any(module.startswith("masterclaw.classifiers") for module in imports)


@pytest.mark.parametrize(
    "first_module",
    [
        "masterclaw.pipelines.state_decision",
        "masterclaw.classifiers.executor",
        "masterclaw.app.state_dispatch_service",
    ],
)
def test_fresh_process_imports_work_in_each_dependency_order(first_module):
    code = (
        f"import {first_module}; import masterclaw.app.message_handler; "
        "import masterclaw.runtime.composition; import masterclaw.cli"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr
