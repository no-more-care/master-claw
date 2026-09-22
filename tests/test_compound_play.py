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


def test_compound_plan_requires_help_to_be_submitted_standalone() -> None:
    with pytest.raises(ValidationError, match="help must be submitted as a standalone"):
        CompoundPlayPlan.model_validate(
            {
                "parts": [
                    {"kind": "scene_status", "text": "Show the scene."},
                    {"kind": "help", "text": "I help <@123>."},
                ],
                "clarification_question": None,
            }
        )


def test_compound_plan_rejects_multiple_roleplay_fragments() -> None:
    with pytest.raises(ValidationError, match="must be merged into one roleplay"):
        CompoundPlayPlan.model_validate(
            {
                "parts": [
                    {"kind": "roleplay", "text": "I greet the guard."},
                    {"kind": "roleplay", "text": "I thank the keeper."},
                ],
                "clarification_question": None,
            }
        )


def test_compound_prompt_forbids_executable_help_and_multiple_roleplay_parts() -> None:
    class PromptCapture:
        def __init__(self) -> None:
            self.system = ""

        async def complete(self, **kwargs) -> CompletionResult:
            self.system = kwargs["system"]
            return CompletionResult(
                '{"parts":[],"clarification_question":"Submit help as a standalone request."}',
                used_tool=True,
            )

    completion = PromptCapture()
    asyncio.run(
        create_compound_play_pipeline(completion).run(
            task="Decompose a help-plus-roleplay request.",
            context=context(),
        )
    )

    assert "Never emit help as an executable compound part" in completion.system
    assert "Emit at most one roleplay part" in completion.system


def test_compound_plan_requires_concrete_clarification_for_multiple_actions() -> None:
    with pytest.raises(ValidationError, match="multiple actions require"):
        CompoundPlayPlan.model_validate(
            {
                "parts": [
                    {"kind": "action", "text": "I open the door."},
                    {"kind": "action", "text": "I attack the guard."},
                ],
                "clarification_question": None,
            }
        )
