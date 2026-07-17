from __future__ import annotations

import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from masterclaw.domain.models import OperatingMode


def normalize_phrase(value: str) -> str:
    value = value.casefold().replace("ё", "е")
    value = re.sub(r"<(?:@!?|#)\d+>", " ", value)
    value = re.sub(r"[^a-zа-я0-9]+", " ", value)
    return " ".join(value.split())


class ScenarioId(StrEnum):
    WORLD_SELECTION = "world_selection"
    WORLD_EDITING_COLLECTING = "world_editing_collecting"
    WORLD_EDITING_REVIEW = "world_editing_review"
    PREPARATION = "preparation"
    PLAY = "play"
    PLAY_PENDING_POOL = "play_pending_pool"
    PLAY_PENDING_NARRATION = "play_pending_narration"
    PLAY_PENDING_OTHER = "play_pending_other"
    ROLL_RESUME = "roll_resume"


class CommandId(StrEnum):
    CLARIFY = "clarify"
    SHOW_RULES = "show_rules"
    SHOW_HELP = "show_help"
    SHOW_WORLD_CATALOG = "show_world_catalog"
    SHOW_WORLD_SETTINGS = "show_world_settings"
    CREATE_WORLD = "create_world"
    SELECT_WORLD = "select_world"
    REVISE_WORLD = "revise_world"
    GENERATE_WORLD = "generate_world"
    CONFIRM_WORLD = "confirm_world"
    EXIT_WORLD_EDITOR = "exit_world_editor"
    SHOW_CHARACTER_SHEET = "show_character_sheet"
    SHOW_GAME_STATUS = "show_game_status"
    SHOW_XP = "show_xp"
    SHOW_SCENE = "show_scene"
    CREATE_CHARACTER = "create_character"
    SELECT_CHARACTER = "select_character"
    START_GAME = "start_game"
    CONFIGURE_GAME = "configure_game"
    DECLARE_ACTION = "declare_action"
    PLAYER_NARRATION = "player_narration"
    ASK_SCENE_QUESTION = "ask_scene_question"
    COMPOUND_PLAY = "compound_play"
    REQUEST_ADVANCEMENT = "request_advancement"
    OFFER_HELP = "offer_help"
    ANSWER_PENDING = "answer_pending"
    CANCEL_PENDING = "cancel_pending"
    RESUME_ROLL = "resume_roll"


class CommandSafety(StrEnum):
    READ_ONLY = "read_only"
    INFERRED = "inferred"
    EXPLICIT_ONLY = "explicit_only"


INFERRED_COMMAND_MIN_CONFIDENCE = 0.9

_READ_ONLY_COMMANDS = frozenset(
    {
        CommandId.CLARIFY,
        CommandId.SHOW_RULES,
        CommandId.SHOW_HELP,
        CommandId.SHOW_WORLD_CATALOG,
        CommandId.SHOW_WORLD_SETTINGS,
        CommandId.SHOW_CHARACTER_SHEET,
        CommandId.SHOW_GAME_STATUS,
        CommandId.SHOW_XP,
        CommandId.SHOW_SCENE,
        CommandId.ASK_SCENE_QUESTION,
    }
)
_EXPLICIT_ONLY_COMMANDS = frozenset(
    {
        CommandId.GENERATE_WORLD,
        CommandId.CONFIRM_WORLD,
        CommandId.EXIT_WORLD_EDITOR,
        CommandId.START_GAME,
    }
)


@dataclass(frozen=True, slots=True)
class Scenario:
    id: ScenarioId
    explicit_commands: Mapping[str, CommandId]
    slash_commands: Mapping[tuple[str, str], CommandId]
    llm_commands: frozenset[CommandId]
    context_projections: tuple[str, ...]
    recent_chat_messages: int
    fallback: CommandId = CommandId.CLARIFY

    @staticmethod
    def command_safety(command: CommandId) -> CommandSafety:
        if command in _READ_ONLY_COMMANDS:
            return CommandSafety.READ_ONLY
        if command in _EXPLICIT_ONLY_COMMANDS:
            return CommandSafety.EXPLICIT_ONLY
        return CommandSafety.INFERRED


