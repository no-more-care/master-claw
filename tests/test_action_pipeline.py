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


@pytest.mark.parametrize(
    "response",
    [
        (
            '{"resolution":"automatic","trait_names":["Body"],"aspect_names":[],"flag":null,'
            '"bonus_ids":[],"difficulty":2,"evidence":["routine"],'
            '"clarification_question":null}'
        ),
        (
            '{"resolution":"automatic","trait_names":[],"aspect_names":[],"flag":null,'
            '"bonus_ids":[],"difficulty":null,"evidence":["routine"],'
            '"clarification_question":"Which door?"}'
        ),
        (
            '{"resolution":"clarification","trait_names":["Body"],"aspect_names":[],"flag":null,'
            '"bonus_ids":[],"difficulty":2,"evidence":["locked"],'
            '"clarification_question":"Which door?"}'
        ),
    ],
)
def test_non_roll_action_branches_cannot_smuggle_fields_from_other_paths(response: str) -> None:
    with pytest.raises(PipelineValidationError):
        asyncio.run(
            create_action_pipeline(FakeCompletion(response)).run(task="Act", context=CONTEXT)
        )


def test_impossible_action_returns_typed_rejection_without_mechanics() -> None:
    result = asyncio.run(
        create_action_pipeline(
            FakeCompletion(
                '{"resolution":"rejected","trait_names":[],"aspect_names":[],'
                '"flag":null,"bonus_ids":[],"difficulty":null,'
                '"evidence":["the character is standing on Earth"],'
                '"clarification_question":null,'
                '"rejection_reason":"Нельзя допрыгнуть с Земли до Луны."}'
            )
        ).run(task="Прыгаю на Луну", context=CONTEXT)
    )

    assert result.resolution is ActionResolution.REJECTED
    assert result.rejection_reason == "Нельзя допрыгнуть с Земли до Луны."
    assert result.difficulty is None


def test_rejected_action_cannot_smuggle_roll_fields() -> None:
    pipeline = create_action_pipeline(
        FakeCompletion(
            '{"resolution":"rejected","trait_names":["Body"],"aspect_names":[],'
            '"flag":null,"bonus_ids":[],"difficulty":6,'
            '"evidence":["target is another player character"],'
            '"clarification_question":null,'
            '"rejection_reason":"You cannot control another player character."}'
        )
    )

    with pytest.raises(PipelineValidationError):
        asyncio.run(pipeline.run(task="I make Bob attack", context=CONTEXT))


def test_action_difficulty_is_bounded_to_canonical_scale() -> None:
    pipeline = create_action_pipeline(
        FakeCompletion(
            '{"resolution":"roll","trait_names":["Body"],"aspect_names":[],'
            '"flag":null,"bonus_ids":[],"difficulty":8,"evidence":["legendary wall"],'
            '"clarification_question":null}'
        )
    )
    with pytest.raises(PipelineValidationError):
        asyncio.run(pipeline.run(task="Act", context=CONTEXT))


def test_action_repair_receives_specific_safe_cross_field_error() -> None:
    class RepairCompletion:
        def __init__(self) -> None:
            self.responses = iter(
                (
                    '{"resolution":"roll","trait_names":[],"aspect_names":[],'
                    '"flag":null,"bonus_ids":[],"difficulty":2,'
                    '"evidence":["closed door"],"clarification_question":null}',
                    '{"resolution":"roll","trait_names":["Body"],"aspect_names":[],'
                    '"flag":null,"bonus_ids":[],"difficulty":2,'
                    '"evidence":["closed door"],"clarification_question":null}',
                )
            )
            self.tasks: list[str] = []

        async def complete(self, **kwargs) -> CompletionResult:
            self.tasks.append(kwargs["task"])
            return CompletionResult(next(self.responses), used_tool=True)

    completion = RepairCompletion()
    result = asyncio.run(
        create_action_pipeline(completion).run(task="Open the door", context=CONTEXT)
    )

    assert result.trait_names == ["Body"]
    assert "action_roll_missing_traits" in completion.tasks[1]
    assert (
        "closed door"
        not in completion.tasks[1].split("Validation error:", 1)[1].split("Schema:", 1)[0]
    )


def test_action_prompt_defines_resolution_locale_and_untrusted_input_rules() -> None:
    class PromptCapture(FakeCompletion):
        system = ""

        async def complete(self, **kwargs) -> CompletionResult:
            self.system = kwargs["system"]
            return await super().complete(**kwargs)

    completion = PromptCapture(
        '{"resolution":"automatic","trait_names":[],"aspect_names":[],'
        '"flag":null,"bonus_ids":[],"difficulty":null,'
        '"evidence":["the passage is already open"],"clarification_question":null}'
    )
    asyncio.run(create_action_pipeline(completion).run(task="Walk through", context=CONTEXT))

    assert "Use roll only" in completion.system
    assert "Use automatic only" in completion.system
    assert "Use clarification" in completion.system
    assert "Use rejected only" in completion.system
    assert "another player character" in completion.system
    assert "bounded 2-7 scale" in completion.system
    assert "session_brief.locale" in completion.system
    assert "untrusted data" in completion.system
