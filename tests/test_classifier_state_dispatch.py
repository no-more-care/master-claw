import asyncio
import json
import uuid
from pathlib import Path

import pytest

from masterclaw.app.message_handler import MessageApplication
from masterclaw.app.scenarios import SCENARIOS, CommandId, ScenarioId
from masterclaw.app.state_dispatch_classifier import StateDispatchClassifier
from masterclaw.app.state_dispatch_service import StateDispatchDecisionService
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
from masterclaw.classifiers.executor import SemanticClassifierExecutor
from masterclaw.classifiers.policy import ClassifierConfig, ClassifierUseCase
from masterclaw.context.assembler import AssembledContext, ContextAssembler
from masterclaw.domain.models import IncomingMessage
from masterclaw.pipelines.base import CompletionResult
from masterclaw.pipelines.state_decision import StateDecisionRouter
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


def make_service(tmp_path, *, completion=None, classifier=None, config=None, store=None):
    store = store or SQLiteStore(tmp_path / "decisions.sqlite3")
    store.initialize()
    config = config or ClassifierConfig(mode="shadow")
    adapter = (
        None
        if classifier is None
        else StateDispatchClassifier(
            SemanticClassifierExecutor(classifier, requested_model=config.model),
            config.for_use_case(ClassifierUseCase.STATE_DISPATCH),
        )
    )
    return StateDispatchDecisionService(
        store=store,
        baseline=StateDecisionRouter(completion or Completion()),
        classifier=adapter,
    )


async def decide(service, *, scenario=ScenarioId.WORLD_SELECTION, content="message", context=None):
    return await service.decide(
        message=IncomingMessage.now(
            event_id=uuid.uuid4().hex,
            channel_id="channel",
            author_id="player",
            content=content,
        ),
        scenario=SCENARIOS[scenario],
        context=context or AssembledContext("", "", (), 0),
        game_id=None,
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
    choice, confidence, error, outcome, caplog, tmp_path
):
    class Sink:
        records = []

        def record_stage_spans(self, records):
            self.records.extend(records)

    sink = Sink()
    binding = bind_trace(sink, trace_id="test")
    classifier = Classifier(choice, confidence, error)
    service = make_service(
        tmp_path,
        classifier=classifier,
    )
    try:
        result = asyncio.run(
            decide(
                service,
                content="raw player secret",
                context=AssembledContext("", "hidden secret", (), 0),
            )
        )
    finally:
        reset_trace(binding)
    assert result.command is CommandId.CLARIFY
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


def test_explicit_only_is_blocked_even_in_shadow_metrics(tmp_path):
    class Sink:
        records = []

        def record_stage_spans(self, records):
            self.records.extend(records)

    sink = Sink()
    binding = bind_trace(sink, trace_id="test")
    service = make_service(
        tmp_path,
        classifier=Classifier("confirm_world"),
    )
    try:
        result = asyncio.run(
            decide(
                service,
                scenario=ScenarioId.WORLD_EDITING_REVIEW,
                content="looks fine",
                context=AssembledContext("", "", (), 0),
            )
        )
    finally:
        reset_trace(binding)
    assert result.command is CommandId.CLARIFY
    records = [record for record in sink.records if record["stage"] == "classifier.state_dispatch"]
    assert records[-1]["attributes"]["outcome"] == "blocked"


def test_shadow_does_not_replace_free_form_argument(tmp_path):
    service = make_service(
        tmp_path,
        completion=Completion("select_world", "The original world"),
        classifier=Classifier(),
    )
    result = asyncio.run(
        decide(
            service,
            content="choose world",
            context=AssembledContext("", "", (), 0),
        )
    )
    assert result.command is CommandId.SELECT_WORLD
    assert result.argument == "The original world"


def test_timeout_falls_back_but_external_cancellation_propagates(tmp_path):
    class SlowClassifier:
        async def classify(self, request):
            await asyncio.sleep(60)

    class CancelledClassifier:
        async def classify(self, request):
            raise asyncio.CancelledError

    async def run(classifier):
        service = make_service(
            tmp_path,
            classifier=classifier,
            config=ClassifierConfig(mode="shadow", timeout_seconds=0.001),
        )
        return await decide(service)

    assert asyncio.run(run(SlowClassifier())).command is CommandId.CLARIFY
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
        state_decisions=make_service(
            tmp_path, completion=completion, classifier=classifier, store=store
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


def test_off_mode_does_not_call_classifier(tmp_path):
    classifier = Classifier()
    service = make_service(tmp_path, classifier=classifier, config=ClassifierConfig())
    asyncio.run(decide(service))
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
        service = make_service(tmp_path, classifier=classifier, store=store)
        asyncio.run(
            decide(
                service,
                content="secret fresh message",
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
    assert set(observation["answers"]["command"]["probabilities"]) == set(
        classifier.requests[0].questions["command"].criteria
    )
    assert observation["answers"]["command"]["confidence"] == 0.99
    assert observation["latency_ms"] >= 0
    assert observation["reference"] == {"command": "clarify"}
    assert observation["use_case"] == "state_dispatch"
    assert observation["scope"] == "world_selection"
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
def test_shadow_observations_preserve_error_category(kind, tmp_path):
    class Sink:
        records = []

        def record_stage_spans(self, records):
            self.records.extend(records)

    sink = Sink()
    binding = bind_trace(sink, trace_id="errors")
    try:
        service = make_service(
            tmp_path,
            classifier=Classifier(error=kind("secret exception body")),
        )
        result = asyncio.run(decide(service))
    finally:
        reset_trace(binding)
    assert result.command is CommandId.CLARIFY
    record = next(row for row in sink.records if row["stage"] == "classifier.state_dispatch")
    assert record["attributes"]["error_category"] == kind.category.value
    assert record["attributes"]["error_transient"] is kind.transient
    assert "secret" not in json.dumps(record)
