from pathlib import Path

import pytest

from masterclaw.app.scenarios import (
    SCENARIOS,
    CommandId,
    CommandSafety,
    ScenarioId,
    match_explicit,
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
        ("/advance", CommandId.REQUEST_ADVANCEMENT),
        ("/advance status", CommandId.REQUEST_ADVANCEMENT),
        ("/advance teleport", None),
        ("/help <@123>", CommandId.OFFER_HELP),
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
        ("/help <@123>", CommandId.OFFER_HELP),
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
