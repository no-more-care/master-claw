from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from masterclaw.app.scenarios import (
    CommandId,
    ScenarioId,
    is_world_creation_request,
    match_explicit,
    normalize_phrase,
    preparation_has_multiple_intents,
    resolve_scenario,
    routes_unknown_slash_subcommand_to_usage,
)
from masterclaw.domain.models import OperatingMode


class DecisionSource(StrEnum):
    COMMAND = "command"
    PENDING = "pending"
    WORKSPACE = "workspace"
    PHRASE = "phrase"
    STRUCTURAL = "structural"
    LLM = "llm"


class StateResponseKind(StrEnum):
    WORLD_CATALOG = "world_catalog"
    WORLD_STATUS = "world_status"
    WORLD_SELECTION_CLARIFICATION = "world_selection_clarification"
    CHARACTER_STATUS = "character_status"
    GAME_STATUS = "game_status"
    XP_STATUS = "xp_status"
    SCENE_STATUS = "scene_status"
    HELP = "help"


class HandlerKind(StrEnum):
    COMMAND = "command"
    RESUMED_ROLL = "resumed_roll"
    WORKSPACE_REVISE = "workspace_revise"
    WORKSPACE_GENERATE = "workspace_generate"
    WORKSPACE_CONFIRM = "workspace_confirm"
    WORKSPACE_EXIT = "workspace_exit"
    WORLD_CREATE = "world_create"
    WORLD_SELECT = "world_select"
    CHARACTER_SELECT = "character_select"
    CHARACTER_CREATE = "character_create"
    GAME_START = "game_start"
    GAME_CONFIGURE = "game_configure"
    HELP = "help"
    ADVANCEMENT = "advancement"
    ACTION = "action"
    RULES_QUESTION = "rules_question"
    SCENE_QUESTION = "scene_question"
    COMPOUND = "compound"
    ROLEPLAY = "roleplay"
    PENDING = "pending"
    PENDING_CANCEL = "pending_cancel"


@dataclass(frozen=True, slots=True)
class PendingView:
    kind: str
    scene_id: str | None = None


@dataclass(frozen=True, slots=True)
class DispatchSnapshot:
    """Read-only input to dispatch decision making.

    Priority is command -> resumed/pending interaction -> world workspace ->
    deterministic information -> structural selections -> bounded LLM classifier.
    """

    mode: OperatingMode
    mode_reason: str
    game_id: str | None
    workspace_stage: str | None
    pending: PendingView | None
    resumed_roll: bool
    locale: str
    content: str
    author_id: str
    command: str | None = None


@dataclass(frozen=True, slots=True)
class RespondFromState:
    kind: StateResponseKind
    source: DecisionSource
    command: CommandId


@dataclass(frozen=True, slots=True)
class RunHandler:
    handler: HandlerKind
    source: DecisionSource
    command: CommandId | None = None
    argument: str | None = None


@dataclass(frozen=True, slots=True)
class Reject:
    reason: str
    source: DecisionSource
    command: CommandId | None = None


@dataclass(frozen=True, slots=True)
class ClassifyWithLlm:
    source: DecisionSource = DecisionSource.LLM


Decision = RespondFromState | RunHandler | Reject | ClassifyWithLlm