_COMMON_EXPLICIT = {
    "правила": CommandId.SHOW_RULES,
    "покажи правила": CommandId.SHOW_RULES,
    "rules": CommandId.SHOW_RULES,
    "show rules": CommandId.SHOW_RULES,
    "помощь": CommandId.SHOW_HELP,
    "что ты умеешь": CommandId.SHOW_HELP,
    "что ты можешь": CommandId.SHOW_HELP,
    "help": CommandId.SHOW_HELP,
    "what can you do": CommandId.SHOW_HELP,
}


def _explicit(**commands: CommandId) -> dict[str, CommandId]:
    return {**_COMMON_EXPLICIT, **{key.replace("_", " "): value for key, value in commands.items()}}


SCENARIOS: dict[ScenarioId, Scenario] = {
    ScenarioId.WORLD_SELECTION: Scenario(
        id=ScenarioId.WORLD_SELECTION,
        explicit_commands=_explicit(
            покажи_миры=CommandId.SHOW_WORLD_CATALOG,
            покажи_список_миров=CommandId.SHOW_WORLD_CATALOG,
            список_миров=CommandId.SHOW_WORLD_CATALOG,
            какие_миры_доступны=CommandId.SHOW_WORLD_CATALOG,
            какие_есть_миры=CommandId.SHOW_WORLD_CATALOG,
            во_что_можно_играть=CommandId.SHOW_WORLD_CATALOG,
            создай_мир=CommandId.CREATE_WORLD,
            создать_мир=CommandId.CREATE_WORLD,
            создай_новый_мир=CommandId.CREATE_WORLD,
            создать_новый_мир=CommandId.CREATE_WORLD,
            давай_создадим_мир=CommandId.CREATE_WORLD,
            хочу_создать_мир=CommandId.CREATE_WORLD,
        )
        | {
            # The same phrase intentionally means a catalogue here and help elsewhere.
            "что ты умеешь": CommandId.SHOW_WORLD_CATALOG,
            "what can you do": CommandId.SHOW_WORLD_CATALOG,
        },
        slash_commands={("/world", "create"): CommandId.CREATE_WORLD},
        llm_commands=frozenset(
            {
                CommandId.SHOW_WORLD_CATALOG,
                CommandId.SELECT_WORLD,
                CommandId.CREATE_WORLD,
                CommandId.SHOW_RULES,
                CommandId.SHOW_HELP,
                CommandId.CLARIFY,
            }
        ),
        context_projections=("world_catalog",),
        recent_chat_messages=4,
    ),
    ScenarioId.WORLD_EDITING_COLLECTING: Scenario(
        id=ScenarioId.WORLD_EDITING_COLLECTING,
        explicit_commands=_explicit(
            покажи_миры=CommandId.CLARIFY,
            покажи_список_миров=CommandId.CLARIFY,
            список_миров=CommandId.CLARIFY,
            покажи_настройки_мира=CommandId.SHOW_WORLD_SETTINGS,
            покажи_параметры_мира=CommandId.SHOW_WORLD_SETTINGS,
            настройки_мира=CommandId.SHOW_WORLD_SETTINGS,
            параметры_мира=CommandId.SHOW_WORLD_SETTINGS,
            сгенерируй=CommandId.GENERATE_WORLD,
            генерируй=CommandId.GENERATE_WORLD,
            сгенерируй_мир=CommandId.GENERATE_WORLD,
            сгенерируй_черновик=CommandId.GENERATE_WORLD,
            сгенерируй_мир_и_покажи_что_получится=CommandId.GENERATE_WORLD,
            можно_генерировать=CommandId.GENERATE_WORLD,
            готовь_черновик=CommandId.GENERATE_WORLD,
            подготовь_черновик=CommandId.GENERATE_WORLD,
            заверши_генерацию=CommandId.GENERATE_WORLD,
            перегенерируй=CommandId.GENERATE_WORLD,
            перегенерируй_мир=CommandId.GENERATE_WORLD,
            вернуться_к_выбору_миров=CommandId.EXIT_WORLD_EDITOR,
            выйти=CommandId.EXIT_WORLD_EDITOR,
            выйти_из_создания_мира=CommandId.EXIT_WORLD_EDITOR,
            отменить_создание_мира=CommandId.EXIT_WORLD_EDITOR,
            отмени_создание_мира=CommandId.EXIT_WORLD_EDITOR,
        ),
        slash_commands={},
        llm_commands=frozenset(
            {
                CommandId.REVISE_WORLD,
                CommandId.GENERATE_WORLD,
                CommandId.EXIT_WORLD_EDITOR,
                CommandId.SHOW_WORLD_SETTINGS,
                CommandId.SHOW_RULES,
                CommandId.SHOW_HELP,
                CommandId.CLARIFY,
            }
        ),
        context_projections=("world_workspace",),
        recent_chat_messages=4,
        fallback=CommandId.CLARIFY,
    ),
    ScenarioId.WORLD_EDITING_REVIEW: Scenario(
        id=ScenarioId.WORLD_EDITING_REVIEW,
        explicit_commands=_explicit(
            покажи_миры=CommandId.CLARIFY,
            покажи_список_миров=CommandId.CLARIFY,
            список_миров=CommandId.CLARIFY,
            покажи_настройки_мира=CommandId.SHOW_WORLD_SETTINGS,
            настройки_мира=CommandId.SHOW_WORLD_SETTINGS,
            сгенерируй=CommandId.GENERATE_WORLD,
            генерируй=CommandId.GENERATE_WORLD,
            сгенерируй_мир=CommandId.GENERATE_WORLD,
            перегенерируй=CommandId.GENERATE_WORLD,
            перегенерируй_мир=CommandId.GENERATE_WORLD,
            подтверждаю_мир=CommandId.CONFIRM_WORLD,
            мир_подтверждаю=CommandId.CONFIRM_WORLD,
            заверши_создание_мира=CommandId.CONFIRM_WORLD,
            добавить_мир_в_каталог=CommandId.CONFIRM_WORLD,
            вернуться_к_выбору_миров=CommandId.EXIT_WORLD_EDITOR,
            выйти=CommandId.EXIT_WORLD_EDITOR,
            выйти_из_создания_мира=CommandId.EXIT_WORLD_EDITOR,
            отменить_создание_мира=CommandId.EXIT_WORLD_EDITOR,
            отмени_создание_мира=CommandId.EXIT_WORLD_EDITOR,
        ),
        slash_commands={},
        llm_commands=frozenset(
            {
                CommandId.REVISE_WORLD,
                CommandId.GENERATE_WORLD,
                CommandId.CONFIRM_WORLD,
                CommandId.EXIT_WORLD_EDITOR,
                CommandId.SHOW_WORLD_SETTINGS,
                CommandId.SHOW_RULES,
                CommandId.SHOW_HELP,
                CommandId.CLARIFY,
            }
        ),
        context_projections=("world_workspace",),
        recent_chat_messages=4,
    ),
    ScenarioId.PREPARATION: Scenario(
        id=ScenarioId.PREPARATION,
        explicit_commands=_explicit(
            покажи_персонажа=CommandId.SHOW_CHARACTER_SHEET,
            покажи_моего_персонажа=CommandId.SHOW_CHARACTER_SHEET,
            покажи_статус=CommandId.SHOW_GAME_STATUS,
            покажи_статус_игры=CommandId.SHOW_GAME_STATUS,
            начать_игру=CommandId.START_GAME,
            начинаем_игру=CommandId.START_GAME,
            все_готовы_начинаем_игру=CommandId.START_GAME,
            запусти_игру=CommandId.START_GAME,
            start_game=CommandId.START_GAME,
            start_the_game=CommandId.START_GAME,
            everyone_is_ready_start_the_game=CommandId.START_GAME,
        ),
        slash_commands={
            ("/game", "status"): CommandId.SHOW_GAME_STATUS,
            ("/game", "start"): CommandId.START_GAME,
            ("/game", "progression"): CommandId.CONFIGURE_GAME,
            ("/game", "rights"): CommandId.CONFIGURE_GAME,
            ("/game", "narrative"): CommandId.CONFIGURE_GAME,
            ("/character", "create"): CommandId.CREATE_CHARACTER,
            ("/character", "status"): CommandId.SHOW_CHARACTER_SHEET,
        },
        llm_commands=frozenset(
            {
                CommandId.CREATE_CHARACTER,
                CommandId.SELECT_CHARACTER,
                CommandId.START_GAME,
                CommandId.CONFIGURE_GAME,
                CommandId.SHOW_CHARACTER_SHEET,
                CommandId.SHOW_GAME_STATUS,
                CommandId.SHOW_RULES,
                CommandId.SHOW_HELP,
                CommandId.CLARIFY,
            }
        ),
        context_projections=("actor_character",),
        recent_chat_messages=4,
    ),
    ScenarioId.PLAY: Scenario(
        id=ScenarioId.PLAY,
        explicit_commands=_explicit(
            покажи_персонажа=CommandId.SHOW_CHARACTER_SHEET,
            покажи_моего_персонажа=CommandId.SHOW_CHARACTER_SHEET,
            покажи_статус=CommandId.SHOW_GAME_STATUS,
            покажи_статус_игры=CommandId.SHOW_GAME_STATUS,
            покажи_опыт=CommandId.SHOW_XP,
            что_вокруг=CommandId.SHOW_SCENE,
            что_я_вижу=CommandId.SHOW_SCENE,
            где_мы=CommandId.SHOW_SCENE,
        ),
        slash_commands={
            ("/game", "status"): CommandId.SHOW_GAME_STATUS,
            ("/xp", "status"): CommandId.SHOW_XP,
            ("/character", "status"): CommandId.SHOW_CHARACTER_SHEET,
            ("/character", "create"): CommandId.CREATE_CHARACTER,
            ("/advance", "raise"): CommandId.REQUEST_ADVANCEMENT,
            ("/advance", "learn"): CommandId.REQUEST_ADVANCEMENT,
            ("/advance", "status"): CommandId.REQUEST_ADVANCEMENT,
            ("/help", "*"): CommandId.OFFER_HELP,
        },
        llm_commands=frozenset(
            {
                CommandId.DECLARE_ACTION,
                CommandId.PLAYER_NARRATION,
                CommandId.CREATE_CHARACTER,
                CommandId.SELECT_CHARACTER,
                CommandId.SHOW_CHARACTER_SHEET,
                CommandId.SHOW_SCENE,
                CommandId.SHOW_XP,
                CommandId.SHOW_GAME_STATUS,
                CommandId.ASK_SCENE_QUESTION,
                CommandId.COMPOUND_PLAY,
                CommandId.SHOW_RULES,
                CommandId.REQUEST_ADVANCEMENT,
                CommandId.OFFER_HELP,
                CommandId.SHOW_HELP,
                CommandId.CLARIFY,
            }
        ),
        context_projections=("current_scene", "actor_character"),
        recent_chat_messages=6,
    ),
}

