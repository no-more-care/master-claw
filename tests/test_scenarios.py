from pathlib import Path

import pytest

from masterclaw.app.scenarios import (
    SCENARIOS,
    CommandId,
    CommandSafety,
    ScenarioId,
    match_explicit,
    preparation_has_multiple_intents,
    resolve_scenario,
)
from masterclaw.context.assembler import ContextAssembler, ContextHistory
from masterclaw.context.manifests import state_decision_manifest
from masterclaw.domain.models import OperatingMode


@pytest.mark.parametrize(
    ("mode", "workspace", "pending", "resumed", "expected"),
    [
        (OperatingMode.WORLD_MANAGEMENT, None, None, False, ScenarioId.WORLD_SELECTION),
        (
            OperatingMode.WORLD_MANAGEMENT,
            "collecting",
            None,
            False,
            ScenarioId.WORLD_EDITING_COLLECTING,
        ),
        (
            OperatingMode.WORLD_MANAGEMENT,
            "review",
            None,
            False,
            ScenarioId.WORLD_EDITING_REVIEW,
        ),
        (OperatingMode.PREPARATION, None, None, False, ScenarioId.PREPARATION),
        (OperatingMode.PLAY, None, None, False, ScenarioId.PLAY),
        (OperatingMode.PAUSED, None, None, False, ScenarioId.PAUSED),
        (OperatingMode.FINISHED, None, None, False, ScenarioId.FINISHED),
        (
            OperatingMode.PLAY,
            None,
            "pool_confirmation",
            False,
            ScenarioId.PLAY_PENDING_POOL,
        ),
        (
            OperatingMode.PLAY,
            None,
            "player_narration",
            False,
            ScenarioId.PLAY_PENDING_NARRATION,
        ),
        (OperatingMode.PLAY, None, "choice", False, ScenarioId.PLAY_PENDING_OTHER),
        (OperatingMode.PLAY, None, None, True, ScenarioId.ROLL_RESUME),
    ],
)
def test_state_resolves_to_one_scenario(mode, workspace, pending, resumed, expected) -> None:
    scenario = resolve_scenario(
        mode=mode,
        workspace_stage=workspace,
        pending_kind=pending,
        resumed_roll=resumed,
    )
    assert scenario.id is expected


def test_same_phrase_has_scenario_specific_meaning() -> None:
    selection = resolve_scenario(
        mode=OperatingMode.WORLD_MANAGEMENT,
        workspace_stage=None,
        pending_kind=None,
        resumed_roll=False,
    )
    play = resolve_scenario(
        mode=OperatingMode.PLAY,
        workspace_stage=None,
        pending_kind=None,
        resumed_roll=False,
    )
    assert match_explicit(selection, "Что ты умеешь?") is CommandId.SHOW_WORLD_CATALOG
    assert match_explicit(play, "Что ты умеешь?") is CommandId.SHOW_HELP


@pytest.mark.parametrize(
    "scenario_id",
    [
        ScenarioId.WORLD_SELECTION,
        ScenarioId.WORLD_EDITING_COLLECTING,
        ScenarioId.WORLD_EDITING_REVIEW,
        ScenarioId.PREPARATION,
        ScenarioId.PLAY,
        ScenarioId.PAUSED,
        ScenarioId.FINISHED,
        ScenarioId.PLAY_PENDING_POOL,
        ScenarioId.PLAY_PENDING_NARRATION,
        ScenarioId.PLAY_PENDING_OTHER,
    ],
)
def test_help_rules_and_clarify_exist_in_every_interactive_scenario(scenario_id) -> None:
    scenario = resolve_scenario(
        mode=(
            OperatingMode.WORLD_MANAGEMENT
            if scenario_id.value.startswith("world")
            else OperatingMode.PREPARATION
            if scenario_id is ScenarioId.PREPARATION
            else OperatingMode.PAUSED
            if scenario_id is ScenarioId.PAUSED
            else OperatingMode.FINISHED
            if scenario_id is ScenarioId.FINISHED
            else OperatingMode.PLAY
        ),
        workspace_stage=(
            "collecting"
            if scenario_id is ScenarioId.WORLD_EDITING_COLLECTING
            else "review"
            if scenario_id is ScenarioId.WORLD_EDITING_REVIEW
            else None
        ),
        pending_kind=(
            "pool_confirmation"
            if scenario_id is ScenarioId.PLAY_PENDING_POOL
            else "player_narration"
            if scenario_id is ScenarioId.PLAY_PENDING_NARRATION
            else "choice"
            if scenario_id is ScenarioId.PLAY_PENDING_OTHER
            else None
        ),
        resumed_roll=False,
    )
    assert {CommandId.SHOW_HELP, CommandId.SHOW_RULES, CommandId.CLARIFY} <= (scenario.llm_commands)


