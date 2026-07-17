import asyncio

import pytest
from pydantic import ValidationError

from masterclaw.app.scenarios import SCENARIOS, CommandId, ScenarioId
from masterclaw.context.assembler import AssembledContext
from masterclaw.pipelines.base import CompletionResult
from masterclaw.pipelines.state_decision import (
    command_of,
    create_state_decision_pipeline,
    state_decision_type,
)


def test_state_decision_schema_contains_only_scenario_commands() -> None:
    output_type = state_decision_type(ScenarioId.WORLD_EDITING_REVIEW)
    schema = output_type.model_json_schema()
    allowed = set(schema["properties"]["command"]["enum"])
    assert allowed == {
        command.value for command in SCENARIOS[ScenarioId.WORLD_EDITING_REVIEW].llm_commands
    }
    assert CommandId.SHOW_WORLD_CATALOG.value not in allowed


def test_state_decision_rejects_command_from_another_scenario() -> None:
    output_type = state_decision_type(ScenarioId.WORLD_EDITING_REVIEW)
    with pytest.raises(ValidationError):
        output_type.model_validate(
            {
                "command": CommandId.SHOW_WORLD_CATALOG.value,
                "argument": None,
                "confidence": 1,
                "evidence": "catalogue request",
            }
        )


def test_scenario_pipeline_returns_typed_command_without_player_answer() -> None:
    class Completion:
        async def complete(self, **kwargs) -> CompletionResult:
            assert "do not answer the player" in kwargs["system"]
            assert kwargs["tool_name"] == "submit_play_decision"
            return CompletionResult(
                '{"command":"show_character_sheet","argument":null,'
                '"confidence":0.95,"evidence":"asks for their sheet"}',
                used_tool=True,
            )

    decision = asyncio.run(
        create_state_decision_pipeline(Completion(), SCENARIOS[ScenarioId.PLAY]).run(
            task="Choose the command for: show me my character",
            context=AssembledContext("", "", (), 0),
        )
    )
    assert command_of(decision) is CommandId.SHOW_CHARACTER_SHEET
