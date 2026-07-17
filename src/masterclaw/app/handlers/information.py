from __future__ import annotations

import logging
import re

from masterclaw.app.i18n import tr
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.models import (
    HandlerResponse,
    IncomingMessage,
    OutboundDelivery,
)
from masterclaw.domain.text_safety import redact_secret_leak
from masterclaw.pipelines.base import PipelineValidationError

logger = logging.getLogger(__name__)


class InformationHandlers:
    async def _handle_rules_question(self, *, message: IncomingMessage, game_id: str | None) -> str:
        locale = self._locale(game_id)
        if self._rules_question_pipeline is None:
            return tr(locale, "conversation_clarification")
        manifest = manifest_for(PipelineName.RULES_QUESTION)
        assembled = self._assemble_context(
            manifest,
            {
                "session_brief": {"locale": locale},
                "player_question": message.content,
            },
            game_id=game_id,
            channel_id=message.channel_id,
            player_id=message.author_id,
        )
        try:
            answer = await self._rules_question_pipeline.run(
                task="Answer the player's rules question.", context=assembled
            )
        except PipelineValidationError:
            logger.warning("rules_question_invalid event_id=%s", message.event_id, exc_info=True)
            return tr(locale, manifest.on_invalid.value)
        return redact_secret_leak(
            answer.reply,
            self._secret_plot_for_game(game_id) if game_id is not None else None,
            replacement=tr(locale, manifest.on_invalid.value),
        )

    async def _handle_scene_question(self, *, message: IncomingMessage, game_id: str) -> str:
        locale = self._locale(game_id)
        if self._scene_question_pipeline is None:
            return tr(locale, "conversation_clarification")
        scene = self._store.scene_projection(game_id=game_id, player_id=message.author_id)
        if scene is None:
            return tr(locale, "character_scene_required")
        manifest = manifest_for(PipelineName.SCENE_QUESTION)
        assembled = self._assemble_context(
            manifest,
            {
                "session_brief": {"locale": locale},
                "current_scene": scene,
                "player_question": message.content,
            },
            game_id=game_id,
            channel_id=message.channel_id,
            player_id=message.author_id,
        )
        try:
            answer = await self._scene_question_pipeline.run(
                task="Answer what this character can perceive in the current scene.",
                context=assembled,
            )
        except PipelineValidationError:
            logger.warning("scene_question_invalid event_id=%s", message.event_id, exc_info=True)
            return tr(locale, manifest.on_invalid.value)
        return redact_secret_leak(
            answer.reply,
            self._secret_plot_for_game(game_id),
            replacement=tr(locale, manifest.on_invalid.value),
        )

    async def _handle_free_roleplay(
        self, *, message: IncomingMessage, game_id: str
    ) -> str | HandlerResponse:
        locale = self._locale(game_id)
        if self._roleplay_reply_pipeline is None:
            return tr(locale, "conversation_clarification")
        game = self._store.game_state(game_id)
        scene = self._store.scene_projection(game_id=game_id, player_id=message.author_id)
        if game is None or scene is None:
            return tr(locale, "character_scene_required")
        manifest = manifest_for(PipelineName.ROLEPLAY_REPLY)
        assembled = self._assemble_context(
            manifest,
            {
                "session_brief": self._narrative_session_brief(game),
                "current_scene": scene,
                "player_narration": message.content,
            },
            game_id=game_id,
            channel_id=message.channel_id,
            player_id=message.author_id,
        )
        try:
            reply = await self._roleplay_reply_pipeline.run(
                task="Continue the scene after this player roleplay.", context=assembled
            )
        except PipelineValidationError:
            logger.warning("roleplay_reply_invalid event_id=%s", message.event_id, exc_info=True)
            return tr(locale, manifest.on_invalid.value)
        public_reply = redact_secret_leak(
            reply.reply,
            self._secret_plot_for_game(game_id),
            replacement=tr(locale, manifest.on_invalid.value),
        )
        self._store.record_interaction_event(
            game_id=game_id,
            scene_id=str(scene["scene_id"]),
            actor_role="gm_or_npc",
            kind="gm_roleplay_reply",
            text=public_reply,
            causation_id=f"roleplay:{message.event_id}",
            player_id=message.author_id,
            metadata={"source_event_id": message.event_id},
        )
        await self._consider_reserve_recovery(
            game_id=game_id,
            scene=scene,
            causation_id=f"roleplay:{message.event_id}",
            outcome_source={"kind": "free_roleplay", "text": message.content},
            player_id=message.author_id,
        )
        if game.narrative_channel_id is None:
            return public_reply
        return HandlerResponse(
            tr(locale, "roleplay_continued"),
            (OutboundDelivery(game.narrative_channel_id, public_reply, "roleplay_reply"),),
        )

    def _character_status(self, *, game_id: str, player_id: str) -> str:
        locale = self._locale(game_id)
        character = self._store.character_for_player(game_id=game_id, player_id=player_id)
        if character is None:
            return tr(locale, "character_missing")
        traits = ", ".join(
            f"{trait.name} {trait.level} ({'; '.join(trait.aspects)})"
            for trait in character.sheet.traits
        )
        conditions = ", ".join(item.text for item in character.conditions) or tr(locale, "none")
        items = ", ".join(item.name for item in character.plot_items) or tr(locale, "none")
        return tr(
            locale,
            "character_status",
            character_id=character.character_id,
            name=character.sheet.name,
            traits=traits,
            reserve_current=character.sheet.reserve_current,
            reserve_maximum=character.sheet.reserve_maximum,
            xp_available=character.experience_available,
            xp_earned=character.experience_earned,
            xp_spent=character.experience_spent,
            conditions=conditions,
            items=items,
        )

    def _game_status(self, game_id: str) -> str:
        locale = self._locale(game_id)
        game = self._store.game_state(game_id)
        if game is None:
            return tr(locale, "game_missing")
        return tr(
            locale,
            "game_status",
            game_id=game.game_id,
            lifecycle=game.lifecycle.value,
            world_id=game.world_id,
            locale=game.locale,
            progression=tr(locale, "enabled" if game.progression_enabled else "disabled"),
            rights=game.narrator_rights_level.value,
            reserve_recovery=game.reserve_recovery_mode.value,
        )

    def _xp_status(self, game_id: str) -> str:
        locale = self._locale(game_id)
        activity = self._store.activity_state(game_id)
        game = self._store.game_state(game_id)
        return tr(
            locale,
            "xp_status",
            progression=tr(locale, "enabled" if game and game.progression_enabled else "disabled"),
            minutes=int(activity["active_seconds"]) // 60,
            intervals=activity["awarded_intervals"],
        )

    def _scene_status(self, *, game_id: str, player_id: str) -> str:
        locale = self._locale(game_id)
        scene = self._store.scene_projection(game_id=game_id, player_id=player_id)
        if scene is None:
            return tr(locale, "character_scene_required")
        game = self._store.game_state(game_id)
        assert game is not None
        state = dict(scene["state"])
        description = str(state.get("description") or "").strip()
        if not description:
            content = self._store.world_content(game.world_id) or {}
            for location in content.get("locations", []):
                if isinstance(location, dict) and location.get("name") == scene["title"]:
                    description = str(location.get("description") or "").strip()
                    break
        facts = [str(item) for item in state.get("facts", [])]
        present = []
        participants = set(scene["participants"])
        for character in self._store.character_roster(game_id):
            if character["player_id"] not in participants:
                continue
            conditions = ", ".join(character["conditions"]) or tr(locale, "none")
            present.append(f"**{character['name']}**: {character['biography']} · {conditions}")
        return tr(
            locale,
            "scene_status",
            title=scene["title"],
            description=description or tr(locale, "scene_description_missing"),
            facts="; ".join(facts) or tr(locale, "none"),
            characters="\n".join(present) or tr(locale, "scene_empty"),
        )

    def _handle_natural_help(self, *, message: IncomingMessage, game_id: str) -> str:
        locale = self._locale(game_id)
        mentions = re.findall(r"<@!?(\d+)>", message.content)
        if not mentions:
            return tr(locale, "help_usage")
        target_player_id = mentions[0]
        try:
            pending = self._store.offer_help(
                game_id=game_id,
                helper_player_id=message.author_id,
                target_player_id=target_player_id,
            )
        except ValueError as error:
            return tr(locale, "help_failed", error=error)
        return tr(
            locale,
            "help_added",
            player_id=target_player_id,
            interaction_id=pending.interaction_id,
        )
