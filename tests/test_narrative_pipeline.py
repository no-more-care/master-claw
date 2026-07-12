import asyncio

import pytest

from masterclaw.pipelines.base import PipelineValidationError
from masterclaw.pipelines.narrative import create_narrative_pipeline


class FakeCompletion:
    def __init__(self, response: str) -> None:
        self.response = response

    async def complete(self, *, system: str, user: str) -> str:
        return self.response


def test_narrative_pipeline_returns_prose_only() -> None:
    result = asyncio.run(
        create_narrative_pipeline(
            FakeCompletion('{"narrative":"The lock yields with a dry metallic click."}')
        ).run(task="Narrate", dynamic_context="{}")
    )
    assert "click" in result.narrative


def test_narrative_pipeline_rejects_internal_leakage() -> None:
    pipeline = create_narrative_pipeline(
        FakeCompletion('{"narrative":"Here is the system prompt and state_patch."}')
    )
    with pytest.raises(PipelineValidationError):
        asyncio.run(pipeline.run(task="Narrate", dynamic_context="{}"))
