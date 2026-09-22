from pathlib import Path

import pytest

from masterclaw.config import ModelConfig, Settings
from masterclaw.model_smoke import (
    ModelSmokeValidationError,
    assemble_model_smoke_contexts,
    validate_world_intake_smoke,
)
from masterclaw.pipelines.world_intake import WorldCreationBrief


def test_all_model_smoke_contexts_assemble_without_api_clients() -> None:
    model = ModelConfig(model="openrouter/test/smoke")
    settings = Settings(
        _env_file=None,
        discord_token="test-token",
        openrouter_api_key="test-key",
        prompt_path=str(Path(__file__).parents[1] / "prompts"),
        state_model=model,
        reasoning_model=model,
        narrative_model=model,
        worldgen_model=model,
    )

    contexts = assemble_model_smoke_contexts(settings)

    assert set(contexts) == {
        "state",
        "action",
        "compound",
        "reasoning",
        "narrative",
        "world_intake",
        "world_creative",
    }
    assert all(context.dynamic_context for context in contexts.values())


def smoke_world_intake(*, player_role: str | None = "storm couriers") -> WorldCreationBrief:
    specified_fields = [
        "genre",
        "tone",
        "scale",
        "locale",
        "pregenerated_character_count",
    ]
    if player_role is not None:
        specified_fields.append("player_role")
    return WorldCreationBrief(
        title="Storm Couriers",
        brief="Create a mysterious fantasy world about storm couriers in one floating city.",
        genre="fantasy",
        tone="mysterious",
        scale="one floating city",
        player_role=player_role,
        locale="en",
        pregenerated_character_count=3,
        specified_fields=specified_fields,
    )


def test_model_smoke_rejects_schema_valid_intake_that_drops_explicit_player_role() -> None:
    with pytest.raises(ModelSmokeValidationError, match="player_role"):
        validate_world_intake_smoke(smoke_world_intake(player_role=None))


def test_model_smoke_accepts_intake_with_all_explicit_settings() -> None:
    validate_world_intake_smoke(smoke_world_intake())
