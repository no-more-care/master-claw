import asyncio

import pytest

from masterclaw.app.decision_checkpoints import run_checkpointed_decision
from masterclaw.context.assembler import AssembledContext
from masterclaw.pipelines.base import CompletionResult, PipelineValidationError
from masterclaw.pipelines.narrative import (
    NarrativeResult,
    ReviewedNarrativePipeline,
    create_narrative_pipeline,
)
from masterclaw.storage.sqlite import SQLiteStore


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


def test_narrative_pipeline_rejects_whitespace_only_prose() -> None:
    pipeline = create_narrative_pipeline(FakeCompletion('{"narrative":"   "}'))
    with pytest.raises(PipelineValidationError):
        asyncio.run(pipeline.run(task="Narrate", context=CONTEXT))


def test_narrative_prompt_requires_locale_public_context_and_explicit_actor() -> None:
    class PromptCapture(FakeCompletion):
        system = ""

        async def complete(self, **kwargs) -> CompletionResult:
            self.system = kwargs["system"]
            return await super().complete(**kwargs)

    completion = PromptCapture('{"narrative":"The gate opens."}')
    asyncio.run(create_narrative_pipeline(completion).run(task="Narrate", context=CONTEXT))

    assert "session_brief.locale" in completion.system
    assert "no actor is identified" in completion.system
    assert "Only public world context" in completion.system
    assert "untrusted data" in completion.system


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


def test_review_degradation_fails_closed_and_keeps_immutable_roll_separate() -> None:
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

    with pytest.raises(PipelineValidationError, match="degraded"):
        asyncio.run(pipeline.run(task="Narrate", context=source))
    assert context_assembler.projections["immutable_roll_result"] == {
        "difficulty": 2,
        "hits": 0,
        "narrator_rights": "gm_failure",
    }
    assert reviewer.calls == 0
    assert fallback.calls == 0


def test_reviewed_narrative_pipeline_exposes_checkpoint_contract(tmp_path) -> None:
    class ReviewContext:
        def assemble(self, manifest, projections) -> AssembledContext:
            return AssembledContext("", "{}", (), 10)

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    narrator = FixedNarrativePipeline("The attempt fails and the lock stays shut.")
    reviewer = FixedNarrativePipeline("The lock resists with a metallic snap.")
    fallback = FixedNarrativePipeline("Fallback prose.")
    pipeline = ReviewedNarrativePipeline(
        context=ReviewContext(),
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

    first = asyncio.run(
        run_checkpointed_decision(
            store=store,
            event_id="reviewed-narrative",
            pipeline_key="outcome_narration",
            pipeline=pipeline,
            task="Narrate",
            context=source,
            game_id=None,
        )
    )
    replayed = asyncio.run(
        run_checkpointed_decision(
            store=store,
            event_id="reviewed-narrative",
            pipeline_key="outcome_narration",
            pipeline=pipeline,
            task="Narrate",
            context=source,
            game_id=None,
        )
    )

    assert replayed == first
    assert replayed.narrative == "The lock resists with a metallic snap."
    assert narrator.calls == 1
    assert reviewer.calls == 1
