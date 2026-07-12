from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelRole(StrEnum):
    STATE = "state"
    REASONING = "reasoning"
    NARRATIVE = "narrative"


class ModelConfig(BaseModel):
    model: str
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_output_tokens: int = Field(default=1200, ge=64)
    timeout_seconds: int = Field(default=60, gt=0)

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
    state_model: ModelConfig
    reasoning_model: ModelConfig
    narrative_model: ModelConfig

    def model_for(self, role: ModelRole) -> ModelConfig:
        return {
            ModelRole.STATE: self.state_model,
            ModelRole.REASONING: self.reasoning_model,
            ModelRole.NARRATIVE: self.narrative_model,
        }[role]
