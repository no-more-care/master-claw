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
    PAUSED = "paused"
    FINISHED = "finished"
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
    PAUSE_GAME = "pause_game"
    RESUME_GAME = "resume_game"
    FINISH_GAME = "finish_game"
    UNBIND_GAME = "unbind_game"
    NEW_SESSION = "new_session"
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
        CommandId.PAUSE_GAME,
        CommandId.RESUME_GAME,
        CommandId.FINISH_GAME,
        CommandId.UNBIND_GAME,
        CommandId.NEW_SESSION,
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

_COMMON_SLASH = {("/help", "*"): CommandId.SHOW_HELP}
_WORLD_EDITOR_SLASH = _COMMON_SLASH | {
    ("/world", "exit"): CommandId.EXIT_WORLD_EDITOR,
    ("/world_exit", "status"): CommandId.EXIT_WORLD_EDITOR,
}

_MUTATING_COMMANDS = frozenset(
    {
        CommandId.CREATE_WORLD,
        CommandId.SELECT_WORLD,
        CommandId.REVISE_WORLD,
        CommandId.GENERATE_WORLD,
        CommandId.CONFIRM_WORLD,
        CommandId.EXIT_WORLD_EDITOR,
        CommandId.CREATE_CHARACTER,
        CommandId.SELECT_CHARACTER,
        CommandId.START_GAME,
        CommandId.CONFIGURE_GAME,
        CommandId.PAUSE_GAME,
        CommandId.RESUME_GAME,
        CommandId.FINISH_GAME,
        CommandId.UNBIND_GAME,
        CommandId.NEW_SESSION,
        CommandId.DECLARE_ACTION,
        CommandId.PLAYER_NARRATION,
        CommandId.REQUEST_ADVANCEMENT,
        CommandId.OFFER_HELP,
        CommandId.ANSWER_PENDING,
        CommandId.CANCEL_PENDING,
    }
)

_POLITE_EXPANDABLE_COMMANDS = frozenset(
    {
        CommandId.CREATE_WORLD,
        CommandId.GENERATE_WORLD,
        CommandId.CONFIRM_WORLD,
        CommandId.EXIT_WORLD_EDITOR,
        CommandId.START_GAME,
        CommandId.PAUSE_GAME,
        CommandId.RESUME_GAME,
        CommandId.FINISH_GAME,
        CommandId.UNBIND_GAME,
        CommandId.NEW_SESSION,
    }
)

_POLITE_PREFIXES = (
    ("пожалуйста",),
    ("да",),
    ("ладно",),
    ("хорошо",),
    ("ок",),
    ("please",),
    ("okay",),
    ("ok",),
)
_POLITE_SUFFIXES = (
    ("пожалуйста",),
    ("спасибо",),
    ("все", "хорошо"),
    ("все", "готово"),
    ("please",),
    ("thanks",),
    ("thank", "you"),
)


def is_assertive_mutation(content: str) -> bool:
    """Reject quoted, negated and hypothetical text as an explicit mutation."""
    stripped = content.strip()
    if any(
        mark in content
        for mark in ('"', "'", "«", "»", "“", "”", "„", "‘", "’", "‹", "›", "「", "」")
    ):
        return False
    # Explicit-only mutations must be direct prose, not an example copied from Markdown,
    # code, a quote, a checklist, or a link label. Normalization intentionally removes this
    # punctuation, so gate it before matching the normalized command lexicon.
    if (
        "`" in content
        or "~~" in content
        or re.search(r"(?m)^\s*(?:>|#{1,6}\s|[-+*]\s+)", content)
        or re.search(r"\[[^\]\r\n]+\](?:\([^)\r\n]+\))?", content)
        or (stripped.startswith("[") and stripped.endswith("]"))
        or any(
            len(stripped) > 2 * len(marker)
            and stripped.startswith(marker)
            and stripped.endswith(marker)
            for marker in ("***", "___", "**", "__", "*", "_")
        )
    ):
        return False
    normalized = normalize_phrase(content)
    tokens = set(normalized.split())
    if tokens & {"не", "ни", "нет", "not", "never", "dont", "cannot", "cant", "wont"}:
        return False
    if re.search(r"\b(?:don t|doesn t|do not|can t|won t)\b", normalized):
        return False
    if tokens & {"если", "когда", "вдруг", "if", "when", "unless", "maybe"}:
        return False
    return True


