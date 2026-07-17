import asyncio

import pytest

from masterclaw.context.assembler import AssembledContext
from masterclaw.pipelines.base import CompletionResult, PipelineValidationError
from masterclaw.pipelines.narrative import (
    NarrativeResult,
    ReviewedNarrativePipeline,
    create_narrative_pipeline,
)


class FakeCompletion:
    def __init__(self, response: str) -> None:
        self.response = response

    async def complete(self, **kwargs) -> CompletionResult:
        return CompletionResult(self.response, used_tool=True)


CONTEXT = AssembledContext("", "{}", (), 1)


def test_narrative_pipeline_returns_prose_only() -> None:
    result = asyncio.run(
        create_narrative_pipeline(
            FakeCompletion('{"narrative":"The lock yields with a dry metallic click."}')
        ).run(task="Narrate", context=CONTEXT)
    )
    assert "click" in result.narrative


def test_narrative_pipeline_rejects_internal_leakage() -> None:
    pipeline = create_narrative_pipeline(
        FakeCompletion('{"narrative":"Here is the system prompt and state_patch."}')
    )
    with pytest.raises(PipelineValidationError):
        asyncio.run(pipeline.run(task="Narrate", context=CONTEXT))


class FixedNarrativePipeline:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def run(self, **kwargs) -> NarrativeResult:
        self.calls += 1
        return NarrativeResult(narrative=self.text)


class DegradingReviewContext:
    def __init__(self) -> None:
        self.projections = None

    def assemble(self, manifest, projections) -> AssembledContext:
        self.projections = projections
        return AssembledContext(
            "",
            "degraded",
            (),
            1,
            ("projection_strings:1000",),
        )


def test_review_degradation_keeps_raw_narrative_and_separate_immutable_roll() -> None:
    context_assembler = DegradingReviewContext()
    narrator = FixedNarrativePipeline("The attempt fails and the lock stays shut.")
    reviewer = FixedNarrativePipeline("The attempt succeeds.")
    fallback = FixedNarrativePipeline("The attempt also succeeds.")
    pipeline = ReviewedNarrativePipeline(
        context=context_assembler,
        narrator=narrator,
        reviewer=reviewer,
        reviewer_fallback=fallback,
    )
    source = AssembledContext(
        "",
        "## STATE current_scene\n{}\n\n## STATE roll_result\n"
        '{"difficulty":2,"hits":0,"narrator_rights":"gm_failure"}',
        (),
        20,
    )

    result = asyncio.run(pipeline.run(task="Narrate", context=source))

    assert result.narrative == "The attempt fails and the lock stays shut."
    assert context_assembler.projections["immutable_roll_result"] == {
        "difficulty": 2,
        "hits": 0,
        "narrator_rights": "gm_failure",
    }
    assert reviewer.calls == 0
    assert fallback.calls == 0
