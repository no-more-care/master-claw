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


def test_state_router_prompt_defines_intent_boundaries_and_confidence_rubric() -> None:
    class Completion:
        system = ""

        async def complete(self, **kwargs) -> CompletionResult:
            self.system = kwargs["system"]
            return CompletionResult(
                '{"command":"clarify","argument":null,'
                '"confidence":0.4,"evidence":"ambiguous out-of-character text"}',
                used_tool=True,
            )

    completion = Completion()
    asyncio.run(
        create_state_decision_pipeline(completion, SCENARIOS[ScenarioId.PLAY]).run(
            task="Choose the command.",
            context=AssembledContext("", "", (), 0),
        )
    )

    for boundary in (
        "declare_action",
        "player_narration",
        "show_scene",
        "ask_scene_question",
        "show_help",
        "offer_help",
        "quoted",
        "hypothetical",
        "negated",
        "out-of-character",
        "0.90-0.97",
    ):
        assert boundary in completion.system.lower()
    assert "untrusted data" in completion.system
    assert "i inspect/search/listen/examine" in completion.system.lower()
    assert "not for a first-person description" in completion.system.lower()


def test_state_decision_rejects_whitespace_only_evidence() -> None:
    output_type = state_decision_type(ScenarioId.PLAY)
    with pytest.raises(ValidationError, match="evidence cannot be blank"):
        output_type.model_validate(
            {
                "command": CommandId.CLARIFY.value,
                "argument": "   ",
                "confidence": 0.2,
                "evidence": "   ",
            }
        )
