import asyncio
import threading
import time
import traceback
from types import SimpleNamespace

import pytest

from masterclaw.adapters.openhands import (
    DeterministicProviderError,
    OpenHandsCompletionPort,
    OpenHandsLLMRegistry,
)
from masterclaw.app.scenarios import ScenarioId
from masterclaw.config import ModelConfig, ModelRole, OutputTransport, Settings
from masterclaw.pipelines.base import (
    CompletionResult,
    FallbackCompletionPort,
    TransientProviderError,
)
from masterclaw.pipelines.state_decision import state_decision_type


def settings() -> Settings:
    model = ModelConfig(model="openrouter/openai/gpt-4.1-mini", max_output_tokens=256)
    return Settings(
        discord_token="test-discord-token",
        openrouter_api_key="test-openrouter-key",
        state_model=model,
        reasoning_model=model,
        narrative_model=model,
    )


def test_registry_constructs_installed_openhands_llm() -> None:
    llm = OpenHandsLLMRegistry(settings()).create(ModelRole.STATE)
    assert llm.model == "openrouter/openai/gpt-4.1-mini"
    assert llm.openrouter_app_name == "MasterClaw"
    assert llm.max_output_tokens == 256
    assert llm.reasoning_effort == "low"


@pytest.mark.asyncio
async def test_completion_adapter_uses_real_sdk_message_contract() -> None:
    from openhands.sdk import Message, TextContent

    class FakeLLM:
        model = "openrouter/test/model"
        metrics = SimpleNamespace(accumulated_cost=0.0)
        effective_max_output_tokens = 256

        def completion(self, messages, tools, **kwargs):
            assert all(isinstance(message, Message) for message in messages)
            assert isinstance(messages[0].content[0], TextContent)
            assert messages[0].content[0].cache_prompt is True
            assert len(messages) == 3
            assert "DYNAMIC CONTEXT" in messages[1].content[0].text
            assert "CURRENT TASK" in messages[2].content[0].text
            schema = tools[0].to_openai_tool()["function"]
            assert schema["strict"] is True
            assert schema["parameters"]["additionalProperties"] is False
            assert kwargs["parallel_tool_calls"] is False
            assert kwargs["max_completion_tokens"] == 256
            from openhands.sdk.llm import MessageToolCall

            usage = SimpleNamespace(
                prompt_tokens=12,
                completion_tokens=3,
                prompt_tokens_details=SimpleNamespace(cached_tokens=4),
                completion_tokens_details=SimpleNamespace(reasoning_tokens=1),
            )
            return SimpleNamespace(
                id="response-1",
                message=Message(
                    role="assistant",
                    content=[],
                    tool_calls=[
                        MessageToolCall(
                            id="call-1",
                            name="submit_play_decision",
                            arguments=(
                                '{"command":"declare_action","argument":null,"confidence":0.9,'
                                '"evidence":"contract works"}'
                            ),
                            origin="completion",
                        )
                    ],
                ),
                raw_response=SimpleNamespace(usage=usage),
                metrics=SimpleNamespace(accumulated_cost=0.001),
            )

    class FakeRegistry:
        def create(self, role):
            assert role is ModelRole.STATE
            return FakeLLM()

    port = OpenHandsCompletionPort(FakeRegistry(), ModelRole.STATE)
    result = await port.complete(
        system="system",
        context="context",
        task="task",
        output_type=state_decision_type(ScenarioId.PLAY),
        tool_name="submit_play_decision",
        max_output_tokens=500,
    )
    assert result.used_tool is True
    assert "contract works" in result.payload


def test_prompt_json_transport_is_explicit_fallback() -> None:
    assert OutputTransport.PROMPT_JSON.value == "prompt_json"


