from __future__ import annotations

import logging
import time

from pydantic import SecretStr

from masterclaw.config import ModelRole, Settings
from masterclaw.storage.sqlite import SQLiteStore
from masterclaw.telemetry import current_game_id

logger = logging.getLogger(__name__)


class OpenHandsLLMRegistry:
    """Creates isolated OpenHands LLM instances for stable application roles."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def create(self, role: ModelRole):  # Return type follows the installed SDK version.
        from openhands.sdk import LLM

        config = self._settings.model_for(role)
        return LLM(
            model=config.model,
            api_key=SecretStr(self._settings.openrouter_api_key.get_secret_value()),
            temperature=config.temperature,
            max_output_tokens=config.max_output_tokens,
            timeout=config.timeout_seconds,
            num_retries=2,
            openrouter_app_name="MasterClaw",
        )


class OpenHandsCompletionPort:
    """Narrow completion adapter; pipelines receive no OpenHands tools."""

    def __init__(
        self,
        registry: OpenHandsLLMRegistry,
        role: ModelRole,
        store: SQLiteStore | None = None,
    ) -> None:
        self._llm = registry.create(role)
        self._role = role
        self._store = store

    async def complete(self, *, system: str, user: str) -> str:
        import asyncio

        from openhands.sdk import Message, TextContent

        messages = [
            Message(role="system", content=[TextContent(text=system, cache_prompt=True)]),
            Message(role="user", content=[TextContent(text=user)]),
        ]
        started = time.monotonic()
        cost_before = float(self._llm.metrics.accumulated_cost or 0)
        try:
            response = await asyncio.to_thread(self._llm.completion, messages)
        except Exception as error:
            self._record(
                latency_ms=int((time.monotonic() - started) * 1000),
                success=False,
                error=repr(error),
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
        )
        return "\n".join(
            block.text for block in response.message.content if isinstance(block, TextContent)
        )

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
    ) -> None:
        if self._store is None:
            return
        try:
            self._store.record_llm_call(
                game_id=current_game_id(),
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
            )
        except Exception:
            logger.exception("Failed to persist LLM telemetry")
