from __future__ import annotations

from pydantic import SecretStr

from masterclaw.config import ModelRole, Settings


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

    def __init__(self, registry: OpenHandsLLMRegistry, role: ModelRole) -> None:
        self._llm = registry.create(role)

    async def complete(self, *, system: str, user: str) -> str:
        import asyncio

        from openhands.sdk import Message, TextContent

        messages = [
            Message(role="system", content=[TextContent(text=system, cache_prompt=True)]),
            Message(role="user", content=[TextContent(text=user)]),
        ]
        response = await asyncio.to_thread(self._llm.completion, messages)
        return "\n".join(
            block.text for block in response.message.content if isinstance(block, TextContent)
        )