def is_world_creation_request(content: str) -> bool:
    """Conservatively recognize an affirmative request for another world."""
    if not is_assertive_mutation(content):
        return False
    normalized = normalize_phrase(content)
    return bool(
        re.search(
            r"(?:создай|создать|создадим|создайте|хочу создать|давай создадим)"
            r"(?: [a-zа-я0-9]+){0,4} мир(?: |$)",
            normalized,
        )
        or re.search(
            r"(?:create|make|build)(?: [a-z0-9]+){0,4} world(?: |$)",
            normalized,
        )
    )


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
        slash_commands=_COMMON_SLASH | {("/world", "create"): CommandId.CREATE_WORLD},
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
            выйди_из_создания_мира=CommandId.EXIT_WORLD_EDITOR,
            отменить_создание_мира=CommandId.EXIT_WORLD_EDITOR,
            отмени_создание_мира=CommandId.EXIT_WORLD_EDITOR,
        ),
        slash_commands=_WORLD_EDITOR_SLASH,
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
            выйди_из_создания_мира=CommandId.EXIT_WORLD_EDITOR,
            отменить_создание_мира=CommandId.EXIT_WORLD_EDITOR,
            отмени_создание_мира=CommandId.EXIT_WORLD_EDITOR,
        ),
        slash_commands=_WORLD_EDITOR_SLASH,
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
            начни_игру=CommandId.START_GAME,
            все_готовы_начинаем_игру=CommandId.START_GAME,
            все_готово_начинаем_игру=CommandId.START_GAME,
            запусти_игру=CommandId.START_GAME,
            start_game=CommandId.START_GAME,
            start_the_game=CommandId.START_GAME,
            everyone_is_ready_start_the_game=CommandId.START_GAME,
            отвязать_игру=CommandId.UNBIND_GAME,
            новая_игровая_сессия=CommandId.NEW_SESSION,
            unbind_game=CommandId.UNBIND_GAME,
            new_game_session=CommandId.NEW_SESSION,
        ),
        slash_commands=_COMMON_SLASH
        | {
            ("/game", "status"): CommandId.SHOW_GAME_STATUS,
            ("/game", "start"): CommandId.START_GAME,
            ("/game", "progression"): CommandId.CONFIGURE_GAME,
            ("/game", "rights"): CommandId.CONFIGURE_GAME,
            ("/game", "narrative"): CommandId.CONFIGURE_GAME,
            ("/game", "unbind"): CommandId.UNBIND_GAME,
            ("/game", "new"): CommandId.NEW_SESSION,
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
            поставь_игру_на_паузу=CommandId.PAUSE_GAME,
            приостанови_игру=CommandId.PAUSE_GAME,
            заверши_игру=CommandId.FINISH_GAME,
            закончить_игру=CommandId.FINISH_GAME,
            отвязать_игру=CommandId.UNBIND_GAME,
            новая_игровая_сессия=CommandId.NEW_SESSION,
            pause_game=CommandId.PAUSE_GAME,
            finish_game=CommandId.FINISH_GAME,
            unbind_game=CommandId.UNBIND_GAME,
            new_game_session=CommandId.NEW_SESSION,
        ),
        slash_commands=_COMMON_SLASH
        | {
            ("/game", "status"): CommandId.SHOW_GAME_STATUS,
            ("/xp", "status"): CommandId.SHOW_XP,
            ("/character", "status"): CommandId.SHOW_CHARACTER_SHEET,
            ("/character", "create"): CommandId.CREATE_CHARACTER,
            ("/advance", "raise"): CommandId.REQUEST_ADVANCEMENT,
            ("/advance", "learn"): CommandId.REQUEST_ADVANCEMENT,
            ("/advance", "status"): CommandId.SHOW_XP,
            ("/assist", "*"): CommandId.OFFER_HELP,
            ("/roll-help", "*"): CommandId.OFFER_HELP,
            ("/game", "pause"): CommandId.PAUSE_GAME,
            ("/game", "finish"): CommandId.FINISH_GAME,
            ("/game", "unbind"): CommandId.UNBIND_GAME,
            ("/game", "new"): CommandId.NEW_SESSION,
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
    ScenarioId.PAUSED: Scenario(
        id=ScenarioId.PAUSED,
        explicit_commands=_explicit(
            покажи_персонажа=CommandId.SHOW_CHARACTER_SHEET,
            покажи_статус=CommandId.SHOW_GAME_STATUS,
            покажи_опыт=CommandId.SHOW_XP,
            продолжить_игру=CommandId.RESUME_GAME,
            возобновить_игру=CommandId.RESUME_GAME,
            заверши_игру=CommandId.FINISH_GAME,
            отвязать_игру=CommandId.UNBIND_GAME,
            новая_игровая_сессия=CommandId.NEW_SESSION,
            resume_game=CommandId.RESUME_GAME,
            finish_game=CommandId.FINISH_GAME,
            unbind_game=CommandId.UNBIND_GAME,
            new_game_session=CommandId.NEW_SESSION,
        ),
        slash_commands=_COMMON_SLASH
        | {
            ("/game", "status"): CommandId.SHOW_GAME_STATUS,
            ("/game", "resume"): CommandId.RESUME_GAME,
            ("/game", "finish"): CommandId.FINISH_GAME,
            ("/game", "unbind"): CommandId.UNBIND_GAME,
            ("/game", "new"): CommandId.NEW_SESSION,
            ("/xp", "status"): CommandId.SHOW_XP,
            ("/character", "status"): CommandId.SHOW_CHARACTER_SHEET,
        },
        llm_commands=frozenset(
            {
                CommandId.SHOW_CHARACTER_SHEET,
                CommandId.SHOW_GAME_STATUS,
                CommandId.SHOW_XP,
                CommandId.SHOW_RULES,
                CommandId.SHOW_HELP,
                CommandId.CLARIFY,
            }
        ),
        context_projections=("actor_character",),
        recent_chat_messages=4,
    ),
    ScenarioId.FINISHED: Scenario(
        id=ScenarioId.FINISHED,
        explicit_commands=_explicit(
            покажи_персонажа=CommandId.SHOW_CHARACTER_SHEET,
            покажи_статус=CommandId.SHOW_GAME_STATUS,
            покажи_опыт=CommandId.SHOW_XP,
            отвязать_игру=CommandId.UNBIND_GAME,
            новая_игровая_сессия=CommandId.NEW_SESSION,
            unbind_game=CommandId.UNBIND_GAME,
            new_game_session=CommandId.NEW_SESSION,
        ),
        slash_commands=_COMMON_SLASH
        | {
            ("/game", "status"): CommandId.SHOW_GAME_STATUS,
            ("/game", "unbind"): CommandId.UNBIND_GAME,
            ("/game", "new"): CommandId.NEW_SESSION,
            ("/xp", "status"): CommandId.SHOW_XP,
            ("/character", "status"): CommandId.SHOW_CHARACTER_SHEET,
        },
        llm_commands=frozenset(
            {
                CommandId.SHOW_CHARACTER_SHEET,
                CommandId.SHOW_GAME_STATUS,
                CommandId.SHOW_XP,
                CommandId.SHOW_RULES,
                CommandId.SHOW_HELP,
                CommandId.CLARIFY,
            }
        ),
        context_projections=("actor_character",),
        recent_chat_messages=4,
    ),
}