@pytest.mark.asyncio
async def test_provider_retries_use_backoff_and_raise_typed_transient_error(monkeypatch) -> None:
    class FailingLLM:
        model = "openrouter/test/model"
        metrics = SimpleNamespace(accumulated_cost=0.0)

        def completion(self, messages, tools, **kwargs):
            raise RuntimeError(
                "provider unavailable Authorization: Bearer super-secret-transient-token"
            )

    class FakeRegistry:
        def create(self, role):
            return FailingLLM()

    recorded = {}

    class RecordingStore:
        def record_llm_call(self, **kwargs) -> None:
            recorded.update(kwargs)

    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    port = OpenHandsCompletionPort(
        FakeRegistry(),
        ModelRole.STATE,
        RecordingStore(),
        retry_delays=(5.0, 30.0),
    )
    with pytest.raises(TransientProviderError) as caught:
        await port.complete(
            system="system",
            context="context",
            task="task",
            output_type=state_decision_type(ScenarioId.PLAY),
            tool_name="submit_play_decision",
        )
    assert delays == [5.0, 30.0]
    assert caught.value.__cause__ is None
    assert "super-secret-transient-token" not in str(caught.value)
    assert "super-secret-transient-token" not in recorded["error"]
    assert "super-secret-transient-token" not in "".join(
        traceback.format_exception(caught.type, caught.value, caught.tb)
    )


@pytest.mark.asyncio
async def test_deterministic_provider_error_is_not_retried(monkeypatch) -> None:
    from openhands.sdk.llm.exceptions import LLMBadRequestError

    class FailingLLM:
        model = "openrouter/test/model"
        metrics = SimpleNamespace(accumulated_cost=0.0)

        def __init__(self) -> None:
            self.calls = 0

        def completion(self, messages, tools, **kwargs):
            self.calls += 1
            raise LLMBadRequestError(
                "unsupported tool_choice Authorization: Bearer super-secret-provider-token"
            )

    llm = FailingLLM()

    class FakeRegistry:
        def create(self, role):
            return llm

    recorded = {}

    class RecordingStore:
        def record_llm_call(self, **kwargs) -> None:
            recorded.update(kwargs)

    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    port = OpenHandsCompletionPort(
        FakeRegistry(),
        ModelRole.STATE,
        RecordingStore(),
        retry_delays=(5.0, 30.0),
    )
    with pytest.raises(DeterministicProviderError) as caught:
        await port.complete(
            system="system",
            context="context",
            task="task",
            output_type=state_decision_type(ScenarioId.PLAY),
            tool_name="submit_play_decision",
        )
    assert llm.calls == 1
    assert delays == []
    assert caught.value.__cause__ is None
    assert "super-secret-provider-token" not in str(caught.value)
    assert "super-secret-provider-token" not in recorded["error"]
    assert "super-secret-provider-token" not in "".join(
        traceback.format_exception(caught.type, caught.value, caught.tb)
    )


@pytest.mark.asyncio
async def test_deterministic_provider_failure_still_uses_configured_fallback() -> None:
    from openhands.sdk.llm.exceptions import LLMBadRequestError

    class FailingLLM:
        model = "openrouter/test/model"
        metrics = SimpleNamespace(accumulated_cost=0.0)

        def completion(self, messages, tools, **kwargs):
            raise LLMBadRequestError("unsupported request")

    class FakeRegistry:
        def create(self, role):
            return FailingLLM()

    class Secondary:
        calls = 0

        async def complete(self, **kwargs) -> CompletionResult:
            self.calls += 1
            return CompletionResult(
                '{"command":"declare_action","argument":null,'
                '"confidence":0.9,"evidence":"fallback"}',
                used_tool=False,
            )

    secondary = Secondary()
    fallback = FallbackCompletionPort(
        OpenHandsCompletionPort(FakeRegistry(), ModelRole.STATE), secondary
    )
    result = await fallback.complete(
        system="system",
        context="context",
        task="task",
        output_type=state_decision_type(ScenarioId.PLAY),
        tool_name="submit_play_decision",
    )

    assert secondary.calls == 1
    assert "fallback" in result.payload


