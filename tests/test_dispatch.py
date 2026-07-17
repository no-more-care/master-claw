import pytest

from masterclaw.app.dispatch import (
    ClassifyWithLlm,
    DecisionSource,
    DispatchSnapshot,
    HandlerKind,
    PendingView,
    Reject,
    RespondFromState,
    RunHandler,
    StateResponseKind,
    decide,
)
from masterclaw.app.scenarios import CommandId
from masterclaw.domain.models import OperatingMode


def snapshot(
    content: str,
    *,
    mode: OperatingMode = OperatingMode.PLAY,
    workspace_stage: str | None = None,
    pending: PendingView | None = None,
    resumed_roll: bool = False,
    command: str | None = None,
) -> DispatchSnapshot:
    return DispatchSnapshot(
        mode=mode,
        mode_reason="test",
        game_id=None if mode is OperatingMode.WORLD_MANAGEMENT else "game",
        workspace_stage=workspace_stage,
        pending=pending,
        resumed_roll=resumed_roll,
        locale="ru",
        content=content,
        author_id="alice",
        command=command,
    )


@pytest.mark.parametrize(
    ("case", "expected_type", "expected_value"),
    [
        (
            snapshot("/game status", command="/game"),
            RunHandler,
            HandlerKind.COMMAND,
        ),
        (
            snapshot("0", pending=PendingView("pool_confirmation")),
            RunHandler,
            HandlerKind.PENDING,
        ),
        (
            snapshot("отмена", pending=PendingView("player_narration")),
            RunHandler,
            HandlerKind.PENDING_CANCEL,
        ),
        (
            snapshot("ignored", resumed_roll=True),
            RunHandler,
            HandlerKind.RESUMED_ROLL,
        ),
        (
            snapshot(
                "Можно генерировать",
                mode=OperatingMode.WORLD_MANAGEMENT,
                workspace_stage="collecting",
            ),
            RunHandler,
            HandlerKind.WORKSPACE_GENERATE,
        ),
        (
            snapshot(
                "Генерируй",
                mode=OperatingMode.WORLD_MANAGEMENT,
                workspace_stage="collecting",
            ),
            RunHandler,
            HandlerKind.WORKSPACE_GENERATE,
        ),
        (
            snapshot("что ты умеешь", mode=OperatingMode.PLAY),
            RespondFromState,
            StateResponseKind.HELP,
        ),
        (
            snapshot("Какие миры доступны", mode=OperatingMode.WORLD_MANAGEMENT),
            RespondFromState,
            StateResponseKind.WORLD_CATALOG,
        ),
    ],
)
def test_dispatch_priority_table(case, expected_type, expected_value) -> None:
    decision = decide(case)
    assert isinstance(decision, expected_type)
    assert getattr(decision, "handler", getattr(decision, "kind", None)) is expected_value


def test_workspace_scenario_rejects_world_creation_command() -> None:
    decision = decide(
        snapshot(
            "/world create id Title",
            mode=OperatingMode.WORLD_MANAGEMENT,
            workspace_stage="review",
            pending=PendingView("choice"),
            command="/world",
        )
    )
    assert isinstance(decision, Reject)
    assert decision.source is DecisionSource.COMMAND


def test_unknown_review_input_is_delegated_to_closed_scenario_model() -> None:
    decision = decide(
        snapshot(
            "Ну, наверное, нормально",
            mode=OperatingMode.WORLD_MANAGEMENT,
            workspace_stage="review",
        )
    )
    assert isinstance(decision, ClassifyWithLlm)


def test_new_world_request_cannot_replace_active_review_workspace() -> None:
    decision = decide(
        snapshot(
            "создай новый мир",
            mode=OperatingMode.WORLD_MANAGEMENT,
            workspace_stage="review",
        )
    )
    assert decision == Reject(
        "world_workspace_active",
        DecisionSource.WORKSPACE,
        CommandId.CREATE_WORLD,
    )


def test_rules_and_help_escape_collecting_revision_fallback() -> None:
    rules = decide(
        snapshot(
            "как работает бросок кубов",
            mode=OperatingMode.WORLD_MANAGEMENT,
            workspace_stage="collecting",
        )
    )
    help_decision = decide(
        snapshot(
            "помощь",
            mode=OperatingMode.WORLD_MANAGEMENT,
            workspace_stage="collecting",
        )
    )
    assert isinstance(rules, ClassifyWithLlm)
    assert isinstance(help_decision, RespondFromState)
    assert help_decision.kind is StateResponseKind.HELP


def test_known_slash_root_with_bad_arguments_reaches_usage_handler() -> None:
    decision = decide(snapshot("/advance teleport", command="/advance"))
    assert decision == RunHandler(HandlerKind.COMMAND, DecisionSource.COMMAND)
