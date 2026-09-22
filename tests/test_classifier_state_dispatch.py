import asyncio
import json
from pathlib import Path

import pytest

from masterclaw.app.message_handler import MessageApplication
from masterclaw.app.scenarios import SCENARIOS, CommandId, ScenarioId
from masterclaw.classifiers.base import (
    ClassificationResponse,
    ClassifierAuthenticationError,
    ClassifierConfigurationError,
    ClassifierError,
    ClassifierNetworkError,
    ClassifierRateLimitError,
    ClassifierRequestError,
    ClassifierResponseError,
    ClassifierServerError,
    ClassifierTimeoutError,
)
from masterclaw.classifiers.policy import ClassifierConfig
from masterclaw.context.assembler import AssembledContext, ContextAssembler
from masterclaw.domain.models import IncomingMessage
from masterclaw.pipelines.base import CompletionResult
from masterclaw.pipelines.state_decision import StateDecisionRouter, command_of
from masterclaw.storage.sqlite import SQLiteStore
from masterclaw.telemetry import bind_trace, reset_trace


class Completion:
    def __init__(self, command="clarify", argument=None):
        self.command = command
        self.argument = argument
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        return CompletionResult(
            json.dumps(
                {
                    "command": self.command,
                    "argument": self.argument,
                    "confidence": 0.99,
                    "evidence": "current router",
                }
            ),
            used_tool=True,
        )


class Classifier:
    def __init__(self, choice="show_help", confidence=0.99, error=None):
        self.choice = choice
        self.confidence = confidence
        self.error = error
        self.requests = []

    async def classify(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        labels = request.questions["command"].criteria
        return ClassificationResponse(
            request_key=request.request_key,
            taxonomy_version=request.taxonomy_version,
            provider="test",
            model="test",
            answers={
                "command": {
                    "type": "choice",
                    "choice": self.choice,
                    "confidence": self.confidence,
                    "probabilities": {
                        label: self.confidence
                        if label == self.choice
                        else (1 - self.confidence) / (len(labels) - 1)
                        for label in labels
                    },
                }
            },
        )


@pytest.mark.parametrize(
    ("choice", "confidence", "error", "outcome"),
    [
        ("show_help", 0.99, None, "eligible"),
        ("show_help", 0.5, None, "uncertain"),
        ("select_world", 0.99, None, "eligible"),
        ("show_help", 0.99, ClassifierError("secret body"), "error"),
        ("alien", 0.99, None, "error"),
    ],
)
def test_shadow_preserves_router_and_sanitizes_telemetry(
    choice, confidence, error, outcome, caplog
):
    class Sink:
        records = []

        def record_stage_spans(self, records):
            self.records.extend(records)

    sink = Sink()
    binding = bind_trace(sink, trace_id="test")
    classifier = Classifier(choice, confidence, error)
    router = StateDecisionRouter(
        Completion(),
        classifier=classifier,
        classifier_config=ClassifierConfig(mode="shadow"),
    )
    try:
        result = asyncio.run(
            router.pipeline_for(SCENARIOS[ScenarioId.WORLD_SELECTION]).run(
                task="raw player secret",
                context=AssembledContext("", "hidden secret", (), 0),
            )
        )
    finally:
        reset_trace(binding)
    assert command_of(result) is CommandId.CLARIFY
    assert result.argument is None
    request = classifier.requests[0]
    assert request.state["message"] == "raw player secret"
    assert "hidden secret" not in request.model_dump_json()
    assert set(request.questions["command"].criteria) == {
        command.value for command in SCENARIOS[ScenarioId.WORLD_SELECTION].llm_commands
    }
    records = [record for record in sink.records if record["stage"] == "classifier.state_dispatch"]
    assert records[-1]["attributes"]["outcome"] == outcome
    assert records[-1]["status"] == "ok"
    for secret in ("raw player secret", "hidden secret", "secret body"):
        assert secret not in json.dumps(records)
        assert secret not in caplog.text


def test_explicit_only_is_blocked_even_in_shadow_metrics():
    class Sink:
        records = []

        def record_stage_spans(self, records):
            self.records.extend(records)

    sink = Sink()
    binding = bind_trace(sink, trace_id="test")
    router = StateDecisionRouter(
        Completion(),
        classifier=Classifier("confirm_world"),
        classifier_config=ClassifierConfig(mode="shadow"),
    )
    try:
        result = asyncio.run(
            router.pipeline_for(SCENARIOS[ScenarioId.WORLD_EDITING_REVIEW]).run(
                task="looks fine",
                context=AssembledContext("", "", (), 0),
            )
        )
    finally:
        reset_trace(binding)
    assert command_of(result) is CommandId.CLARIFY
    records = [record for record in sink.records if record["stage"] == "classifier.state_dispatch"]
    assert records[-1]["attributes"]["outcome"] == "blocked"


def test_shadow_does_not_replace_free_form_argument():
    router = StateDecisionRouter(
        Completion("select_world", "The original world"),
        classifier=Classifier(),
        classifier_config=ClassifierConfig(mode="shadow"),
    )
    result = asyncio.run(
        router.pipeline_for(SCENARIOS[ScenarioId.WORLD_SELECTION]).run(
            task="choose world",
            context=AssembledContext("", "", (), 0),
        )
    )
    assert command_of(result) is CommandId.SELECT_WORLD
    assert result.argument == "The original world"


def test_timeout_falls_back_but_external_cancellation_propagates():
    class SlowClassifier:
        async def classify(self, request):
            await asyncio.sleep(60)

    class CancelledClassifier:
        async def classify(self, request):
            raise asyncio.CancelledError

    async def run(classifier):
        router = StateDecisionRouter(
            Completion(),
            classifier=classifier,
            classifier_config=ClassifierConfig(mode="shadow", timeout_seconds=0.001),
        )
        return await router.pipeline_for(SCENARIOS[ScenarioId.WORLD_SELECTION]).run(
            task="message",
            context=AssembledContext("", "", (), 0),
        )

    assert command_of(asyncio.run(run(SlowClassifier()))) is CommandId.CLARIFY
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run(CancelledClassifier()))


