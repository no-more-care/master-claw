from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import ClassVar

from pydantic import BaseModel, SecretStr

from masterclaw.config import ModelConfig, ModelRole, OutputTransport, Settings
from masterclaw.pipelines.base import (
    CompletionResult,
    TransientProviderError,
    strict_output_schema,
)
from masterclaw.storage.sqlite import SQLiteStore
from masterclaw.telemetry import current_game_id, current_trace_fields, stage_span

logger = logging.getLogger(__name__)


class DeterministicProviderError(RuntimeError):
    """A provider rejection that requires changing configuration or request content."""


class OpenHandsLLMRegistry:
    """Creates isolated OpenHands LLM instances for stable application roles."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._completion_limiter = asyncio.BoundedSemaphore(settings.llm_max_concurrency)

    def create(
        self, role: ModelRole, model_config: ModelConfig | None = None
    ):  # Return type follows the installed SDK version.
        from openhands.sdk import LLM

        config = model_config or self._settings.model_for(role)
        return LLM(
            model=config.model,
            api_key=SecretStr(self._settings.openrouter_api_key.get_secret_value()),
            temperature=config.temperature,
            max_output_tokens=config.max_output_tokens,
            timeout=config.timeout_seconds,
            reasoning_effort=config.reasoning_effort,
            # Retries are owned by the adapter so every attempt receives its own timing span.
            num_retries=0,
            openrouter_app_name="MasterClaw",
        )

    def output_transport_for(
        self, role: ModelRole, model_config: ModelConfig | None = None
    ) -> OutputTransport:
        return (model_config or self._settings.model_for(role)).output_transport

    def completion_limiter(self) -> asyncio.BoundedSemaphore:
        """Return the process-local limiter shared by every port in this registry."""
        return self._completion_limiter


class OpenHandsCompletionPort:
    """Narrow completion adapter; pipelines receive no OpenHands tools."""

    def __init__(
        self,
        registry: OpenHandsLLMRegistry,
        role: ModelRole,
        store: SQLiteStore | None = None,
        output_transport: OutputTransport | None = None,
        model_config: ModelConfig | None = None,
        retry_delays: tuple[float, ...] = (),
    ) -> None:
        self._llm = (
            registry.create(role) if model_config is None else registry.create(role, model_config)
        )
        self._role = role
        self._store = store
        self._retry_delays = retry_delays
        self._call_lock = asyncio.Lock()
        limiter_factory = getattr(registry, "completion_limiter", None)
        self._completion_limiter = limiter_factory() if limiter_factory is not None else None
        configured_transport = getattr(registry, "output_transport_for", None)
        self._output_transport = output_transport or (
            (
                configured_transport(role)
                if model_config is None
                else configured_transport(role, model_config)
            )
            if configured_transport
            else OutputTransport.NATIVE_TOOL
        )

    async def complete(
        self,
        *,
        system: str,
        context: str,
        task: str,
        output_type: type[BaseModel],
        tool_name: str,
        max_output_tokens: int | None = None,
    ) -> CompletionResult:
        # OpenHands LLM metrics and client state are shared within one port. Keep the full
        # cost-before -> provider call -> cost-delta interval serialized for that instance.
        async with self._call_lock:
            return await self._complete_locked(
                system=system,
                context=context,
                task=task,
                output_type=output_type,
                tool_name=tool_name,
                max_output_tokens=max_output_tokens,
            )

    async def _complete_locked(
        self,
        *,
        system: str,
        context: str,
        task: str,
        output_type: type[BaseModel],
        tool_name: str,
        max_output_tokens: int | None,
    ) -> CompletionResult:
        from openhands.sdk import Message, TextContent

        messages = [
            Message(role="system", content=[TextContent(text=system, cache_prompt=True)]),
            Message(
                role="user",
                content=[TextContent(text=f"DYNAMIC CONTEXT\n{context or '(empty)'}")],
            ),
            Message(role="user", content=[TextContent(text=f"CURRENT TASK\n{task}")]),
        ]
        tools = None
        prompt_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "system": system,
                    "tool_name": tool_name,
                    "schema": strict_output_schema(output_type),
                    "transport": self._output_transport.value,
                    "max_output_tokens": max_output_tokens,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        completion_kwargs: dict[str, object] = {}
        if self._output_transport is OutputTransport.NATIVE_TOOL:
            tool = _terminal_output_tool(tool_name, output_type)
            tools = [tool]
            completion_kwargs = {
                "tool_choice": {
                    "type": "function",
                    "function": {"name": tool_name},
                },
                "parallel_tool_calls": False,
            }
        else:
            schema = strict_output_schema(output_type)
            messages.append(
                Message(
                    role="user",
                    content=[
                        TextContent(
                            text=(
                                "Return JSON only and match this schema exactly:\n"
                                f"{json.dumps(schema, ensure_ascii=False)}"
                            )
                        )
                    ],
                )
            )
        if max_output_tokens is not None:
            configured_limit = getattr(self._llm, "effective_max_output_tokens", None)
            request_limit = max_output_tokens
            if configured_limit is not None and int(configured_limit) > 0:
                request_limit = min(request_limit, int(configured_limit))
            completion_kwargs["max_completion_tokens"] = request_limit
        started = time.monotonic()
        cost_before = float(self._llm.metrics.accumulated_cost or 0)
        try:
            with stage_span(
                "llm.provider",
                component="openhands",
                operation=self._role.value,
                attributes={
                    "model": self._llm.model,
                    "transport": self._output_transport.value,
                    "retry_budget": len(self._retry_delays),
                    "max_output_tokens": max_output_tokens,
                },
            ):
                response = None
                maximum_attempts = len(self._retry_delays) + 1
                for attempt in range(1, maximum_attempts + 1):
                    try:
                        with stage_span(
                            "llm.provider_attempt",
                            component="openhands",
                            operation=self._role.value,
                            attributes={
                                "model": self._llm.model,
                                "attempt": attempt,
                                "is_retry": attempt > 1,
                            },
                        ):
                            response = await self._invoke_provider(
                                messages, tools, completion_kwargs
                            )
                        break
                    except Exception as error:
                        if _is_deterministic_provider_error(error):
                            raise DeterministicProviderError(
                                "provider rejected the request; retry requires changing it"
                            ) from None
                        if attempt == maximum_attempts:
                            raise TransientProviderError(
                                f"provider failed after {attempt} attempts"
                            ) from None
                        logger.warning(
                            "provider attempt %s failed for role %s; retrying after %.1fs",
                            attempt,
                            self._role.value,
                            self._retry_delays[attempt - 1],
                        )
                        await asyncio.sleep(self._retry_delays[attempt - 1])
                assert response is not None
        except Exception as error:
            self._record(
                latency_ms=int((time.monotonic() - started) * 1000),
                success=False,
                error=repr(error),
                prompt_fingerprint=prompt_fingerprint,
            )
            raise
        usage = getattr(response.raw_response, "usage", None)
        input_details = getattr(usage, "prompt_tokens_details", None) or getattr(
            usage, "input_tokens_details", None
        )
        self._record(
            response_id=response.id,
            prompt_tokens=self._usage_value(usage, "prompt_tokens", "input_tokens"),
            completion_tokens=self._usage_value(usage, "completion_tokens", "output_tokens"),
            cache_read_tokens=self._usage_value(input_details, "cached_tokens"),
            cache_write_tokens=self._usage_value(usage, "_cache_creation_input_tokens"),
            reasoning_tokens=self._reasoning_tokens(usage),
            latency_ms=int((time.monotonic() - started) * 1000),
            cost=max(0.0, float(response.metrics.accumulated_cost or 0) - cost_before),
            success=True,
            prompt_fingerprint=prompt_fingerprint,
        )
        text = "\n".join(
            block.text for block in response.message.content if isinstance(block, TextContent)
        )
        if self._output_transport is OutputTransport.PROMPT_JSON:
            return CompletionResult(text, used_tool=False)
        tool_calls = response.message.tool_calls or []
        if len(tool_calls) != 1:
            return CompletionResult(
                text,
                used_tool=False,
                protocol_error=(
                    f"expected exactly one {tool_name} tool call, got {len(tool_calls)}"
                ),
            )
        call = tool_calls[0]
        if call.name != tool_name:
            return CompletionResult(
                call.arguments,
                used_tool=True,
                protocol_error=f"expected tool {tool_name}, got {call.name}",
            )
        return CompletionResult(call.arguments, used_tool=True)

    async def _invoke_provider(self, messages, tools, completion_kwargs: dict[str, object]):
        if self._completion_limiter is None:
            return await asyncio.to_thread(
                self._llm.completion, messages, tools, **completion_kwargs
            )
        async with self._completion_limiter:
            return await asyncio.to_thread(
                self._llm.completion, messages, tools, **completion_kwargs
            )

    def metrics_snapshot(self) -> dict[str, int | float]:
        """Return aggregate metrics for calls made through this isolated port."""
        metrics = self._llm.metrics
        usage = metrics.accumulated_token_usage
        return {
            "calls": len(metrics.token_usages),
            "prompt_tokens": 0 if usage is None else usage.prompt_tokens,
            "completion_tokens": 0 if usage is None else usage.completion_tokens,
            "reasoning_tokens": 0 if usage is None else usage.reasoning_tokens,
            "cache_read_tokens": 0 if usage is None else usage.cache_read_tokens,
            "cost": float(metrics.accumulated_cost or 0),
        }

    @staticmethod
    def _usage_value(usage, *names: str) -> int:
        for name in names:
            value = getattr(usage, name, None)
            if value is not None:
                return int(value or 0)
        return 0

    @classmethod
    def _reasoning_tokens(cls, usage) -> int:
        details = getattr(usage, "completion_tokens_details", None) or getattr(
            usage, "output_tokens_details", None
        )
        return cls._usage_value(details, "reasoning_tokens")

    def _record(
        self,
        *,
        response_id: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
        latency_ms: int,
        cost: float = 0,
        success: bool,
        error: str | None = None,
        prompt_fingerprint: str,
    ) -> None:
        if self._store is None:
            return
        try:
            trace = current_trace_fields()
            self._store.record_llm_call(
                game_id=current_game_id(),
                trace_id=trace["trace_id"],
                channel_id=trace["channel_id"],
                event_id=trace["event_id"],
                role=self._role.value,
                model=self._llm.model,
                response_id=response_id,
                prompt_tokens=max(0, prompt_tokens),
                completion_tokens=max(0, completion_tokens),
                cache_read_tokens=max(0, cache_read_tokens),
                cache_write_tokens=max(0, cache_write_tokens),
                reasoning_tokens=max(0, reasoning_tokens),
                latency_ms=max(0, latency_ms),
                cost=max(0, cost),
                success=success,
                error=error,
                prompt_fingerprint=prompt_fingerprint,
            )
        except Exception:
            logger.exception("Failed to persist LLM telemetry")


def _is_deterministic_provider_error(error: Exception) -> bool:
    """Return true when resending the identical provider request cannot recover."""
    from openhands.sdk.llm.exceptions import (
        LLMAuthenticationError,
        LLMBadRequestError,
        LLMContextWindowExceedError,
        LLMContextWindowTooSmallError,
        LLMMalformedConversationHistoryError,
    )

    return isinstance(
        error,
        (
            LLMAuthenticationError,
            LLMBadRequestError,
            LLMContextWindowExceedError,
            LLMContextWindowTooSmallError,
            LLMMalformedConversationHistoryError,
        ),
    )


def _terminal_output_tool(tool_name: str, output_type: type[BaseModel]):
    """Build a non-executable typed tool used only to submit a pipeline result."""
    from openhands.sdk import Action, ToolDefinition

    class PlaceholderAction(Action):
        pass

    def to_openai_tool(self, add_security_risk_prediction=False, action_type=None):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "strict": True,
                "parameters": strict_output_schema(self.output_type),
            },
        }

    @classmethod
    def create(cls, *args, **kwargs):
        return [
            cls(
                description=(
                    "Submit the final result for this pipeline. Call exactly once after "
                    "reasoning; this tool does not mutate application state."
                ),
                action_type=PlaceholderAction,
            )
        ]

    class_name = "".join(part.title() for part in tool_name.split("_")) + "Tool"
    tool_type = type(
        class_name,
        (ToolDefinition,),
        {
            "__module__": __name__,
            "__annotations__": {"output_type": ClassVar[type[BaseModel]]},
            "name": tool_name,
            "output_type": output_type,
            "create": create,
            "to_openai_tool": to_openai_tool,
        },
    )
    return tool_type.create()[0]
