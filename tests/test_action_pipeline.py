import asyncio

import pytest

from masterclaw.pipelines.action import ActionResolution, create_action_pipeline
from masterclaw.pipelines.base import PipelineValidationError


class FakeCompletion:
    def __init__(self, response: str) -> None:
        self.response = response

    async def complete(self, *, system: str, user: str) -> str:
        return self.response


def test_action_pipeline_returns_typed_roll_proposal() -> None:
    result = asyncio.run(
        create_action_pipeline(
            FakeCompletion(
                '{"resolution":"roll","trait_names":["Body"],"aspect_names":[],"flag":null,'
                '"difficulty":2,"evidence":["closed door"],"clarification_question":null}'
            )
        ).run(task="Open the door", dynamic_context="{}")
    )
    assert result.resolution is ActionResolution.ROLL
    assert result.difficulty == 2


def test_roll_without_traits_fails_closed() -> None:
    pipeline = create_action_pipeline(
        FakeCompletion(
            '{"resolution":"roll","trait_names":[],"aspect_names":[],"flag":null,'
            '"difficulty":2,"evidence":[],"clarification_question":null}'
        )
    )
    with pytest.raises(PipelineValidationError):
        asyncio.run(pipeline.run(task="Act", dynamic_context="{}"))