def decision_for_scenario_command(
    command: CommandId, *, source: DecisionSource, argument: str | None = None
) -> Decision:
    state_commands = {
        CommandId.SHOW_WORLD_CATALOG: StateResponseKind.WORLD_CATALOG,
        CommandId.SHOW_WORLD_SETTINGS: StateResponseKind.WORLD_STATUS,
        CommandId.SHOW_CHARACTER_SHEET: StateResponseKind.CHARACTER_STATUS,
        CommandId.SHOW_GAME_STATUS: StateResponseKind.GAME_STATUS,
        CommandId.SHOW_XP: StateResponseKind.XP_STATUS,
        CommandId.SHOW_SCENE: StateResponseKind.SCENE_STATUS,
        CommandId.SHOW_HELP: StateResponseKind.HELP,
    }
    if command in state_commands:
        return RespondFromState(state_commands[command], source, command)
    handlers = {
        CommandId.SHOW_RULES: HandlerKind.RULES_QUESTION,
        CommandId.CREATE_WORLD: HandlerKind.WORLD_CREATE,
        CommandId.SELECT_WORLD: HandlerKind.WORLD_SELECT,
        CommandId.REVISE_WORLD: HandlerKind.WORKSPACE_REVISE,
        CommandId.GENERATE_WORLD: HandlerKind.WORKSPACE_GENERATE,
        CommandId.CONFIRM_WORLD: HandlerKind.WORKSPACE_CONFIRM,
        CommandId.EXIT_WORLD_EDITOR: HandlerKind.WORKSPACE_EXIT,
        CommandId.CREATE_CHARACTER: HandlerKind.CHARACTER_CREATE,
        CommandId.SELECT_CHARACTER: HandlerKind.CHARACTER_SELECT,
        CommandId.START_GAME: HandlerKind.GAME_START,
        CommandId.PAUSE_GAME: HandlerKind.COMMAND,
        CommandId.RESUME_GAME: HandlerKind.COMMAND,
        CommandId.FINISH_GAME: HandlerKind.COMMAND,
        CommandId.UNBIND_GAME: HandlerKind.COMMAND,
        CommandId.NEW_SESSION: HandlerKind.COMMAND,
        CommandId.CONFIGURE_GAME: HandlerKind.GAME_CONFIGURE,
        CommandId.DECLARE_ACTION: HandlerKind.ACTION,
        CommandId.PLAYER_NARRATION: HandlerKind.ROLEPLAY,
        CommandId.ASK_SCENE_QUESTION: HandlerKind.SCENE_QUESTION,
        CommandId.COMPOUND_PLAY: HandlerKind.COMPOUND,
        CommandId.REQUEST_ADVANCEMENT: HandlerKind.ADVANCEMENT,
        CommandId.OFFER_HELP: HandlerKind.HELP,
        CommandId.ANSWER_PENDING: HandlerKind.PENDING,
        CommandId.CANCEL_PENDING: HandlerKind.PENDING_CANCEL,
        CommandId.RESUME_ROLL: HandlerKind.RESUMED_ROLL,
    }
    if command in handlers:
        return RunHandler(handlers[command], source, command, argument)
    return Reject("conversation_clarification", source, command)


def decide(snapshot: DispatchSnapshot) -> Decision:
    """Resolve state first, then recognize only commands declared by that scenario."""
    scenario = resolve_scenario(
        mode=snapshot.mode,
        workspace_stage=snapshot.workspace_stage,
        pending_kind=None if snapshot.pending is None else snapshot.pending.kind,
        resumed_roll=snapshot.resumed_roll,
    )
    if scenario.id is ScenarioId.ROLL_RESUME:
        return decision_for_scenario_command(CommandId.RESUME_ROLL, source=DecisionSource.PENDING)
    normalized = normalize_phrase(snapshot.content)
    if (
        snapshot.content.lstrip().startswith("//")
        or normalized.startswith("ooc ")
        or normalized == "ooc"
        or normalized.startswith("вне игры ")
        or normalized == "вне игры"
    ):
        return Reject("ooc_ignored", DecisionSource.PHRASE)
    if snapshot.command is not None:
        matched_command = match_explicit(scenario, snapshot.content)
        if matched_command is not None:
            direct = decision_for_scenario_command(matched_command, source=DecisionSource.COMMAND)
            if isinstance(direct, RespondFromState):
                return direct
            return RunHandler(HandlerKind.COMMAND, DecisionSource.COMMAND, matched_command)
        if routes_unknown_slash_subcommand_to_usage(scenario, snapshot.content):
            return RunHandler(HandlerKind.COMMAND, DecisionSource.COMMAND)
        return Reject(
            f"unavailable_{snapshot.mode.value}",
            DecisionSource.COMMAND,
        )
    if snapshot.workspace_stage is not None and is_world_creation_request(snapshot.content):
        return Reject(
            "world_workspace_active",
            DecisionSource.WORKSPACE,
            CommandId.CREATE_WORLD,
        )
    explicit = match_explicit(scenario, snapshot.content)
    if explicit is not None:
        return decision_for_scenario_command(explicit, source=DecisionSource.PHRASE)
    if snapshot.mode is OperatingMode.PREPARATION and preparation_has_multiple_intents(
        snapshot.content
    ):
        return Reject("preparation_multi_intent", DecisionSource.PHRASE, CommandId.CLARIFY)
    if snapshot.pending is not None:
        normalized = snapshot.content.strip().casefold()
        if normalized in {"да", "нет", "yes", "no", "confirm", "cancel", "отмена"} or (
            normalized.isdecimal()
        ):
            return decision_for_scenario_command(
                CommandId.ANSWER_PENDING, source=DecisionSource.PENDING
            )

    return ClassifyWithLlm()
