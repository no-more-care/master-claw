from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace

from masterclaw.app.decision_checkpoints import (
    DecisionContextChangedError,
    decision_input_fingerprint,
    run_checkpointed_decision,
)
from masterclaw.app.handlers.types import (
    FictionContextChangedError,
    FictionContextSnapshot,
)
from masterclaw.app.i18n import tr
from masterclaw.app.scenarios import normalize_phrase
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.models import (
    HandlerResponse,
    IncomingMessage,
    OutboundDelivery,
)
from masterclaw.domain.text_safety import redact_secret_leak
from masterclaw.pipelines.base import PipelineValidationError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PreparedRoleplay:
    """A model-complete roleplay part whose domain effects have not been committed yet."""

    message: IncomingMessage
    game_id: str
    scene: dict[str, object]
    context: FictionContextSnapshot
    public_reply: str
    response: str | HandlerResponse
    interaction_committed: bool = False


class InformationHandlers:
    async def _handle_rules_question(
        self,
        *,
        message: IncomingMessage,
        game_id: str | None,
        raise_on_invalid: bool = False,
    ) -> str:
        locale = self._locale(game_id)
        if self._rules_question_pipeline is None:
            if raise_on_invalid:
                raise PipelineValidationError("rules question pipeline is unavailable")
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
            answer = await run_checkpointed_decision(
                store=self._store,
                event_id=message.event_id,
                pipeline_key="rules_question",
                pipeline=self._rules_question_pipeline,
                task="Answer the player's rules question.",
                context=assembled,
                game_id=game_id,
            )
        except PipelineValidationError:
            logger.warning("rules_question_invalid event_id=%s", message.event_id, exc_info=True)
            if raise_on_invalid:
                raise
            return tr(locale, manifest.on_invalid.value)
        return redact_secret_leak(
            answer.reply,
            self._secret_plot_for_game(game_id) if game_id is not None else None,
            replacement=tr(locale, manifest.on_invalid.value),
        )

    async def _handle_scene_question(
        self,
        *,
        message: IncomingMessage,
        game_id: str,
        raise_on_invalid: bool = False,
    ) -> str:
        locale = self._locale(game_id)
        if self._scene_question_pipeline is None:
            if raise_on_invalid:
                raise PipelineValidationError("scene question pipeline is unavailable")
            return tr(locale, "conversation_clarification")
        scene = self._store.scene_projection(game_id=game_id, player_id=message.author_id)
        character = self._store.character_for_player(
            game_id=game_id,
            player_id=message.author_id,
        )
        if scene is None or character is None:
            return tr(locale, "character_scene_required")
        context_snapshot = FictionContextSnapshot.capture(
            game_id=game_id,
            player_id=message.author_id,
            character=character,
            scene=scene,
        )
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
            answer = await run_checkpointed_decision(
                store=self._store,
                event_id=message.event_id,
                pipeline_key="scene_question",
                pipeline=self._scene_question_pipeline,
                task="Answer what this character can perceive in the current scene.",
                context=assembled,
                game_id=game_id,
                input_fingerprint=decision_input_fingerprint(
                    {
                        "stage": "scene_question",
                        "question": message.content,
                        **context_snapshot.as_mapping(),
                    }
                ),
            )
        except DecisionContextChangedError as error:
            logger.warning(
                "scene_question_context_changed event_id=%s",
                message.event_id,
            )
            if raise_on_invalid:
                raise FictionContextChangedError(str(error)) from error
            return tr(locale, "fiction_context_changed_retry")
        except PipelineValidationError:
            logger.warning("scene_question_invalid event_id=%s", message.event_id, exc_info=True)
            if raise_on_invalid:
                raise
            return tr(locale, manifest.on_invalid.value)
        if not context_snapshot.matches(
            character=self._store.character_for_player(
                game_id=game_id,
                player_id=message.author_id,
            ),
            scene=self._store.scene_projection(
                game_id=game_id,
                player_id=message.author_id,
            ),
        ):
            if raise_on_invalid:
                raise FictionContextChangedError("scene question context changed before response")
            return tr(locale, "fiction_context_changed_retry")
        return redact_secret_leak(
            answer.reply,
            self._secret_plot_for_game(game_id),
            replacement=tr(locale, manifest.on_invalid.value),
        )

    async def _handle_free_roleplay(
        self, *, message: IncomingMessage, game_id: str
    ) -> str | HandlerResponse:
        prepared = await self._prepare_free_roleplay(message=message, game_id=game_id)
        if isinstance(prepared, str):
            return prepared
        try:
            return await self._commit_prepared_roleplay(prepared)
        except FictionContextChangedError:
            return tr(self._locale(game_id), "fiction_context_changed_retry")

    async def _prepare_free_roleplay(
        self,
        *,
        message: IncomingMessage,
        game_id: str,
        raise_on_invalid: bool = False,
    ) -> PreparedRoleplay | str:
        """Run every fallible roleplay model step without changing canonical state."""

        locale = self._locale(game_id)
        committed = self._store.domain_event_for_causation(
            event_type="interaction_recorded",
            causation_id=f"roleplay:{message.event_id}",
        )
        if committed is not None:
            if committed["game_id"] != game_id:
                raise RuntimeError("committed roleplay replay belongs to another game")
            payload = committed["payload"]
            if not isinstance(payload, Mapping) or payload.get("kind") != "gm_roleplay_reply":
                raise RuntimeError("committed roleplay replay payload is invalid")
            metadata = payload.get("metadata")
            if not isinstance(metadata, Mapping):
                raise RuntimeError("committed roleplay replay metadata is missing")
            raw_context = metadata.get("fiction_context")
            if not isinstance(raw_context, Mapping):
                raise RuntimeError("committed roleplay replay context is missing")
            context_snapshot = FictionContextSnapshot.from_mapping(raw_context)
            if (
                context_snapshot.game_id != game_id
                or context_snapshot.player_id != message.author_id
                or metadata.get("source_event_id") != message.event_id
            ):
                raise RuntimeError("committed roleplay replay identity mismatch")
            public_reply = str(payload.get("text") or "").strip()
            if not public_reply:
                raise RuntimeError("committed roleplay replay text is missing")
            response_mode = metadata.get("response_mode")
            original_target = metadata.get("original_target_channel_id")
            if response_mode == "delivery":
                if not isinstance(original_target, str) or not original_target:
                    raise RuntimeError("committed roleplay delivery target is missing")
                response: str | HandlerResponse = HandlerResponse(
                    tr(locale, "roleplay_continued"),
                    (OutboundDelivery(original_target, public_reply, "roleplay_reply"),),
                )
            elif response_mode == "inline":
                response = public_reply
            else:
                raise RuntimeError("committed roleplay response mode is invalid")
            current_scene = self._store.scene_projection(
                game_id=game_id,
                player_id=message.author_id,
            )
            scene = (
                current_scene
                if current_scene is not None
                else {
                    "scene_id": context_snapshot.scene_id,
                    "scene_revision": context_snapshot.scene_revision,
                    "location_revision": context_snapshot.location_revision,
                    "participants": list(context_snapshot.participants),
                    "state": {},
                }
            )
            return PreparedRoleplay(
                message=message,
                game_id=game_id,
                scene=scene,
                context=context_snapshot,
                public_reply=public_reply,
                response=response,
                interaction_committed=True,
            )
        if self._roleplay_reply_pipeline is None:
            if raise_on_invalid:
                raise PipelineValidationError("roleplay reply pipeline is unavailable")
            return tr(locale, "conversation_clarification")
        game = self._store.game_state(game_id)
        character = self._store.character_for_player(
            game_id=game_id,
            player_id=message.author_id,
        )
        scene = self._store.scene_projection(game_id=game_id, player_id=message.author_id)
        if game is None or character is None or scene is None:
            return tr(locale, "character_scene_required")
        context_snapshot = FictionContextSnapshot.capture(
            game_id=game_id,
            player_id=message.author_id,
            character=character,
            scene=scene,
        )
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
            reply = await run_checkpointed_decision(
                store=self._store,
                event_id=message.event_id,
                pipeline_key="roleplay_reply",
                pipeline=self._roleplay_reply_pipeline,
                task="Continue the scene after this player roleplay.",
                context=assembled,
                game_id=game_id,
                input_fingerprint=decision_input_fingerprint(
                    {
                        "stage": "roleplay_reply",
                        "message": message.content,
                        **context_snapshot.as_mapping(),
                    }
                ),
            )
        except DecisionContextChangedError as error:
            logger.warning(
                "roleplay_reply_context_changed event_id=%s",
                message.event_id,
            )
            if raise_on_invalid:
                raise FictionContextChangedError(str(error)) from error
            return tr(locale, "fiction_context_changed_retry")
        except PipelineValidationError:
            logger.warning("roleplay_reply_invalid event_id=%s", message.event_id, exc_info=True)
            if raise_on_invalid:
                raise
            return tr(locale, manifest.on_invalid.value)
        current_character = self._store.character_for_player(
            game_id=game_id,
            player_id=message.author_id,
        )
        current_scene = self._store.scene_projection(
            game_id=game_id,
            player_id=message.author_id,
        )
        if not context_snapshot.matches(
            character=current_character,
            scene=current_scene,
        ):
            if raise_on_invalid:
                raise FictionContextChangedError("roleplay reply context changed before commit")
            return tr(locale, "fiction_context_changed_retry")
        assert current_scene is not None
        scene = current_scene
        public_reply = redact_secret_leak(
            reply.reply,
            self._secret_plot_for_game(game_id),
            replacement=tr(locale, manifest.on_invalid.value),
        )
        response: str | HandlerResponse
        if game.narrative_channel_id is None:
            response = public_reply
        else:
            response = HandlerResponse(
                tr(locale, "roleplay_continued"),
                (OutboundDelivery(game.narrative_channel_id, public_reply, "roleplay_reply"),),
            )
        return PreparedRoleplay(
            message=message,
            game_id=game_id,
            scene=scene,
            context=context_snapshot,
            public_reply=public_reply,
            response=response,
        )

    async def _commit_prepared_roleplay(
        self,
        prepared: PreparedRoleplay,
        *,
        defer_activity_and_recovery: bool = False,
    ) -> str | HandlerResponse:
        """Commit a preflighted roleplay part without calling its reply model again."""

        message = prepared.message
        if not prepared.interaction_committed:
            current_character = self._store.character_for_player(
                game_id=prepared.game_id,
                player_id=message.author_id,
            )
            current_scene = self._store.scene_projection(
                game_id=prepared.game_id,
                player_id=message.author_id,
            )
            if not prepared.context.matches(
                character=current_character,
                scene=current_scene,
            ):
                raise FictionContextChangedError("prepared roleplay context changed before commit")
            target_channel_id = (
                prepared.response.deliveries[0].channel_id
                if isinstance(prepared.response, HandlerResponse)
                else None
            )
            self._store.record_interaction_event(
                game_id=prepared.game_id,
                scene_id=prepared.context.scene_id,
                actor_role="gm_or_npc",
                kind="gm_roleplay_reply",
                text=prepared.public_reply,
                causation_id=f"roleplay:{message.event_id}",
                player_id=message.author_id,
                metadata={
                    "source_event_id": message.event_id,
                    "fiction_context": prepared.context.as_mapping(),
                    "response_mode": (
                        "delivery" if isinstance(prepared.response, HandlerResponse) else "inline"
                    ),
                    "original_target_channel_id": target_channel_id,
                },
            )
        if not defer_activity_and_recovery:
            await self._commit_prepared_roleplay_side_effects(prepared)
        return prepared.response

    async def _commit_prepared_roleplay_side_effects(
        self,
        prepared: PreparedRoleplay,
        *,
        attributable_context: FictionContextSnapshot | None = None,
    ) -> None:
        message = prepared.message
        expected_context = prepared.context
        activity = self._store.activity_record_for_causation(
            causation_id=f"activity:{message.event_id}",
            game_id=prepared.game_id,
            occurred_at=message.created_at,
        )
        if activity is not None and prepared.context.character_id in activity[1]:
            expected_context = replace(
                expected_context,
                character_revision=expected_context.character_revision + 1,
            )
        context_is_current = expected_context.matches(
            character=self._store.character_for_player(
                game_id=prepared.game_id,
                player_id=message.author_id,
            ),
            scene=self._store.scene_projection(
                game_id=prepared.game_id,
                player_id=message.author_id,
            ),
        )
        if not context_is_current and attributable_context is not None:
            context_is_current = attributable_context.matches(
                character=self._store.character_for_player(
                    game_id=prepared.game_id,
                    player_id=message.author_id,
                ),
                scene=self._store.scene_projection(
                    game_id=prepared.game_id,
                    player_id=message.author_id,
                ),
            )
        self._store.record_activity(
            game_id=prepared.game_id,
            occurred_at=message.created_at,
            causation_id=f"activity:{message.event_id}",
        )
        causation_id = f"roleplay:{message.event_id}"
        if not context_is_current and self._store.reserve_recovery_decision(causation_id) is None:
            logger.warning(
                "roleplay_recovery_skipped_stale_context event_id=%s",
                message.event_id,
            )
            return
        await self._consider_reserve_recovery(
            game_id=prepared.game_id,
            scene=prepared.scene,
            causation_id=causation_id,
            outcome_source={"kind": "free_roleplay", "text": message.content},
            player_id=message.author_id,
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

    def _xp_status(self, game_id: str, *, player_id: str | None = None) -> str:
        locale = self._locale(game_id)
        activity = self._store.activity_state(game_id)
        game = self._store.game_state(game_id)
        character = (
            self._store.character_for_player(game_id=game_id, player_id=player_id)
            if player_id is not None
            else None
        )
        return tr(
            locale,
            "xp_status",
            progression=tr(locale, "enabled" if game and game.progression_enabled else "disabled"),
            minutes=int(activity["active_seconds"]) // 60,
            intervals=activity["awarded_intervals"],
            available=character.experience_available if character is not None else 0,
            earned=character.experience_earned if character is not None else 0,
            spent=character.experience_spent if character is not None else 0,
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
        target_player_id = self._help_target_player_id(
            game_id=game_id, selector=message.content, embedded=True
        )
        if target_player_id is None:
            return tr(locale, "help_usage")
        operation_event_id = (
            message.event_id if self._has_durable_inbox_event(message.event_id) else None
        )
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

    def _help_target_player_id(
        self, *, game_id: str, selector: str, embedded: bool = False
    ) -> str | None:
        mentions = re.findall(r"<@!?(\d+)>", selector)
        if mentions:
            return mentions[0]
        normalized = normalize_phrase(selector)
        matches = []
        for item in self._store.character_roster(game_id):
            player_id = str(item["player_id"])
            name = normalize_phrase(str(item["name"]))
            if normalized == normalize_phrase(player_id) or normalized == name:
                matches.append(player_id)
            elif embedded and name and f" {name} " in f" {normalized} ":
                matches.append(player_id)
        unique = list(dict.fromkeys(matches))
        return unique[0] if len(unique) == 1 else None