@pytest.mark.asyncio
async def test_registry_limiter_bounds_concurrent_provider_calls() -> None:
    from openhands.sdk import Message, TextContent

    state_lock = threading.Lock()
    active = 0
    maximum_active = 0

    class SlowLLM:
        model = "openrouter/test/model"
        metrics = SimpleNamespace(accumulated_cost=0.0)

        def completion(self, messages, tools, **kwargs):
            nonlocal active, maximum_active
            with state_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                time.sleep(0.04)
                return SimpleNamespace(
                    id="response",
                    message=Message(
                        role="assistant",
                        content=[
                            TextContent(
                                text=(
                                    '{"command":"declare_action","argument":null,'
                                    '"confidence":0.9,"evidence":"bounded"}'
                                )
                            )
                        ],
                    ),
                    raw_response=SimpleNamespace(usage=None),
                    metrics=SimpleNamespace(accumulated_cost=0.0),
                )
            finally:
                with state_lock:
                    active -= 1

    llm = SlowLLM()

    class LimitedRegistry(OpenHandsLLMRegistry):
        def create(self, role, model_config=None):
            return llm

    limited_settings = settings().model_copy(update={"llm_max_concurrency": 2})
    registry = LimitedRegistry(limited_settings)
    ports = [OpenHandsCompletionPort(registry, ModelRole.STATE) for _ in range(6)]
    await asyncio.gather(
        *(
            port.complete(
                system="system",
                context="context",
                task="task",
                output_type=state_decision_type(ScenarioId.PLAY),
                tool_name="submit_play_decision",
            )
            for port in ports
        )
    )
    assert maximum_active == 2


@pytest.mark.asyncio
async def test_one_port_serializes_shared_llm_and_metrics_state() -> None:
    from openhands.sdk import Message, TextContent

    state_lock = threading.Lock()
    active = 0
    maximum_active = 0

    class SlowLLM:
        model = "openrouter/test/model"
        metrics = SimpleNamespace(accumulated_cost=0.0)

        def completion(self, messages, tools, **kwargs):
            nonlocal active, maximum_active
            with state_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                time.sleep(0.03)
                return SimpleNamespace(
                    id="response",
                    message=Message(
                        role="assistant",
                        content=[
                            TextContent(
                                text=(
                                    '{"command":"declare_action","argument":null,'
                                    '"confidence":0.9,"evidence":"serialized"}'
                                )
                            )
                        ],
                    ),
                    raw_response=SimpleNamespace(usage=None),
                    metrics=SimpleNamespace(accumulated_cost=0.0),
                )
            finally:
                with state_lock:
                    active -= 1

    llm = SlowLLM()

    class FakeRegistry:
        def create(self, role):
            return llm

    port = OpenHandsCompletionPort(
        FakeRegistry(), ModelRole.STATE, output_transport=OutputTransport.PROMPT_JSON
    )
    await asyncio.gather(
        *(
            port.complete(
                system="system",
                context="context",
                task="task",
                output_type=state_decision_type(ScenarioId.PLAY),
                tool_name="submit_play_decision",
            )
            for _ in range(4)
        )
    )

    assert maximum_active == 1


@pytest.mark.asyncio
async def test_provider_is_attempted_once_by_default() -> None:
    class FailingLLM:
        model = "openrouter/test/model"
        metrics = SimpleNamespace(accumulated_cost=0.0)

        def __init__(self) -> None:
            self.calls = 0

        def completion(self, messages, tools, **kwargs):
            self.calls += 1
            raise RuntimeError("provider unavailable")

    llm = FailingLLM()

    class FakeRegistry:
        def create(self, role):
            return llm

    port = OpenHandsCompletionPort(FakeRegistry(), ModelRole.STATE)
    with pytest.raises(TransientProviderError):
        await port.complete(
            system="system",
            context="context",
            task="task",
            output_type=state_decision_type(ScenarioId.PLAY),
            tool_name="submit_play_decision",
        )
    assert llm.calls == 1