def test_world_catalog_is_not_available_inside_editor() -> None:
    scenario = resolve_scenario(
        mode=OperatingMode.WORLD_MANAGEMENT,
        workspace_stage="review",
        pending_kind=None,
        resumed_roll=False,
    )
    assert CommandId.SHOW_WORLD_CATALOG not in scenario.llm_commands
    assert match_explicit(scenario, "покажи миры") is CommandId.CLARIFY


def test_mutating_transition_policies_are_explicit() -> None:
    scenario = SCENARIOS[ScenarioId.WORLD_EDITING_REVIEW]
    assert scenario.command_safety(CommandId.SHOW_WORLD_SETTINGS) is CommandSafety.READ_ONLY
    assert scenario.command_safety(CommandId.REVISE_WORLD) is CommandSafety.INFERRED
    assert scenario.command_safety(CommandId.GENERATE_WORLD) is CommandSafety.EXPLICIT_ONLY
    assert scenario.command_safety(CommandId.CONFIRM_WORLD) is CommandSafety.EXPLICIT_ONLY
    assert scenario.command_safety(CommandId.EXIT_WORLD_EDITOR) is CommandSafety.EXPLICIT_ONLY
    assert (
        SCENARIOS[ScenarioId.PREPARATION].command_safety(CommandId.START_GAME)
        is CommandSafety.EXPLICIT_ONLY
    )


def test_collecting_invalid_state_result_falls_back_to_clarification() -> None:
    assert SCENARIOS[ScenarioId.WORLD_EDITING_COLLECTING].fallback is CommandId.CLARIFY


def test_game_start_has_deterministic_explicit_phrases() -> None:
    preparation = SCENARIOS[ScenarioId.PREPARATION]
    assert match_explicit(preparation, "Все готовы, начинаем игру") is CommandId.START_GAME
    assert match_explicit(preparation, "start the game") is CommandId.START_GAME


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("/advance raise Скрытность", CommandId.REQUEST_ADVANCEMENT),
        ("/advance", CommandId.SHOW_XP),
        ("/advance status", CommandId.SHOW_XP),
        ("/advance teleport", None),
        ("/help <@123>", CommandId.SHOW_HELP),
        ("/assist <@123>", CommandId.OFFER_HELP),
        ("/roll-help <@123>", CommandId.OFFER_HELP),
        ("/world generate id brief", None),
    ],
)
def test_play_slash_lexicon_is_the_only_allowlist(content, expected) -> None:
    play = resolve_scenario(
        mode=OperatingMode.PLAY,
        workspace_stage=None,
        pending_kind=None,
        resumed_roll=False,
    )
    assert match_explicit(play, content) is expected


def test_every_scenario_builds_its_declared_state_context_and_history() -> None:
    assembler = ContextAssembler(Path(__file__).parents[1] / "prompts")
    history = ContextHistory(chat_messages=[{"sequence": index} for index in range(10)])
    for scenario in SCENARIOS.values():
        manifest = state_decision_manifest(
            context_projections=scenario.context_projections,
            recent_chat_messages=scenario.recent_chat_messages,
        )
        projections = {
            "mode": {},
            "scenario": {"id": scenario.id.value},
            **{projection: {"provided": projection} for projection in scenario.context_projections},
        }
        context = assembler.assemble(manifest, projections, history=history)
        for projection in scenario.context_projections:
            assert f"## STATE {projection}" in context.dynamic_context
        if scenario.recent_chat_messages:
            assert '"sequence":9' in context.dynamic_context
            oldest = 10 - scenario.recent_chat_messages
            assert f'"sequence":{oldest}' in context.dynamic_context
        else:
            assert "HISTORY chat_messages" not in context.dynamic_context


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("/game status", CommandId.SHOW_GAME_STATUS),
        ("/xp status", CommandId.SHOW_XP),
        ("/character status", CommandId.SHOW_CHARACTER_SHEET),
        ("/help <@123>", CommandId.SHOW_HELP),
        ("/assist <@123>", CommandId.OFFER_HELP),
    ],
)
def test_pending_scenarios_keep_information_slash_commands(content, expected) -> None:
    for scenario_id in (
        ScenarioId.PLAY_PENDING_POOL,
        ScenarioId.PLAY_PENDING_NARRATION,
        ScenarioId.PLAY_PENDING_OTHER,
    ):
        assert match_explicit(SCENARIOS[scenario_id], content) is expected