WORLD_CREATION_PHRASES = frozenset(
    phrase
    for phrase, command in SCENARIOS[ScenarioId.WORLD_SELECTION].explicit_commands.items()
    if command is CommandId.CREATE_WORLD
)

_PENDING_SLASH_COMMANDS = {
    ("/game", "status"): CommandId.SHOW_GAME_STATUS,
    ("/xp", "status"): CommandId.SHOW_XP,
    ("/character", "status"): CommandId.SHOW_CHARACTER_SHEET,
    ("/help", "*"): CommandId.OFFER_HELP,
}

_PENDING_EXPLICIT_COMMANDS = _explicit(
    отмена=CommandId.CANCEL_PENDING,
    отменить=CommandId.CANCEL_PENDING,
    отмени=CommandId.CANCEL_PENDING,
    cancel=CommandId.CANCEL_PENDING,
)


for pending_scenario in (
    ScenarioId.PLAY_PENDING_POOL,
    ScenarioId.PLAY_PENDING_NARRATION,
    ScenarioId.PLAY_PENDING_OTHER,
):
    SCENARIOS[pending_scenario] = Scenario(
        id=pending_scenario,
        explicit_commands=_PENDING_EXPLICIT_COMMANDS,
        slash_commands=_PENDING_SLASH_COMMANDS,
        llm_commands=frozenset(
            {
                CommandId.ANSWER_PENDING,
                CommandId.SHOW_CHARACTER_SHEET,
                CommandId.SHOW_SCENE,
                CommandId.SHOW_GAME_STATUS,
                CommandId.SHOW_XP,
                CommandId.OFFER_HELP,
                CommandId.CANCEL_PENDING,
                CommandId.SHOW_RULES,
                CommandId.SHOW_HELP,
                CommandId.CLARIFY,
            }
        ),
        context_projections=("pending_interaction", "actor_character"),
        recent_chat_messages=4,
    )

