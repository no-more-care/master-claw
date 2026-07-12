import asyncio

import pytest

from masterclaw.pipelines.base import PipelineValidationError
from masterclaw.pipelines.intent import MessageIntent, create_intent_pipeline


class FakeCompletion:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.calls: list[tuple[str, str]] = []

    async def complete(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        return next(self.responses)


def test_pipeline_parses_typed_output_without_agent_loop() -> None:
    fake = FakeCompletion(
        ['{"intent":"action_declaration","confidence":0.9,"evidence":"I open the door"}']
    )
    result = asyncio.run(
        create_intent_pipeline(fake).run(
            task="Classify: I open the door", dynamic_context='{"mode":"play"}'
        )
    )
    assert result.intent is MessageIntent.ACTION_DECLARATION
    assert len(fake.calls) == 1


def test_pipeline_repairs_invalid_output_once() -> None:
    fake = FakeCompletion(
        [
            "not json",
            '{"intent":"ambiguous","confidence":0.2,"evidence":"insufficient context"}',
        ]
    )
    result = asyncio.run(create_intent_pipeline(fake).run(task="Classify", dynamic_context="{}"))
    assert result.intent is MessageIntent.AMBIGUOUS
    assert len(fake.calls) == 2


def test_pipeline_fails_closed_after_one_repair() -> None:
    fake = FakeCompletion(["bad", "still bad"])
    with pytest.raises(PipelineValidationError):
        asyncio.run(create_intent_pipeline(fake).run(task="Classify", dynamic_context="{}"))
    assert len(fake.calls) == 2
