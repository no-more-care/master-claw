import asyncio

import pytest

from masterclaw.context.assembler import AssembledContext
from masterclaw.pipelines.action import ActionResolution, create_action_pipeline
from masterclaw.pipelines.base import CompletionResult, PipelineValidationError


class FakeCompletion:
    def __init__(self, response: str) -> None:
        self.response = response

    async def complete(self, **kwargs) -> CompletionResult:
        return CompletionResult(self.response, used_tool=True)


CONTEXT = AssembledContext("", "{}", (), 1)


def test_action_pipeline_returns_typed_roll_proposal() -> None:
    result = asyncio.run(
        create_action_pipeline(
            FakeCompletion(
                '{"resolution":"roll","trait_names":["Body"],"aspect_names":[],"flag":null,'
                '"bonus_ids":["door-edge"],"difficulty":2,"evidence":["closed door"],'
                '"clarification_question":null}'
            )
        ).run(task="Open the door", context=CONTEXT)
    )
    assert result.resolution is ActionResolution.ROLL
    assert result.difficulty == 2
    assert result.bonus_ids == ["door-edge"]


def test_roll_without_traits_fails_closed() -> None:
    pipeline = create_action_pipeline(
        FakeCompletion(
            '{"resolution":"roll","trait_names":[],"aspect_names":[],"flag":null,'
            '"bonus_ids":[],"difficulty":2,"evidence":[],"clarification_question":null}'
        )
    )
    with pytest.raises(PipelineValidationError):
        asyncio.run(pipeline.run(task="Act", context=CONTEXT))


def test_automatic_resolution_cannot_consume_temporary_bonus() -> None:
    pipeline = create_action_pipeline(
        FakeCompletion(
            '{"resolution":"automatic","trait_names":[],"aspect_names":[],"flag":null,'
            '"bonus_ids":["door-edge"],"difficulty":null,"evidence":[],"'
            '"clarification_question":null}'
        )
    )
    with pytest.raises(PipelineValidationError):
        asyncio.run(pipeline.run(task="Act", context=CONTEXT))