WORLD_CREATION_PHRASES = frozenset(
    phrase
    for phrase, command in SCENARIOS[ScenarioId.WORLD_SELECTION].explicit_commands.items()
    if command is CommandId.CREATE_WORLD
)

_PENDING_SLASH_COMMANDS = _COMMON_SLASH | {
    ("/game", "status"): CommandId.SHOW_GAME_STATUS,
    ("/game", "pause"): CommandId.PAUSE_GAME,
    ("/game", "finish"): CommandId.FINISH_GAME,
    ("/game", "unbind"): CommandId.UNBIND_GAME,
    ("/game", "new"): CommandId.NEW_SESSION,
    ("/xp", "status"): CommandId.SHOW_XP,
    ("/character", "status"): CommandId.SHOW_CHARACTER_SHEET,
    ("/assist", "*"): CommandId.OFFER_HELP,
    ("/roll-help", "*"): CommandId.OFFER_HELP,
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
    if mode is OperatingMode.PAUSED:
        return SCENARIOS[ScenarioId.PAUSED]
    if mode is OperatingMode.FINISHED:
        return SCENARIOS[ScenarioId.FINISHED]
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
        matched = scenario.slash_commands.get((command, subcommand)) or scenario.slash_commands.get(
            (command, "*")
        )
        if matched in _EXPLICIT_ONLY_COMMANDS and not is_assertive_mutation(content):
            return None
        return matched

    normalized = normalize_phrase(content)
    matched = scenario.explicit_commands.get(normalized)
    if matched is not None:
        if matched in _MUTATING_COMMANDS and not is_assertive_mutation(content):
            return None
        return matched
    if not is_assertive_mutation(content):
        return None
    tokens = normalized.split()
    changed = True
    while tokens and changed:
        changed = False
        for prefix in _POLITE_PREFIXES:
            if tuple(tokens[: len(prefix)]) == prefix:
                del tokens[: len(prefix)]
                changed = True
                break
        for suffix in _POLITE_SUFFIXES:
            if tuple(tokens[-len(suffix) :]) == suffix:
                del tokens[-len(suffix) :]
                changed = True
                break
    matched = scenario.explicit_commands.get(" ".join(tokens))
    return matched if matched in _POLITE_EXPANDABLE_COMMANDS else None


def preparation_has_multiple_intents(content: str) -> bool:
    """Detect common compound setup requests before a singular-intent parser drops a part."""
    normalized = f" {normalize_phrase(content)} "
    if " и " not in normalized and " and " not in normalized:
        return False
    groups = (
        (
            " персонаж",
            "character ",
            " hero ",
            " играю за ",
            " play as ",
            " create my character",
            " choose a character",
        ),
        (" начать игру", " начинаем", " запусти игру", " start game", " start the game"),
        (
            " прогрес",
            " прокач",
            " опыт",
            " xp ",
            " progression",
            " experience ",
        ),
        (
            " права рассказ",
            " права нарратор",
            " игроки описыва",
            " игроки narrat",
            " narrator rights",
            " players narrate",
            " player narration rights",
        ),
        (
            " нарративн канал",
            " канал наррац",
            " narrative channel",
            " story channel",
        ),
        (" восстанов", " запас куб", " reserve recovery", " reserve dice recovery"),
    )
    matched_groups = sum(any(marker in normalized for marker in group) for group in groups)
    return matched_groups >= 2


def routes_unknown_slash_subcommand_to_usage(
    scenario: Scenario,
    content: str,
) -> bool:
    """Route only genuinely unknown arguments for an allowed root to its usage handler.

    A subcommand known in another lifecycle is not a bad argument: it is an unavailable
    operation and must be rejected by the current scenario.  This distinction prevents, for
    example, ``/world create`` from escaping an active world-editor workspace merely because
    ``/world exit`` gives that scenario the same slash root.
    """

    try:
        args = shlex.split(content)
    except ValueError:
        args = content.split()
    if not args:
        return False
    root = args[0].casefold()
    if not any(candidate_root == root for candidate_root, _ in scenario.slash_commands):
        return False
    subcommand = args[1].casefold() if len(args) > 1 else "status"
    globally_known = {
        candidate_subcommand
        for candidate in SCENARIOS.values()
        for (candidate_root, candidate_subcommand) in candidate.slash_commands
        if candidate_root == root and candidate_subcommand != "*"
    }
    return subcommand not in globally_known