def test_dispatch_replay_skips_both_models_and_structural_commands_bypass_shadow(tmp_path):
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    classifier = Classifier()
    completion = Completion()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            completion,
            classifier=classifier,
            classifier_config=ClassifierConfig(mode="shadow"),
        ),
    )
    message = IncomingMessage.now(
        event_id="event",
        channel_id="channel",
        author_id="player",
        content="hmmm mysterious",
    )
    first = asyncio.run(app(message))
    assert asyncio.run(app(message)) == first
    assert completion.calls == len(classifier.requests) == 1
    asyncio.run(
        app(
            IncomingMessage.now(
                event_id="help",
                channel_id="channel",
                author_id="player",
                content="/help",
            )
        )
    )
    assert completion.calls == len(classifier.requests) == 1


def test_off_mode_does_not_call_classifier():
    classifier = Classifier()
    router = StateDecisionRouter(Completion(), classifier=classifier)
    asyncio.run(
        router.pipeline_for(SCENARIOS[ScenarioId.WORLD_SELECTION]).run(
            task="message",
            context=AssembledContext("", "", (), 0),
        )
    )
    assert classifier.requests == []


def test_calibration_metadata_and_distributions_are_durable_without_private_state(tmp_path):
    class MetadataClassifier(Classifier):
        async def classify(self, request):
            response = await super().classify(request)
            return response.model_copy(
                update={
                    "model": "typesafe/jev-1.13",
                    "upstream_provider": "TypeSafe",
                    "version": "1.13",
                    "request_id": "req-123",
                    "cost": 0.001,
                    "usage": {
                        "input_tokens": 123,
                        "cost": 0.001,
                        "user_id": "secret-user",
                        "input_tokens_details": {"cached_tokens": 10, "body": "secret body"},
                    },
                }
            )

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    binding = bind_trace(store, trace_id="classifier-test")
    classifier = MetadataClassifier()
    try:
        router = StateDecisionRouter(
            Completion(),
            classifier=classifier,
            classifier_config=ClassifierConfig(mode="shadow"),
        )
        asyncio.run(
            router.pipeline_for(SCENARIOS[ScenarioId.WORLD_SELECTION]).run(
                task="secret fresh message",
                context=AssembledContext("", "secret context", (), 0),
            )
        )
    finally:
        reset_trace(binding)
    with store.connect() as connection:
        row = connection.execute(
            "SELECT attributes_json FROM stage_spans WHERE stage = 'classifier.state_dispatch'"
        ).fetchone()
    observation = json.loads(row["attributes_json"])
    assert observation["requested_model"] == "~typesafe/jev-latest"
    assert observation["resolved_model"] == "typesafe/jev-1.13"
    assert observation["version"] == "1.13"
    assert observation["provider"] == "test"
    assert observation["upstream_provider"] == "TypeSafe"
    assert observation["request_id"] == "req-123"
    assert observation["usage"] == {
        "input_tokens": 123,
        "cost": 0.001,
        "input_tokens_details": {"cached_tokens": 10},
    }
    assert observation["cost"] == 0.001
    assert set(observation["probabilities"]) == set(
        classifier.requests[0].questions["command"].criteria
    )
    assert observation["confidence"] == 0.99
    assert observation["latency_ms"] >= 0
    assert observation["baseline_command"] == "clarify"
    assert observation["error_category"] is None
    assert "secret" not in row["attributes_json"]
    assert "user_id" not in row["attributes_json"]


@pytest.mark.parametrize(
    "kind",
    [
        ClassifierAuthenticationError,
        ClassifierConfigurationError,
        ClassifierRequestError,
        ClassifierResponseError,
        ClassifierRateLimitError,
        ClassifierServerError,
        ClassifierTimeoutError,
        ClassifierNetworkError,
    ],
)
def test_shadow_observations_preserve_error_category(kind):
    class Sink:
        records = []

        def record_stage_spans(self, records):
            self.records.extend(records)

    sink = Sink()
    binding = bind_trace(sink, trace_id="errors")
    try:
        router = StateDecisionRouter(
            Completion(),
            classifier=Classifier(error=kind("secret exception body")),
            classifier_config=ClassifierConfig(mode="shadow"),
        )
        result = asyncio.run(
            router.pipeline_for(SCENARIOS[ScenarioId.WORLD_SELECTION]).run(
                task="message",
                context=AssembledContext("", "", (), 0),
            )
        )
    finally:
        reset_trace(binding)
    assert command_of(result) is CommandId.CLARIFY
    record = next(row for row in sink.records if row["stage"] == "classifier.state_dispatch")
    assert record["attributes"]["error_category"] == kind.category.value
    assert record["attributes"]["error_transient"] is kind.transient
    assert "secret" not in json.dumps(record)
