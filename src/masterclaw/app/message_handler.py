from __future__ import annotations

import logging
import shlex
from collections.abc import Callable

from masterclaw.app.action_service import ActionService
from masterclaw.app.advancement_coordinator import AdvancementCoordinator
from masterclaw.app.game_service import GameService, validate_id
from masterclaw.app.progression_service import ProgressionService
from masterclaw.context.assembler import ContextAssembler
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.mechanics import (
    STARTING_CHARACTER_RULES,
    CharacterSheet,
    Flag,
    MechanicsError,
    PoolProposal,
    Trait,
    validate_character,
)
from masterclaw.domain.models import (
    HandlerResponse,
    IncomingMessage,
    OperatingMode,
    OutboundDelivery,
)
from masterclaw.domain.routing import ModeRouter
from masterclaw.domain.state import NarratorRightsLevel
from masterclaw.pipelines.action import ActionInterpretation, ActionResolution
from masterclaw.pipelines.base import BoundedJsonPipeline
from masterclaw.pipelines.character_creation import CharacterDraft
from masterclaw.pipelines.consequence import SceneConsequencePlan
from masterclaw.pipelines.intent import IntentResult, MessageIntent
from masterclaw.pipelines.narrative import NarrativeResult
from masterclaw.pipelines.player_narration import PlayerNarrationReview
from masterclaw.pipelines.worldgen import WorldDraft
from masterclaw.storage.sqlite import SQLiteStore

logger = logging.getLogger(__name__)


