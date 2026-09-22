from __future__ import annotations

import logging
import shlex

from masterclaw.app.decision_checkpoints import run_checkpointed_decision
from masterclaw.app.game_service import validate_id
from masterclaw.app.handlers.types import WorldWorkspaceStage
from masterclaw.app.i18n import tr
from masterclaw.app.scenarios import CommandId
from masterclaw.app.world_settings import (
    WORLD_SETTING_DEFAULTS,
)
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.discord_ids import parse_discord_channel_id
from masterclaw.domain.mechanics import (
    STARTING_CHARACTER_RULES,
    CharacterSheet,
    Flag,
    Trait,
    validate_character,
)
from masterclaw.domain.models import (
    HandlerResponse,
    IncomingMessage,
)
from masterclaw.domain.state import NarratorRightsLevel, WorldState
from masterclaw.pipelines.base import PipelineValidationError

logger = logging.getLogger(__name__)


class CommandHandlers:
    async def _handle_command(
        self,
        *,
        message: IncomingMessage,
        game_id: str | None,
        command: str | None,
        routed_command: CommandId | None,
    ) -> str | HandlerResponse | None:
        try:
            args = shlex.split(message.content)
        except ValueError as error:
            return tr(self._locale(game_id), "command_parse_error", error=error)
        locale = self._locale(game_id)
        if routed_command is None:
            usage_keys = {
                "/advance": "advance_usage",
                "/character": "character_usage",
                "/game": "game_usage",
                "/help": "help_usage",
                "/world": "world_usage",
                "/xp": "xp_usage",
            }
            return tr(locale, usage_keys.get(command, "unknown_command"))
        if routed_command is CommandId.CREATE_WORLD:
            if len(args) == 4 and args[1].lower() == "create":
                world_id = args[2]
                title = args[3].strip()
                try:
                    validate_id(world_id, field="world_id")
                    if not title:
                        raise ValueError("world title cannot be empty")
                    world = WorldState(world_id=world_id, title=title)
                    settings = {"title": world.title, **WORLD_SETTING_DEFAULTS}
                    self._store.save_world_workspace(
                        channel_id=message.channel_id,
                        world_id=world.world_id,
                        stage=WorldWorkspaceStage.COLLECTING.value,
                        brief=(
                            f"Новый мир: {world.title}. "
                            "Требуется заполнить вводные перед генерацией."
                        ),
                        settings=settings,
                        sources={key: "default" for key in settings},
                        world=world,
                    )
                except (ValueError, RuntimeError) as error:
                    return tr(locale, "world_create_failed", error=error)
                return tr(locale, "world_created", world_id=world.world_id, title=world.title)
            return tr(locale, "world_usage")
        if routed_command is CommandId.EXIT_WORLD_EDITOR:
            valid_exit = (
                len(args) == 2 and args[0].casefold() == "/world" and args[1].casefold() == "exit"
            ) or (len(args) == 1 and args[0].casefold() == "/world_exit")
            workspace = self._store.world_workspace(message.channel_id)
            if not valid_exit or workspace is None:
                return tr(locale, "world_usage")
            return self._handle_world_exit(message=message, workspace=workspace)
        if game_id is None:
            return tr(locale, "game_unbound")
        operation_event_id = (
            message.event_id if self._has_durable_inbox_event(message.event_id) else None
        )
        subcommand = args[1].lower() if len(args) > 1 else "status"
        if routed_command is CommandId.OFFER_HELP:
            if len(args) != 2:
                return tr(locale, "help_usage")
            target_player_id = self._help_target_player_id(game_id=game_id, selector=args[1])
            if target_player_id is None:
                return tr(locale, "help_usage")
            try:
                pending = self._store.offer_help(
                    game_id=game_id,
                    helper_player_id=message.author_id,
                    target_player_id=target_player_id,
                    event_id=operation_event_id,
                    channel_id=(message.channel_id if operation_event_id is not None else None),
                )
            except ValueError as error:
                return tr(locale, "help_failed", error=error)
            return tr(
                locale,
                "help_added",
                player_id=target_player_id,
                interaction_id=pending.interaction_id,
            )
        if routed_command is CommandId.CREATE_CHARACTER:
            if len(args) != 4:
                return tr(locale, "character_create_usage")
            game = self._store.game_state(game_id)
            if game is None or game.lifecycle.value not in {"preparing", "active", "paused"}:
                return tr(locale, "character_prepare_only")
            existing = self._store.character_for_player(
                game_id=game_id,
                player_id=message.author_id,
            )
            if existing is not None:
                if (
                    game.lifecycle.value != "preparing"
                    and self._store.scene_projection(
                        game_id=game_id,
                        player_id=message.author_id,
                    )
                    is None
                    and self._place_joining_character(
                        game_id=game_id,
                        player_id=message.author_id,
                    )
                ):
                    return tr(
                        locale,
                        "character_created_joined",
                        character_id=existing.character_id,
                        name=existing.sheet.name,
                    )
                return tr(locale, "character_exists")
            if self._character_pipeline is None:
                return tr(locale, "character_pipeline_unavailable")
            try:
                validate_id(args[2], field="character_id")
            except ValueError as error:
                return tr(locale, "character_create_failed", error=error)
            world = self._store.world_content(game.world_id) or {}
            public_world = {key: value for key, value in world.items() if key != "secret_plot"}
            manifest = manifest_for(PipelineName.CHARACTER_CREATION)
            assembled = self._assemble_context(
                manifest,
                {"public_world": public_world, "player_brief": args[3]},
                game_id=game_id,
                channel_id=message.channel_id,
                player_id=message.author_id,
            )
            try:
                draft = await run_checkpointed_decision(
                    store=self._store,
                    event_id=message.event_id,
                    pipeline_key="character_creation",
                    pipeline=self._character_pipeline,
                    task="Create a validated starting character.",
                    context=assembled,
                    game_id=game_id,
                )
            except PipelineValidationError:
                logger.warning(
                    "character_creation_invalid event_id=%s", message.event_id, exc_info=True
                )
                return tr(locale, manifest.on_invalid.value)
            sheet = CharacterSheet(
                name=draft.name,
                traits=tuple(
                    Trait(item.name, item.level, tuple(item.aspects)) for item in draft.traits
                ),
                flags=tuple(Flag(item.text, item.type, locked=True) for item in draft.flags),
            )
            if self._character_name_taken(game_id=game_id, name=sheet.name):
                return tr(locale, "character_name_taken", name=sheet.name)
            try:
                validate_character(sheet, STARTING_CHARACTER_RULES)
                self._store.create_character(
                    CharacterState(args[2], game_id, message.author_id, draft.biography, sheet)
                )
            except (ValueError, RuntimeError) as error:
                return tr(locale, "character_invalid", error=error)
            if game.lifecycle.value != "preparing":
                self._place_joining_character(game_id=game_id, player_id=message.author_id)
            return tr(locale, "character_created", character_id=args[2], name=sheet.name)
        if routed_command is CommandId.SHOW_CHARACTER_SHEET:
            return self._character_status(game_id=game_id, player_id=message.author_id)
        if routed_command is CommandId.SHOW_XP:
            return self._xp_status(game_id, player_id=message.author_id)
        if routed_command is CommandId.SHOW_GAME_STATUS:
            return self._game_status(game_id)
        if routed_command is CommandId.CONFIGURE_GAME and subcommand == "progression":
            if len(args) != 3 or args[2].lower() not in {"on", "off"}:
                return tr(locale, "progression_usage")
            try:
                game = self._games.configure_progression(
                    game_id=game_id,
                    enabled=args[2].lower() == "on",
                    event_id=operation_event_id,
                    channel_id=message.channel_id,
                )
            except (ValueError, RuntimeError) as error:
                return tr(locale, "setting_failed", error=error)
            return tr(
                locale,
                "progression_changed",
                progression=tr(locale, "enabled" if game.progression_enabled else "disabled"),
            )
        if routed_command is CommandId.CONFIGURE_GAME and subcommand == "rights":
            if len(args) != 3:
                return tr(locale, "rights_usage")
            try:
                level = NarratorRightsLevel(args[2].lower())
                game = self._games.configure_narrator_rights(
                    game_id=game_id,
                    level=level,
                    event_id=operation_event_id,
                    channel_id=message.channel_id,
                )
            except (ValueError, RuntimeError) as error:
                return tr(locale, "rights_failed", error=error)
            return tr(locale, "rights_changed", rights=game.narrator_rights_level.value)
        if routed_command is CommandId.CONFIGURE_GAME and subcommand == "narrative":
            if len(args) != 3:
                return tr(locale, "narrative_channel_usage")
            try:
                channel_id = parse_discord_channel_id(args[2])
            except ValueError:
                return tr(locale, "narrative_channel_usage")
            try:
                self._games.configure_narrative_channel(
                    game_id=game_id,
                    narrative_channel_id=channel_id,
                    event_id=operation_event_id,
                    channel_id=message.channel_id,
                )
            except (ValueError, RuntimeError) as error:
                return tr(locale, "narrative_channel_failed", error=error)
            return tr(locale, "narrative_channel_changed", channel_id=channel_id)
        if routed_command is CommandId.START_GAME:
            try:
                game = self._games.start_game(
                    game_id,
                    started_at=message.created_at,
                    event_id=operation_event_id,
                    channel_id=message.channel_id,
                )
            except (ValueError, RuntimeError) as error:
                return tr(locale, "game_start_failed", error=error)
            return HandlerResponse(
                tr(locale, "game_started", game_id=game.game_id),
                render_live_status=True,
            )
        if routed_command is CommandId.PAUSE_GAME:
            try:
                self._games.pause_game(
                    game_id,
                    event_id=operation_event_id,
                    channel_id=message.channel_id,
                )
            except (ValueError, RuntimeError) as error:
                return tr(locale, "game_pause_failed", error=error)
            return HandlerResponse(tr(locale, "game_paused"), render_live_status=True)
        if routed_command is CommandId.RESUME_GAME:
            try:
                self._games.resume_game(
                    game_id,
                    resumed_at=message.created_at,
                    event_id=operation_event_id,
                    channel_id=message.channel_id,
                )
            except (ValueError, RuntimeError) as error:
                return tr(locale, "game_resume_failed", error=error)
            return HandlerResponse(tr(locale, "game_resumed"), render_live_status=True)
        if routed_command is CommandId.FINISH_GAME:
            try:
                self._games.finish_game(
                    game_id,
                    event_id=operation_event_id,
                    channel_id=message.channel_id,
                )
            except (ValueError, RuntimeError) as error:
                return tr(locale, "game_finish_failed", error=error)
            return HandlerResponse(tr(locale, "game_finished"), render_live_status=True)
        if routed_command in {CommandId.UNBIND_GAME, CommandId.NEW_SESSION}:
            try:
                self._games.unbind_channel(
                    channel_id=message.channel_id,
                    game_id=game_id,
                    event_id=operation_event_id,
                    operation_type=(
                        "new_session" if routed_command is CommandId.NEW_SESSION else "unbind_game"
                    ),
                )
            except (ValueError, RuntimeError) as error:
                return tr(locale, "game_unbind_failed", error=error)
            return HandlerResponse(
                tr(
                    locale,
                    "new_session_ready"
                    if routed_command is CommandId.NEW_SESSION
                    else "game_unbound_complete",
                ),
                render_live_status=True,
            )
        if routed_command is CommandId.REQUEST_ADVANCEMENT:
            valid_raise = len(args) == 4 and args[1].lower() == "raise"
            valid_learn = len(args) == 6 and args[1].lower() == "learn"
            if not (valid_raise or valid_learn):
                return tr(locale, "advance_usage")
            if self._advancement is None:
                return tr(locale, "advancement_unavailable")
            try:
                if valid_raise:
                    updated = await self._advancement.raise_trait(
                        game_id=game_id,
                        player_id=message.author_id,
                        trait_name=args[2],
                        new_aspect=args[3],
                        causation_id=f"advancement:{message.event_id}",
                        checkpoint_event_id=message.event_id,
                    )
                    return tr(
                        locale,
                        "trait_raised",
                        trait=args[2],
                        xp=updated.experience_available,
                    )
                if valid_learn:
                    updated = await self._advancement.learn_trait(
                        game_id=game_id,
                        player_id=message.author_id,
                        trait_name=args[2],
                        aspects=(args[3], args[4]),
                        justification=args[5],
                        causation_id=f"advancement:{message.event_id}",
                        checkpoint_event_id=message.event_id,
                    )
                    return tr(
                        locale,
                        "trait_learned",
                        trait=args[2],
                        xp=updated.experience_available,
                    )
            except ValueError as error:
                return tr(locale, "advancement_rejected", error=error)
            return tr(locale, "advance_usage")
        return tr(locale, "unknown_command")