def test_pending_scenarios_have_deterministic_cancellation() -> None:
    for scenario_id in (
        ScenarioId.PLAY_PENDING_POOL,
        ScenarioId.PLAY_PENDING_NARRATION,
        ScenarioId.PLAY_PENDING_OTHER,
    ):
        assert match_explicit(SCENARIOS[scenario_id], "отмена") is CommandId.CANCEL_PENDING
        assert match_explicit(SCENARIOS[scenario_id], "cancel") is CommandId.CANCEL_PENDING


@pytest.mark.parametrize(
    ("scenario_id", "content", "expected"),
    [
        (
            ScenarioId.WORLD_EDITING_COLLECTING,
            "Пожалуйста, сгенерируй мир",
            CommandId.GENERATE_WORLD,
        ),
        (
            ScenarioId.WORLD_EDITING_REVIEW,
            "Да, подтверждаю мир, всё хорошо",
            CommandId.CONFIRM_WORLD,
        ),
        (ScenarioId.PREPARATION, "Пожалуйста, начать игру", CommandId.START_GAME),
        (ScenarioId.PREPARATION, "Да, всё готово, начинаем игру", CommandId.START_GAME),
        (ScenarioId.PLAY, "Пожалуйста, поставь игру на паузу", CommandId.PAUSE_GAME),
        (ScenarioId.PAUSED, "Продолжить игру, пожалуйста", CommandId.RESUME_GAME),
    ],
)
def test_explicit_mutations_accept_bounded_polite_wrappers(scenario_id, content, expected) -> None:
    assert match_explicit(SCENARIOS[scenario_id], content) is expected


@pytest.mark.parametrize(
    ("scenario_id", "content"),
    [
        (ScenarioId.WORLD_EDITING_COLLECTING, "Не сгенерируй мир"),
        (ScenarioId.WORLD_EDITING_REVIEW, "«Подтверждаю мир»"),
        (ScenarioId.PREPARATION, "Если все готовы, начать игру"),
        (ScenarioId.PLAY, "Не ставь игру на паузу"),
        (ScenarioId.PAUSED, "Он сказал: «продолжить игру»"),
        (ScenarioId.PLAY, "`finish_game`"),
        (ScenarioId.PLAY, "```finish_game```"),
        (ScenarioId.PLAY, "> finish_game"),
        (ScenarioId.PLAY, "~~finish_game~~"),
        (ScenarioId.PLAY, "[finish_game](https://example.test/docs)"),
        (ScenarioId.PLAY, "- finish_game"),
        (ScenarioId.PLAY, "**finish_game**"),
        (ScenarioId.PLAY, "_finish_game_"),
    ],
)
def test_negated_quoted_or_conditional_mutations_are_not_explicit(scenario_id, content) -> None:
    assert match_explicit(SCENARIOS[scenario_id], content) is None


@pytest.mark.parametrize(
    "content",
    [
        "Enable XP and let players narrate significant changes",
        "I want a hero and enable XP",
        "Включи опыт и дай игрокам значительные права рассказчика",
    ],
)
def test_preparation_multi_intent_guard_covers_natural_setting_synonyms(content) -> None:
    assert preparation_has_multiple_intents(content)


def test_lifecycle_commands_are_explicit_only() -> None:
    for command in (
        CommandId.PAUSE_GAME,
        CommandId.RESUME_GAME,
        CommandId.FINISH_GAME,
        CommandId.UNBIND_GAME,
        CommandId.NEW_SESSION,
    ):
        assert SCENARIOS[ScenarioId.PLAY].command_safety(command) is CommandSafety.EXPLICIT_ONLY