class MessageApplication:
    """Initial message boundary: deterministic mode, pending lookup, bounded intent."""

    def __init__(
        self,
        *,
        store: SQLiteStore,
        context: ContextAssembler,
        intent_pipeline: BoundedJsonPipeline[IntentResult],
        action_pipeline: BoundedJsonPipeline[ActionInterpretation] | None = None,
        narrative_pipeline: BoundedJsonPipeline[NarrativeResult] | None = None,
        advancement: AdvancementCoordinator | None = None,
        player_narration_pipeline: BoundedJsonPipeline[PlayerNarrationReview] | None = None,
        die: Callable[[], int] | None = None,
        worldgen_pipeline: BoundedJsonPipeline[WorldDraft] | None = None,
        character_pipeline: BoundedJsonPipeline[CharacterDraft] | None = None,
        consequence_pipeline: BoundedJsonPipeline[SceneConsequencePlan] | None = None,
    ) -> None:
        self._store = store
        self._context = context
        self._intent = intent_pipeline
        self._router = ModeRouter()
        self._progression = ProgressionService(store)
        self._actions = ActionService(store)
        self._action_pipeline = action_pipeline
        self._narrative_pipeline = narrative_pipeline
        self._advancement = advancement
        self._games = GameService(store)
        self._player_narration_pipeline = player_narration_pipeline
        self._die = die
        self._worldgen_pipeline = worldgen_pipeline
        self._character_pipeline = character_pipeline
        self._consequence_pipeline = consequence_pipeline

    async def __call__(self, message: IncomingMessage) -> str:
        command = self._command(message.content)
        channel = self._store.channel_state(message.channel_id)
        route = self._router.route(command=command, channel=channel)
        if route.mode is OperatingMode.PLAY and channel.game_id is not None:
            self._store.record_activity(game_id=channel.game_id, occurred_at=message.created_at)
        command_response = await self._handle_command(
            message=message, game_id=channel.game_id, command=command
        )
        if command_response is not None:
            return command_response
        resumed_roll = self._store.roll_for_confirmation_event(message.event_id)
        if resumed_roll is not None:
            return await self._render_roll_outcome(message=message, roll=resumed_roll)
        pending = None
        if channel.game_id:
            pending = self._store.open_pending(game_id=channel.game_id, player_id=message.author_id)

        projections = {
            "mode": {"value": route.mode.value, "reason": route.reason},
            "pending_interaction": (
                None
                if pending is None
                else {
                    "id": pending.interaction_id,
                    "kind": pending.kind.value,
                    "prompt": pending.prompt,
                    "scene_id": pending.scene_id,
                }
            ),
        }
        deterministic = self._deterministic_intent(
            command=command, pending=pending, content=message.content
        )
        if deterministic is None:
            assembled = self._context.assemble(
                manifest_for(PipelineName.INTENT_CLASSIFICATION), projections
            )
            result = await self._intent.run(
                task=f"Classify this player message exactly once:\n{message.content}",
                dynamic_context=assembled.text,
            )
            intent = result.intent
        else:
            intent = deterministic
        if (
            route.mode is OperatingMode.PLAY
            and intent in {MessageIntent.ACTION_DECLARATION, MessageIntent.MIXED_NARRATION_ACTION}
            and channel.game_id is not None
        ):
            return await self._handle_action_declaration(message=message, game_id=channel.game_id)
        if intent is MessageIntent.PENDING_RESPONSE and pending is not None:
            if pending.kind.value == "player_narration":
                return await self._handle_player_narration(message=message, pending=pending)
            return await self._handle_pending_response(message=message, pending=pending)
        return self._safe_boundary_response(route.mode, intent)

    @staticmethod
    def _command(content: str) -> str | None:
        stripped = content.strip()
        return stripped.split(maxsplit=1)[0] if stripped.startswith("/") else None

    @staticmethod
    def _deterministic_intent(
        *, command: str | None, pending, content: str
    ) -> MessageIntent | None:
        if command is not None:
            return MessageIntent.COMMAND
        if pending is not None:
            if pending.kind.value == "player_narration":
                return MessageIntent.PENDING_RESPONSE
            normalized = content.strip().lower()
            if normalized in {"да", "нет", "yes", "no", "confirm", "cancel", "отмена"}:
                return MessageIntent.PENDING_RESPONSE
            if normalized.isdecimal():
                return MessageIntent.PENDING_RESPONSE
        return None

    async def _handle_player_narration(
        self, *, message: IncomingMessage, pending
    ) -> str | HandlerResponse:
        if self._player_narration_pipeline is None:
            return "Проверка прав рассказчика не настроена."
        game = self._store.game_state(pending.game_id)
        scene = self._store.scene_projection(game_id=pending.game_id, player_id=message.author_id)
        roll = self._store.roll_by_id(str(pending.payload.get("roll_id", "")))
        if game is None or scene is None or roll is None:
            return "Не удалось восстановить контекст прав рассказчика."
        assembled = self._context.assemble(
            manifest_for(PipelineName.PLAYER_NARRATION_REVIEW),
            {
                "current_scene": scene,
                "roll_result": {
                    "hits": roll.hits,
                    "difficulty": roll.difficulty,
                    "narrator_rights": roll.narrator_rights.value,
                    "narrator_rights_level": game.narrator_rights_level.value,
                },
                "submitted_narration": message.content,
            },
        )
        review = await self._player_narration_pipeline.run(
            task="Review the submitted player narration.",
            dynamic_context=assembled.text,
        )
        if not review.accepted:
            return review.scale_back_request or f"Нарратив отклонён: {review.reason}"
        if self._consequence_pipeline is not None:
            await self._ensure_scene_consequence(
                game_id=pending.game_id,
                scene=scene,
                causation_id=f"player-narration:{pending.interaction_id}",
                outcome_source={
                    "kind": "player_narration",
                    "text": message.content,
                    "roll_id": roll.roll_id,
                },
                narrator_rights=roll.narrator_rights.value,
            )
        self._store.resolve_pending(
            interaction_id=pending.interaction_id,
            player_id=message.author_id,
            expected_revision=pending.revision,
        )
        if game.narrative_channel_id is None:
            return "Нарратив принят."
        return HandlerResponse(
            "Нарратив принят.",
            (OutboundDelivery(game.narrative_channel_id, message.content, "player_narration"),),
        )

    @staticmethod
    def _safe_boundary_response(mode: OperatingMode, intent: MessageIntent) -> str:
        # This response is intentionally explicit until downstream use cases exist.
        labels = {
            OperatingMode.WORLD_MANAGEMENT: "управление миром",
            OperatingMode.PREPARATION: "подготовка игры",
            OperatingMode.PLAY: "игра",
        }
        return f"Режим: {labels[mode]}. Запрос принят как `{intent.value}`."

    async def _handle_command(
        self,
        *,
        message: IncomingMessage,
        game_id: str | None,
        command: str | None,
    ) -> str | None:
        if command not in {
            "/xp",
            "/game",
            "/advance",
            "/world",
            "/character",
            "/help",
        }:
            return None
        try:
            args = shlex.split(message.content)
        except ValueError as error:
            return f"Не удалось разобрать команду: {error}"
        if command == "/world":
            if len(args) == 4 and args[1].lower() == "create":
                try:
                    world = self._games.create_world(world_id=args[2], title=args[3])
                except (ValueError, RuntimeError) as error:
                    return f"Мир не создан: {error}"
                return f"Создан черновик мира `{world.world_id}` — {world.title}."
            if len(args) == 4 and args[1].lower() == "generate":
                if self._worldgen_pipeline is None:
                    return "Пайплайн генерации миров не настроен."
                world = self._store.world_state(args[2])
                if world is None:
                    return "Сначала создайте черновик мира командой `/world create`."
                assembled = self._context.assemble(
                    manifest_for(PipelineName.WORLD_SECTION),
                    {
                        "world_outline": {"world_id": world.world_id, "title": world.title},
                        "world_constraints": {"brief": args[3]},
                        "target_section": "full_initial_draft",
                    },
                )
                draft = await self._worldgen_pipeline.run(
                    task=f"Generate the world draft from this brief:\n{args[3]}",
                    dynamic_context=assembled.text,
                )
                self._store.update_world_content(
                    world_id=world.world_id,
                    expected_revision=world.revision,
                    content=draft.model_dump(mode="json"),
                )
                return (
                    f"Мир `{world.world_id}` сгенерирован: "
                    f"{len(draft.locations)} локаций, {len(draft.factions)} фракций."
                )
            return (
                'Формат: `/world create world_id "Название мира"` или '
                '`/world generate world_id "Описание"`.'
            )
        if command == "/game" and len(args) >= 2 and args[1].lower() == "prepare":
            if len(args) not in {4, 5}:
                return "Формат: `/game prepare game_id world_id [ru|en]`."
            try:
                game = self._games.prepare_game(
                    game_id=args[2],
                    world_id=args[3],
                    channel_id=message.channel_id,
                    locale=args[4] if len(args) == 5 else "ru",
                )
            except (ValueError, RuntimeError) as error:
                return f"Игра не подготовлена: {error}"
            return f"Игра `{game.game_id}` создана в режиме подготовки."
        if game_id is None:
            return "В этом канале ещё нет привязанной игры."
        subcommand = args[1].lower() if len(args) > 1 else "status"
        if command == "/help":
            if len(args) != 2:
                return "Формат: `/help @игрок`."
            target_player_id = args[1].strip("<@!>")
            try:
                pending = self._store.offer_help(
                    game_id=game_id,
                    helper_player_id=message.author_id,
                    target_player_id=target_player_id,
                )
            except ValueError as error:
                return f"Помощь не добавлена: {error}"
            return (
                f"К пулу <@{target_player_id}> добавлен 1 куб помощи. "
                f"Ожидается подтверждение `{pending.interaction_id}`."
            )
        if command == "/character" and subcommand == "create":
            if len(args) != 4:
                return 'Формат: `/character create character_id "Описание персонажа"`.'
            if self._character_pipeline is None:
                return "Пайплайн создания персонажей не настроен."
            game = self._store.game_state(game_id)
            if game is None or game.lifecycle.value != "preparing":
                return "Создавать стартового персонажа можно только при подготовке игры."
            if (
                self._store.character_for_player(game_id=game_id, player_id=message.author_id)
                is not None
            ):
                return "У вас уже есть персонаж в этой игре."
            try:
                validate_id(args[2], field="character_id")
            except ValueError as error:
                return f"Персонаж не создан: {error}"
            world = self._store.world_content(game.world_id) or {}
            public_world = {key: value for key, value in world.items() if key != "secret_plot"}
            assembled = self._context.assemble(
                manifest_for(PipelineName.CHARACTER_CREATION),
                {"public_world": public_world, "player_brief": args[3]},
            )
            draft = await self._character_pipeline.run(
                task="Create a validated starting character.",
                dynamic_context=assembled.text,
            )
            sheet = CharacterSheet(
                name=draft.name,
                traits=tuple(
                    Trait(item.name, item.level, tuple(item.aspects)) for item in draft.traits
                ),
                flags=tuple(Flag(item.text, item.type, locked=True) for item in draft.flags),
            )
            try:
                validate_character(sheet, STARTING_CHARACTER_RULES)
                self._store.create_character(
                    CharacterState(args[2], game_id, message.author_id, draft.biography, sheet)
                )
            except (ValueError, RuntimeError) as error:
                return f"Персонаж не прошёл проверку: {error}"
            return f"Создан персонаж `{args[2]}` — {sheet.name}."
        if command == "/character" and subcommand == "place":
            if len(args) != 3:
                return "Формат: `/character place scene_id`."
            if (
                self._store.character_for_player(game_id=game_id, player_id=message.author_id)
                is None
            ):
                return "Сначала создайте персонажа."
            try:
                self._store.place_player(
                    game_id=game_id,
                    player_id=message.author_id,
                    scene_id=args[2],
                )
            except (ValueError, RuntimeError) as error:
                return f"Персонаж не размещён: {error}"
            return f"Персонаж размещён в сцене `{args[2]}`."
        if command == "/character" and subcommand == "status":
            character = self._store.character_for_player(
                game_id=game_id, player_id=message.author_id
            )
            if character is None:
                return "У вас ещё нет персонажа в этой игре."
            traits = ", ".join(
                f"{trait.name} {trait.level} ({'; '.join(trait.aspects)})"
                for trait in character.sheet.traits
            )
            conditions = ", ".join(item.text for item in character.conditions) or "нет"
            items = ", ".join(item.name for item in character.plot_items) or "нет"
            return (
                f"Персонаж `{character.character_id}` — {character.sheet.name}. "
                f"Черты: {traits}. Резерв: {character.sheet.reserve_current}/"
                f"{character.sheet.reserve_maximum}. Опыт: {character.experience_available} "
                f"(получено {character.experience_earned}, "
                f"потрачено {character.experience_spent}). "
                f"Состояния: {conditions}. Сюжетные предметы: {items}."
            )
        if command == "/xp" and subcommand == "status":
            activity = self._store.activity_state(game_id)
            game = self._store.game_state(game_id)
            active_minutes = activity["active_seconds"] // 60
            return (
                f"Прогрессия: {'включена' if game and game.progression_enabled else 'выключена'}. "
                f"Активное время: {active_minutes} мин. "
                f"Автоматически начислено полных интервалов: {activity['awarded_intervals']}."
            )
        if command == "/game" and subcommand == "status":
            game = self._store.game_state(game_id)
            if game is None:
                return "Игра не найдена."
            return (
                f"Игра `{game.game_id}`: {game.lifecycle.value}; "
                f"мир `{game.world_id}`; язык `{game.locale}`; "
                f"прогрессия: {'включена' if game.progression_enabled else 'выключена'}; "
                f"права рассказчика: `{game.narrator_rights_level.value}`."
            )
        if command == "/game" and subcommand == "progression":
            if len(args) != 3 or args[2].lower() not in {"on", "off"}:
                return "Формат: `/game progression on|off`."
            try:
                game = self._games.configure_progression(
                    game_id=game_id, enabled=args[2].lower() == "on"
                )
            except (ValueError, RuntimeError) as error:
                return f"Настройка не изменена: {error}"
            return f"Прогрессия {'включена' if game.progression_enabled else 'выключена'}."
        if command == "/game" and subcommand == "rights":
            if len(args) != 3:
                return "Формат: `/game rights disabled|minor|significant|madness`."
            try:
                level = NarratorRightsLevel(args[2].lower())
                game = self._games.configure_narrator_rights(game_id=game_id, level=level)
            except (ValueError, RuntimeError) as error:
                return f"Уровень прав не изменён: {error}"
            return f"Уровень прав рассказчика: `{game.narrator_rights_level.value}`."
        if command == "/game" and subcommand == "narrative":
            if len(args) != 3:
                return "Формат: `/game narrative channel_id`."
            channel_id = args[2].strip("<#>")
            game = self._store.game_state(game_id)
            try:
                self._store.set_narrative_channel(
                    game_id=game_id,
                    channel_id=channel_id,
                    expected_revision=game.revision,
                )
            except RuntimeError as error:
                return f"Нарративный канал не изменён: {error}"
            return f"Нарративный канал установлен: <#{channel_id}>."
        if command == "/game" and subcommand == "scene":
            if len(args) != 4:
                return 'Формат: `/game scene scene_id "Название сцены"`.'
            try:
                self._store.create_scene(scene_id=args[2], game_id=game_id, title=args[3])
            except Exception as error:
                return f"Сцена не создана: {error}"
            return f"Создана сцена `{args[2]}`."
        if command == "/game" and subcommand == "start":
            try:
                game = self._games.start_game(game_id, started_at=message.created_at)
            except (ValueError, RuntimeError) as error:
                return f"Игра не запущена: {error}"
            return f"Игра `{game.game_id}` запущена."
        if command == "/advance":
            if self._advancement is None:
                return "Пайплайн прокачки не настроен."
            try:
                args = shlex.split(message.content)
            except ValueError as error:
                return f"Не удалось разобрать команду: {error}"
            try:
                if len(args) == 4 and args[1].lower() == "raise":
                    updated = await self._advancement.raise_trait(
                        game_id=game_id,
                        player_id=message.author_id,
                        trait_name=args[2],
                        new_aspect=args[3],
                    )
                    return (
                        f"Черта `{args[2]}` повышена. "
                        f"Доступный опыт: {updated.experience_available}."
                    )
                if len(args) == 6 and args[1].lower() == "learn":
                    updated = await self._advancement.learn_trait(
                        game_id=game_id,
                        player_id=message.author_id,
                        trait_name=args[2],
                        aspects=(args[3], args[4]),
                        justification=args[5],
                    )
                    return (
                        f"Получена новая черта `{args[2]}` уровня 2. "
                        f"Доступный опыт: {updated.experience_available}."
                    )
            except ValueError as error:
                return f"Прокачка отклонена: {error}"
            return (
                'Формат: `/advance raise "Черта" "Новый аспект"` или '
                '`/advance learn "Черта" "Аспект 1" "Аспект 2" "Обоснование"`.'
            )
        return (
            "Неизвестная команда. Доступно: `/world create`, `/game prepare`, "
            "`/game progression`, `/game rights`, `/game narrative`, `/game scene`, "
            "`/game start`, `/game status`, `/character create`, `/character place`, "
            "`/character status`, `/xp status`, `/advance`, `/help`."
        )

    async def _handle_action_declaration(self, *, message: IncomingMessage, game_id: str) -> str:
        if self._action_pipeline is None:
            return "Обработка игровых деклараций ещё не настроена."
        character = self._store.character_for_player(game_id=game_id, player_id=message.author_id)
        scene = self._store.scene_projection(game_id=game_id, player_id=message.author_id)
        if character is None:
            return "Сначала создайте персонажа для этой игры."
        if scene is None:
            return "Персонаж пока не размещён в игровой сцене."
        sheet = character.sheet
        actor_projection = {
            "character_id": character.character_id,
            "revision": character.revision,
            "name": sheet.name,
            "traits": [
                {"name": trait.name, "level": trait.level, "aspects": list(trait.aspects)}
                for trait in sheet.traits
            ],
            "flags": [flag.text for flag in sheet.flags],
            "reserve": sheet.reserve_current,
            "conditions": [condition.text for condition in character.conditions],
            "plot_items": [item.name for item in character.plot_items],
        }
        assembled = self._context.assemble(
            manifest_for(PipelineName.ACTION_INTERPRETATION),
            {
                "session_brief": {
                    "game_id": game_id,
                    "participants_here": scene["participants"],
                },
                "actor_character": actor_projection,
                "current_scene": scene,
            },
        )
        result = await self._action_pipeline.run(
            task=f"Interpret the declaration:\n{message.content}",
            dynamic_context=assembled.text,
        )
        if result.resolution is ActionResolution.CLARIFICATION:
            return result.clarification_question or "Уточните действие."
        if result.resolution is ActionResolution.AUTOMATIC:
            if self._consequence_pipeline is None:
                return "Пайплайн последствий автоматических действий не настроен."
            await self._ensure_scene_consequence(
                game_id=game_id,
                scene=scene,
                causation_id=f"automatic:{message.event_id}",
                outcome_source={
                    "kind": "automatic_action",
                    "declaration": message.content,
                    "evidence": result.evidence,
                },
                narrator_rights="gm_automatic",
            )
            game = self._store.game_state(game_id)
            updated_scene = self._store.scene_projection(
                game_id=game_id, player_id=message.author_id
            )
            if (
                self._narrative_pipeline is None
                or game is None
                or game.narrative_channel_id is None
            ):
                return "Действие выполнено без броска; состояние сцены обновлено."
            assembled_narrative = self._context.assemble(
                manifest_for(PipelineName.OUTCOME_NARRATION),
                {
                    "session_brief": {"game_id": game_id, "locale": game.locale},
                    "current_scene": updated_scene,
                    "roll_result": {
                        "resolution": "automatic",
                        "declaration": message.content,
                    },
                },
            )
            narrative = await self._narrative_pipeline.run(
                task="Narrate the automatic action outcome.",
                dynamic_context=assembled_narrative.text,
            )
            return HandlerResponse(
                "Действие выполнено без броска.",
                (OutboundDelivery(game.narrative_channel_id, narrative.narrative, "narrative"),),
            )
        try:
            pending = self._actions.propose_roll(
                game_id=game_id,
                player_id=message.author_id,
                scene_id=str(scene["scene_id"]),
                proposal=PoolProposal(
                    trait_names=tuple(result.trait_names),
                    aspect_names=tuple(result.aspect_names),
                    flag=result.flag,
                    reserve_spent=0,
                    difficulty=int(result.difficulty),
                ),
                prompt="Подтвердите пул и укажите резерв.",
                declaration=message.content,
            )
        except MechanicsError as error:
            return f"Предложенный пул не прошёл проверку: {error}. Уточните заявку."
        payload = pending.payload
        return (
            f"Пул: {payload['pool_size']} куб.; сложность: {payload['difficulty']}. "
            f"Резерв доступен: {sheet.reserve_current}. "
            "Ответьте числом добавляемых кубов резерва (0 тоже допустим) или `отмена`."
        )

    async def _handle_pending_response(
        self, *, message: IncomingMessage, pending
    ) -> str | HandlerResponse:
        normalized = message.content.strip().lower()
        if normalized in {"отмена", "cancel", "нет", "no"}:
            self._store.cancel_pending(
                interaction_id=pending.interaction_id,
                player_id=message.author_id,
                expected_revision=pending.revision,
            )
            return "Бросок отменён."
        try:
            reserve_spent = 0 if normalized in {"да", "yes", "confirm"} else int(normalized)
        except ValueError:
            return "Укажите число кубов резерва или `отмена`."
        try:
            roll = self._actions.confirm_roll(
                interaction_id=pending.interaction_id,
                player_id=message.author_id,
                reserve_spent=reserve_spent,
                confirmation_event_id=message.event_id,
                die=self._die,
            )
        except (MechanicsError, ValueError) as error:
            return (
                f"Подтверждение не прошло проверку: {error}. "
                "Укажите допустимый резерв или `отмена`."
            )
        return await self._render_roll_outcome(message=message, roll=roll)

    async def _render_roll_outcome(
        self, *, message: IncomingMessage, roll
    ) -> str | HandlerResponse:
        dice = ", ".join(str(value) for value in roll.dice)
        mechanical = (
            f"🎲 [{dice}] — успехов: {roll.hits}, сложность: {roll.difficulty}. "
            f"Права рассказчика: `{roll.narrator_rights.value}`. "
            f"Резерв: {roll.reserve_after}/7."
        )
        if roll.narrator_rights.value.startswith("player_"):
            return mechanical + " Опишите исход действия в пределах полученных прав рассказчика."
        game = self._store.game_state(roll.game_id)
        if self._narrative_pipeline is None or game is None or game.narrative_channel_id is None:
            return mechanical
        scene = self._store.scene_projection(game_id=roll.game_id, player_id=message.author_id)
        if scene is None:
            return mechanical
        resolved_pending = self._store.pending_by_id(roll.interaction_id)
        declaration = (
            str(resolved_pending.payload.get("declaration", ""))
            if resolved_pending is not None
            else ""
        )
        if self._consequence_pipeline is not None:
            await self._ensure_scene_consequence(
                game_id=roll.game_id,
                scene=scene,
                causation_id=f"roll:{roll.roll_id}",
                outcome_source={
                    "kind": "roll",
                    "declaration": declaration,
                    "hits": roll.hits,
                    "difficulty": roll.difficulty,
                },
                narrator_rights=roll.narrator_rights.value,
            )
            scene = self._store.scene_projection(game_id=roll.game_id, player_id=message.author_id)
        assembled = self._context.assemble(
            manifest_for(PipelineName.OUTCOME_NARRATION),
            {
                "session_brief": {
                    "game_id": roll.game_id,
                    "locale": game.locale,
                    "participants_here": scene["participants"],
                },
                "current_scene": scene,
                "roll_result": {
                    "declaration": declaration,
                    "dice": roll.dice,
                    "hits": roll.hits,
                    "difficulty": roll.difficulty,
                    "narrator_rights": roll.narrator_rights.value,
                    "narrator_rights_level": game.narrator_rights_level.value,
                },
            },
        )
        try:
            narrative = await self._narrative_pipeline.run(
                task="Narrate the resolved action outcome without changing its mechanics.",
                dynamic_context=assembled.text,
            )
            narrative_text = narrative.narrative
        except Exception:
            logger.exception(
                "Narrative pipeline failed for roll %s; using safe fallback",
                roll.roll_id,
            )
            narrative_text = (
                "Исход действия зафиксирован, но подробное описание сцены временно недоступно."
                if game.locale == "ru"
                else (
                    "The action outcome is recorded, but the detailed scene description "
                    "is temporarily unavailable."
                )
            )
        return HandlerResponse(
            text=mechanical,
            deliveries=(
                OutboundDelivery(
                    channel_id=game.narrative_channel_id,
                    content=narrative_text,
                    kind="narrative",
                ),
            ),
        )

    async def _ensure_scene_consequence(
        self,
        *,
        game_id: str,
        scene: dict[str, object],
        causation_id: str,
        outcome_source: dict[str, object],
        narrator_rights: str,
    ) -> None:
        if self._store.has_scene_patch(causation_id):
            return
        if self._consequence_pipeline is None:
            raise RuntimeError("consequence pipeline is not configured")
        assembled = self._context.assemble(
            manifest_for(PipelineName.CONSEQUENCE_PLANNING),
            {
                "current_scene": scene,
                "outcome_source": outcome_source,
                "narrator_rights": narrator_rights,
            },
        )
        plan = await self._consequence_pipeline.run(
            task="Produce the minimal persistent scene patch for this outcome.",
            dynamic_context=assembled.text,
        )
        self._store.apply_scene_patch(
            game_id=game_id,
            scene_id=str(scene["scene_id"]),
            expected_revision=int(scene["scene_revision"]),
            causation_id=causation_id,
            summary=plan.summary,
            add_facts=plan.add_facts,
            remove_facts=plan.remove_facts,
        )