SCENARIOS[ScenarioId.ROLL_RESUME] = Scenario(
    id=ScenarioId.ROLL_RESUME,
    explicit_commands={},
    slash_commands={},
    llm_commands=frozenset({CommandId.RESUME_ROLL}),
    context_projections=(),
    recent_chat_messages=0,
    fallback=CommandId.RESUME_ROLL,
)


def resolve_scenario(
    *,
    mode: OperatingMode,
    workspace_stage: str | None,
    pending_kind: str | None,
    resumed_roll: bool,
) -> Scenario:
    if resumed_roll:
        return SCENARIOS[ScenarioId.ROLL_RESUME]
    if workspace_stage == "collecting":
        return SCENARIOS[ScenarioId.WORLD_EDITING_COLLECTING]
    if workspace_stage == "review":
        return SCENARIOS[ScenarioId.WORLD_EDITING_REVIEW]
    if mode is OperatingMode.WORLD_MANAGEMENT:
        return SCENARIOS[ScenarioId.WORLD_SELECTION]
    if mode is OperatingMode.PREPARATION:
        return SCENARIOS[ScenarioId.PREPARATION]
    if pending_kind == "pool_confirmation":
        return SCENARIOS[ScenarioId.PLAY_PENDING_POOL]
    if pending_kind == "player_narration":
        return SCENARIOS[ScenarioId.PLAY_PENDING_NARRATION]
    if pending_kind is not None:
        return SCENARIOS[ScenarioId.PLAY_PENDING_OTHER]
    return SCENARIOS[ScenarioId.PLAY]


def match_explicit(scenario: Scenario, content: str) -> CommandId | None:
    if content.lstrip().startswith("/"):
        try:
            args = shlex.split(content)
        except ValueError:
            return None
        if not args:
            return None
        command = args[0].casefold()
        subcommand = args[1].casefold() if len(args) > 1 else "status"
        return scenario.slash_commands.get((command, subcommand)) or scenario.slash_commands.get(
            (command, "*")
        )
    return scenario.explicit_commands.get(normalize_phrase(content))


def has_slash_root(scenario: Scenario, command: str) -> bool:
    return any(root == command.casefold() for root, _subcommand in scenario.slash_commands)
