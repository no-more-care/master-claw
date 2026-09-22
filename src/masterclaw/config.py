from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from masterclaw.classifiers.policy import ClassifierConfig


class ModelRole(StrEnum):
    STATE = "state"
    REASONING = "reasoning"
    NARRATIVE = "narrative"
    WORLDGEN = "worldgen"


class OutputTransport(StrEnum):
    NATIVE_TOOL = "native_tool"
    PROMPT_JSON = "prompt_json"


class ModelConfig(BaseModel):
    model: str
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_output_tokens: int = Field(default=1200, ge=64)
    timeout_seconds: int = Field(default=60, gt=0)
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh"] = "low"
    output_transport: OutputTransport = OutputTransport.PROMPT_JSON

    @model_validator(mode="after")
    def require_openrouter_model(self) -> ModelConfig:
        if not self.model.startswith("openrouter/"):
            raise ValueError("v2 currently supports only openrouter/<provider>/<model>")
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="MASTERCLAW_", env_nested_delimiter="__"
    )

    discord_token: SecretStr
    openrouter_api_key: SecretStr
    database_path: str = "data/masterclaw.sqlite3"
    prompt_path: str = "prompts"
    discord_debounce_seconds: float = Field(default=1.5, ge=0.1, le=10)
    llm_max_concurrency: int = Field(default=4, ge=1, le=64)
    classifier: ClassifierConfig = ClassifierConfig()
    classifier_api_key: SecretStr | None = None
    state_model: ModelConfig
    state_fallback_model: ModelConfig = ModelConfig(
        model="openrouter/google/gemini-3-flash-preview",
        temperature=0,
        max_output_tokens=1200,
        timeout_seconds=120,
        reasoning_effort="low",
        output_transport=OutputTransport.PROMPT_JSON,
    )
    reasoning_model: ModelConfig
    reasoning_fallback_model: ModelConfig = ModelConfig(
        model="openrouter/google/gemini-3-flash-preview",
        temperature=0.2,
        max_output_tokens=6000,
        timeout_seconds=180,
        reasoning_effort="medium",
        output_transport=OutputTransport.PROMPT_JSON,
    )
    narrative_model: ModelConfig
    narrative_review_model: ModelConfig = ModelConfig(
        model="openrouter/openai/gpt-5.6-luna",
        temperature=0.2,
        max_output_tokens=6000,
        timeout_seconds=120,
        reasoning_effort="medium",
        output_transport=OutputTransport.PROMPT_JSON,
    )
    narrative_review_fallback_model: ModelConfig = ModelConfig(
        model="openrouter/google/gemini-3-flash-preview",
        temperature=0.2,
        max_output_tokens=6000,
        timeout_seconds=180,
        reasoning_effort="medium",
        output_transport=OutputTransport.PROMPT_JSON,
    )
    worldgen_model: ModelConfig = ModelConfig(
        model="openrouter/openai/gpt-5.6-luna",
        temperature=0.2,
        max_output_tokens=12000,
        timeout_seconds=120,
        reasoning_effort="medium",
        output_transport=OutputTransport.PROMPT_JSON,
    )
    worldgen_fallback_model: ModelConfig = ModelConfig(
        model="openrouter/google/gemini-3-flash-preview",
        temperature=0.2,
        max_output_tokens=12000,
        timeout_seconds=150,
        reasoning_effort="medium",
        output_transport=OutputTransport.PROMPT_JSON,
    )
    worldgen_creative_model: ModelConfig = ModelConfig(
        model="openrouter/deepseek/deepseek-v4-pro",
        temperature=0.9,
        max_output_tokens=6000,
        timeout_seconds=90,
        reasoning_effort="medium",
        output_transport=OutputTransport.PROMPT_JSON,
    )
    worldgen_creative_fallback_model: ModelConfig = ModelConfig(
        model="openrouter/aion-labs/aion-3.0",
        temperature=0.9,
        max_output_tokens=6000,
        timeout_seconds=120,
        reasoning_effort="medium",
        output_transport=OutputTransport.PROMPT_JSON,
    )

    def model_for(self, role: ModelRole) -> ModelConfig:
        return {
            ModelRole.STATE: self.state_model,
            ModelRole.REASONING: self.reasoning_model,
            ModelRole.NARRATIVE: self.narrative_model,
            ModelRole.WORLDGEN: self.worldgen_model,
        }[role]
