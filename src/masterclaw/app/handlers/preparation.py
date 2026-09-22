from __future__ import annotations

import logging

from masterclaw.app.decision_checkpoints import run_checkpointed_decision
from masterclaw.app.i18n import tr
from masterclaw.app.scenarios import normalize_phrase
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.discord_ids import parse_discord_channel_id
from masterclaw.domain.mechanics import (
    STARTING_CHARACTER_RULES,
    CharacterSheet,
    Flag,
    FlagType,
    Trait,
    validate_character,
)
from masterclaw.domain.models import (
    HandlerResponse,
    IncomingMessage,
)
from masterclaw.pipelines.base import PipelineValidationError
from masterclaw.pipelines.conversation_actions import (
    GameConfigurationKind,
)

logger = logging.getLogger(__name__)


_JOINABLE_LIFECYCLES = {"preparing", "active", "paused"}


class PreparationHandlers:
    def _character_name_taken(self, *, game_id: str, name: str) -> bool:
        normalized = name.strip().casefold()
        return any(
            str(item["name"]).strip().casefold() == normalized
            for item in self._store.character_roster(game_id)
        )

    def _place_joining_character(self, *, game_id: str, player_id: str) -> bool:
        """Place a character created after game start into the party's scene."""
        roster = self._store.character_roster(game_id)
        scene_id = next(
            (
                str(item["scene_id"])
                for item in roster
                if item["scene_id"] and item["player_id"] != player_id
            ),
            None,
        ) or self._store.first_scene_id(game_id)
        if scene_id is None:
            return False
        self._store.place_player(game_id=game_id, player_id=player_id, scene_id=scene_id)
        return True

    async def _handle_natural_character_creation(
        self, *, message: IncomingMessage, game_id: str
    ) -> str:
        locale = self._locale(game_id)
        if self._character_pipeline is None:
            return tr(locale, "character_pipeline_unavailable")
        game = self._store.game_state(game_id)
        if game is None or game.lifecycle.value not in _JOINABLE_LIFECYCLES:
            return tr(locale, "character_prepare_only")
        existing = self._store.character_for_player(game_id=game_id, player_id=message.author_id)
        if existing is not None:
            if (
                game.lifecycle.value != "preparing"
                and self._store.scene_projection(game_id=game_id, player_id=message.author_id)
                is None
                and self._place_joining_character(game_id=game_id, player_id=message.author_id)
            ):
                return tr(
                    locale,
                    "character_created_joined",
                    character_id=existing.character_id,
                    name=existing.sheet.name,
                )
            return tr(
                locale,
                "character_created_and_placed",
                character_id=existing.character_id,
                name=existing.sheet.name,
            )
        world = self._store.world_content(game.world_id) or {}
        public_world = {key: value for key, value in world.items() if key != "secret_plot"}
        manifest = manifest_for(PipelineName.CHARACTER_CREATION)
        assembled = self._assemble_context(
            manifest,
            {"public_world": public_world, "player_brief": message.content},
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
                task="Create a validated starting character from the player's natural description.",
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
        validate_character(sheet, STARTING_CHARACTER_RULES)
        if self._character_name_taken(game_id=game_id, name=sheet.name):
            return tr(locale, "character_name_taken", name=sheet.name)
        character_id = f"character_{message.event_id}"
        self._store.create_character(
            CharacterState(
                character_id,
                game_id,
                message.author_id,
                draft.biography,
                sheet,
            )
        )
        if game.lifecycle.value != "preparing" and self._place_joining_character(
            game_id=game_id, player_id=message.author_id
        ):
            return tr(
                locale,
                "character_created_joined",
                character_id=character_id,
                name=sheet.name,
            )
        return tr(
            locale,
            "character_created_and_placed",
            character_id=character_id,
            name=sheet.name,
        )

    def _match_pregenerated_character(
        self, *, game_id: str, content: str
    ) -> dict[str, object] | None:
        game = self._store.game_state(game_id)
        if game is None:
            return None
        templates = (self._store.world_content(game.world_id) or {}).get("character_templates", [])
        normalized = normalize_phrase(content)
        prefixes = (
            "выбираю ",
            "выбрать ",
            "беру ",
            "хочу играть за ",
            "choose ",
            "play as ",
        )
        requested = next(
            (normalized[len(prefix) :] for prefix in prefixes if normalized.startswith(prefix)),
            None,
        )
        if requested is None:
            exact_names = [
                item
                for item in templates
                if isinstance(item, dict)
                and normalize_phrase(str(item.get("name", ""))) == normalized
            ]
            return exact_names[0] if len(exact_names) == 1 else None
        if not requested:
            return None
        matches = [
            item
            for item in templates
            if isinstance(item, dict) and normalize_phrase(str(item.get("name", ""))) == requested
        ]
        return matches[0] if len(matches) == 1 else None

    def _select_pregenerated_character(
        self, *, message: IncomingMessage, game_id: str, pregen: dict[str, object]
    ) -> str:
        locale = self._locale(game_id)
        existing = self._store.character_for_player(game_id=game_id, player_id=message.author_id)
        if existing is not None:
            game = self._store.game_state(game_id)
            if (
                game is not None
                and game.lifecycle.value != "preparing"
                and self._store.scene_projection(game_id=game_id, player_id=message.author_id)
                is None
                and self._place_joining_character(game_id=game_id, player_id=message.author_id)
            ):
                return tr(
                    locale,
                    "character_created_joined",
                    character_id=existing.character_id,
                    name=existing.sheet.name,
                )
            return tr(
                locale,
                "character_created_and_placed",
                character_id=existing.character_id,
                name=existing.sheet.name,
            )
        pregen_name = str(pregen["name"])
        if self._character_name_taken(game_id=game_id, name=pregen_name):
            return tr(locale, "pregen_already_claimed", name=pregen_name)
        traits = tuple(
            Trait(str(item["name"]), int(item["level"]), tuple(item["aspects"]))
            for item in pregen.get("traits", [])
        )
        flags = tuple(
            Flag(str(item["text"]), FlagType(str(item["type"])), locked=True)
            for item in pregen.get("flags", [])
        )
        sheet = CharacterSheet(name=pregen_name, traits=traits, flags=flags)
        validate_character(sheet, STARTING_CHARACTER_RULES)
        character_id = f"character_{message.event_id}"
        self._store.create_character(
            CharacterState(
                character_id,
                game_id,
                message.author_id,
                str(pregen["biography"]),
                sheet,
            )
        )
        game = self._store.game_state(game_id)
        if (
            game is not None
            and game.lifecycle.value != "preparing"
            and self._place_joining_character(game_id=game_id, player_id=message.author_id)
        ):
            return tr(
                locale,
                "character_created_joined",
                character_id=character_id,
                name=sheet.name,
            )
        return tr(
            locale,
            "character_created_and_placed",
            character_id=character_id,
            name=sheet.name,
        )

    def _handle_natural_game_start(
        self, *, message: IncomingMessage, game_id: str
    ) -> str | HandlerResponse:
        locale = self._locale(game_id)
        operation_event_id = (
            message.event_id if self._has_durable_inbox_event(message.event_id) else None
        )
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

    async def _handle_natural_game_configuration(
        self, *, message: IncomingMessage, game_id: str
    ) -> str:
        locale = self._locale(game_id)
        if self._game_configuration_pipeline is None:
            return tr(locale, "conversation_clarification")
        game = self._store.game_state(game_id)
        if game is None:
            return tr(locale, "game_missing")
        manifest = manifest_for(PipelineName.GAME_CONFIGURATION)
        assembled = self._assemble_context(
            manifest,
            {
                "game": {
                    "lifecycle": game.lifecycle.value,
                    "progression_enabled": game.progression_enabled,
                    "narrator_rights": game.narrator_rights_level.value,
                    "narrative_channel_id": game.narrative_channel_id,
                },
                "player_request": message.content,
            },
            game_id=game_id,
            channel_id=message.channel_id,
            player_id=message.author_id,
        )
        try:
            request = await run_checkpointed_decision(
                store=self._store,
                event_id=message.event_id,
                pipeline_key="game_configuration",
                pipeline=self._game_configuration_pipeline,
                task="Extract the requested game setting.",
                context=assembled,
                game_id=game_id,
            )
        except PipelineValidationError:
            logger.warning(
                "game_configuration_invalid event_id=%s", message.event_id, exc_info=True
            )
            return tr(locale, manifest.on_invalid.value)
        try:
            operation_event_id = (
                message.event_id if self._has_durable_inbox_event(message.event_id) else None
            )
            if request.kind is GameConfigurationKind.PROGRESSION:
                if request.enabled is None:
                    raise ValueError("progression setting requires on or off")
                updated = self._games.configure_progression(
                    game_id=game_id,
                    enabled=request.enabled,
                    event_id=operation_event_id,
                    channel_id=message.channel_id,
                )
                return tr(
                    locale,
                    "progression_changed",
                    progression=tr(
                        locale, "enabled" if updated.progression_enabled else "disabled"
                    ),
                )
            if request.kind is GameConfigurationKind.NARRATOR_RIGHTS:
                if request.narrator_rights is None:
                    raise ValueError("narrator-rights level is required")
                updated = self._games.configure_narrator_rights(
                    game_id=game_id,
                    level=request.narrator_rights,
                    event_id=operation_event_id,
                    channel_id=message.channel_id,
                )
                return tr(
                    locale,
                    "rights_changed",
                    rights=updated.narrator_rights_level.value,
                )
            if request.kind is GameConfigurationKind.NARRATIVE_CHANNEL:
                if request.channel_id is None:
                    raise ValueError("narrative channel id is required")
                channel_id = parse_discord_channel_id(request.channel_id)
                self._games.configure_narrative_channel(
                    game_id=game_id,
                    narrative_channel_id=channel_id,
                    event_id=operation_event_id,
                    channel_id=message.channel_id,
                )
                return tr(locale, "narrative_channel_changed", channel_id=channel_id)
            if request.kind is GameConfigurationKind.RESERVE_RECOVERY:
                if request.reserve_recovery_mode is None:
                    raise ValueError("reserve recovery mode is required")
                updated = self._games.configure_reserve_recovery(
                    game_id=game_id,
                    mode=request.reserve_recovery_mode,
                    event_id=operation_event_id,
                    channel_id=message.channel_id,
                )
                return tr(
                    locale,
                    "reserve_recovery_changed",
                    mode=updated.reserve_recovery_mode.value,
                )
            if not request.scene_title:
                raise ValueError("scene title is required")
            scene_id = f"scene_{message.event_id}"
            self._store.create_scene_if_absent(
                scene_id=scene_id,
                game_id=game_id,
                title=request.scene_title,
            )
            return tr(locale, "scene_created", scene_id=scene_id)
        except (ValueError, RuntimeError) as error:
            return tr(locale, "setting_failed", error=error)
