import asyncio
import json

import pytest

from masterclaw.app.scenarios import SCENARIOS, CommandId, ScenarioId
from masterclaw.context.assembler import AssembledContext
from masterclaw.pipelines.action import ActionInterpretation, create_action_pipeline
from masterclaw.pipelines.base import (
    CompletionResult,
    PipelineValidationError,
    strict_output_schema,
)
from masterclaw.pipelines.state_decision import command_of, create_state_decision_pipeline


class FakeCompletion:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.calls: list[tuple[str, str]] = []
        self.output_budgets: list[int | None] = []

    async def complete(self, **kwargs) -> CompletionResult:
        self.calls.append((kwargs["system"], kwargs["task"]))
        self.output_budgets.append(kwargs["max_output_tokens"])
        return CompletionResult(next(self.responses), used_tool=True)


def context(value: str = "{}", *, output_token_budget: int | None = None) -> AssembledContext:
    return AssembledContext("", value, (), 1, output_token_budget=output_token_budget)


def test_pipeline_parses_typed_output_without_agent_loop() -> None:
    fake = FakeCompletion(
        [
            '{"command":"declare_action","argument":null,'
            '"confidence":0.9,"evidence":"I open the door"}'
        ]
    )
    result = asyncio.run(
        create_state_decision_pipeline(fake, SCENARIOS[ScenarioId.PLAY]).run(
            task="Classify: I open the door", context=context('{"mode":"play"}')
        )
    )
    assert command_of(result) is CommandId.DECLARE_ACTION
    assert len(fake.calls) == 1


def test_pipeline_repairs_invalid_output_once() -> None:
    fake = FakeCompletion(
        [
            "not json",
            '{"command":"clarify","argument":null,'
            '"confidence":0.2,"evidence":"insufficient context"}',
        ]
    )
    result = asyncio.run(
        create_state_decision_pipeline(fake, SCENARIOS[ScenarioId.PLAY]).run(
            task="Classify", context=context(output_token_budget=321)
        )
    )
    assert command_of(result) is CommandId.CLARIFY
    assert len(fake.calls) == 2
    assert fake.output_budgets == [321, 321]


def test_pipeline_fails_closed_after_one_repair() -> None:
    fake = FakeCompletion(["bad", "still bad"])
    with pytest.raises(PipelineValidationError):
        asyncio.run(
            create_state_decision_pipeline(fake, SCENARIOS[ScenarioId.PLAY]).run(
                task="Classify", context=context()
            )
        )
    assert len(fake.calls) == 2


def test_strict_schema_preserves_pydantic_required_and_default_semantics() -> None:
    schema = strict_output_schema(ActionInterpretation)
    assert set(schema["required"]) == {"resolution", "evidence"}
    assert schema["additionalProperties"] is False
    assert "default" not in json.dumps(schema)


class TransportCompletion:
    def __init__(self, *, used_tool: bool) -> None:
        self.used_tool = used_tool

    async def complete(self, **kwargs) -> CompletionResult:
        return CompletionResult(
            '{"resolution":"automatic","trait_names":[],"aspect_names":[],'
            '"flag":null,"bonus_ids":[],"difficulty":null,'
            '"evidence":["The door is already open"],"clarification_question":null}',
            used_tool=self.used_tool,
        )


def test_prompt_json_and_native_tool_payloads_have_equivalent_typed_semantics() -> None:
    prompt_json = asyncio.run(
        create_action_pipeline(TransportCompletion(used_tool=False)).run(
            task="Walk through the open door",
            context=context(),
        )
    )
    native_tool = asyncio.run(
        create_action_pipeline(TransportCompletion(used_tool=True)).run(
            task="Walk through the open door",
            context=context(),
        )
    )
    assert prompt_json == native_tool


def test_validation_exception_never_echoes_malformed_secret_output() -> None:
    secret = "THE HIDDEN BELL KEEPER IS THE STORM"
    candidate = json.dumps(
        {
            "command": secret,
            "argument": None,
            "confidence": 1,
            "evidence": secret,
        }
    )
    fake = FakeCompletion([candidate, candidate])
    with pytest.raises(PipelineValidationError) as raised:
        asyncio.run(
            create_state_decision_pipeline(fake, SCENARIOS[ScenarioId.PLAY]).run(
                task="Classify",
                context=context(),
            )
        )
    assert secret not in str(raised.value)
    assert secret not in repr(raised.value)
