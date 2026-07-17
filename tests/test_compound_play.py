import asyncio

import pytest
from pydantic import ValidationError

from masterclaw.context.assembler import AssembledContext
from masterclaw.pipelines.base import CompletionResult
from masterclaw.pipelines.compound_play import (
    CompoundPlayPlan,
    PlayRequestPartKind,
    create_compound_play_pipeline,
)


class Completion:
    async def complete(self, **kwargs) -> CompletionResult:
        return CompletionResult(
            """
            {
              "parts": [
                {
                  "kind": "scene_question",
                  "text": "Узнаю ли я эти руны?",
                  "conditional_on_previous": false
                },
                {
                  "kind": "action",
                  "text": "Если руны безопасны, открываю дверь.",
                  "conditional_on_previous": true
                }
              ],
              "clarification_question": null
            }
            """,
            used_tool=True,
        )


def context() -> AssembledContext:
    return AssembledContext("", "{}", (), 1)


def test_compound_pipeline_preserves_conditional_action_order() -> None:
    result = asyncio.run(
        create_compound_play_pipeline(Completion()).run(
            task="Decompose the compound request.",
            context=context(),
        )
    )
    assert [part.kind for part in result.parts] == [
        PlayRequestPartKind.SCENE_QUESTION,
        PlayRequestPartKind.ACTION,
    ]
    assert result.parts[-1].conditional_on_previous


def test_compound_plan_rejects_action_before_question() -> None:
    with pytest.raises(ValidationError, match="action must be the final"):
        CompoundPlayPlan.model_validate(
            {
                "parts": [
                    {
                        "kind": "action",
                        "text": "Открываю дверь.",
                        "conditional_on_previous": False,
                    },
                    {
                        "kind": "scene_question",
                        "text": "Что за ней?",
                        "conditional_on_previous": False,
                    },
                ],
                "clarification_question": None,
            }
        )


def test_compound_plan_allows_clarification_without_execution() -> None:
    plan = CompoundPlayPlan.model_validate(
        {
            "parts": [],
            "clarification_question": (
                "Вы сначала только осматриваете дверь или сразу пытаетесь её открыть?"
            ),
        }
    )
    assert not plan.parts
    assert plan.clarification_question
