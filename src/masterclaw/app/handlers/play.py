from __future__ import annotations

import logging
from dataclasses import replace

from masterclaw.app.i18n import tr
from masterclaw.app.response_format import format_pool_confirmation, format_roll_result
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.mechanics import (
    MechanicsError,
    OutcomeAuthority,
    PoolProposal,
)
from masterclaw.domain.models import (
    HandlerResponse,
    IncomingMessage,
    OutboundDelivery,
)
from masterclaw.domain.outcomes import OutcomePolicyError, enforce_narrator_rights_policy
from masterclaw.domain.state import PendingKind
from masterclaw.domain.text_safety import (
    SecretLeakError,
    ensure_no_secret_fragments,
    redact_secret_leak,
)
from masterclaw.pipelines.action import ActionResolution
from masterclaw.pipelines.base import PipelineValidationError
from masterclaw.pipelines.conversation_actions import (
    AdvancementKind,
    RollConfirmationKind,
)

logger = logging.getLogger(__name__)


class PlayHandlers:
    def _cancel_pending_interaction(self, *, message: IncomingMessage, pending) -> str:
        locale = self._locale(pending.game_id)
        self._store.cancel_pending(
            interaction_id=pending.interaction_id,
            player_id=message.author_id,
            expected_revision=pending.revision,
        )
        if pending.kind is PendingKind.POOL_CONFIRMATION:
            return tr(locale, "roll_cancelled")
        if pending.kind is PendingKind.PLAYER_NARRATION:
            return tr(locale, "narration_cancelled")
        return tr(locale, "pending_cancelled")

    async def _handle_natural_advancement(self, *, message: IncomingMessage, game_id: str) -> str:
        locale = self._locale(game_id)
        if self._advancement is None or self._advancement_intake_pipeline is None:
            return tr(locale, "advancement_unavailable")
        character = self._store.character_for_player(game_id=game_id, player_id=message.author_id)
        if character is None:
            return tr(locale, "character_missing")
        manifest = manifest_for(PipelineName.ADVANCEMENT_INTAKE)
        assembled = self._assemble_context(
            manifest,
            {
                "actor_character": {
                    "name": character.sheet.name,
                    "available_xp": character.experience_available,
                    "traits": [
                        {
                            "name": trait.name,
                            "level": trait.level,
                            "aspects": list(trait.aspects),
                        }
                        for trait in character.sheet.traits
                    ],
                },
                "player_request": message.content,
            },
            game_id=game_id,
            channel_id=message.channel_id,
            player_id=message.author_id,
        )
        try:
            request = await self._advancement_intake_pipeline.run(
                task="Extract the requested character advancement.", context=assembled
            )
        except PipelineValidationError:
            logger.warning(
                "advancement_intake_invalid event_id=%s", message.event_id, exc_info=True
            )
            return tr(locale, manifest.on_invalid.value)
        try:
            if request.kind is AdvancementKind.RAISE:
                updated = await self._advancement.raise_trait(
                    game_id=game_id,
                    player_id=message.author_id,
                    trait_name=request.trait_name,
                    new_aspect=request.aspects[0],
                )
                return tr(
                    locale,
                    "trait_raised",
                    trait=request.trait_name,
                    xp=updated.experience_available,
                )
            updated = await self._advancement.learn_trait(
                game_id=game_id,
                player_id=message.author_id,
                trait_name=request.trait_name,
                aspects=(request.aspects[0], request.aspects[1]),
                justification=request.justification or "",
            )
            return tr(
                locale,
                "trait_learned",
                trait=request.trait_name,
                xp=updated.experience_available,
            )
        except ValueError as error:
            return tr(locale, "advancement_rejected", error=error)

    async def _handle_player_narration(
        self, *, message: IncomingMessage, pending
    ) -> str | HandlerResponse:
        locale = self._locale(pending.game_id)
        if self._player_narration_pipeline is None:
            return tr(locale, "rights_review_unavailable")
        game = self._store.game_state(pending.game_id)
        scene = self._store.scene_projection(game_id=pending.game_id, player_id=message.author_id)
        roll = self._store.roll_by_id(str(pending.payload.get("roll_id", "")))
        if game is None or scene is None or roll is None:
            return tr(locale, "rights_context_missing")
        manifest = manifest_for(PipelineName.PLAYER_NARRATION_REVIEW)
        assembled = self._assemble_context(
            manifest,
            {
                "session_brief": self._narrative_session_brief(game),
                "current_scene": scene,
                "roll_result": {
                    "hits": roll.hits,
                    "difficulty": roll.difficulty,
                    "narrator_rights": roll.narrator_rights.value,
                    "narrator_rights_level": game.narrator_rights_level.value,
                },
                "submitted_narration": message.content,
            },
            game_id=pending.game_id,
            channel_id=message.channel_id,
            player_id=message.author_id,
        )
        try:
            review = await self._player_narration_pipeline.run(
                task="Review the submitted player narration.",
                context=assembled,
            )
        except PipelineValidationError:
            logger.warning("player_narration_invalid event_id=%s", message.event_id, exc_info=True)
            return tr(locale, manifest.on_invalid.value)
        if not review.accepted:
            return redact_secret_leak(
                review.scale_back_request or tr(locale, "narration_rejected", error=review.reason),
                self._secret_plot_for_game(pending.game_id),
                replacement=tr(locale, manifest.on_invalid.value),
            )
        approved_narration = review.approved_narration
        if approved_narration is None:
            return tr(locale, manifest.on_invalid.value)
        try:
            ensure_no_secret_fragments(
                approved_narration,
                self._secret_plot_for_game(pending.game_id),
            )
        except SecretLeakError:
            return tr(locale, manifest.on_invalid.value)
        if self._consequence_pipeline is not None:
            await self._ensure_scene_consequence(
                game_id=pending.game_id,
                scene=scene,
                causation_id=f"player-narration:{pending.interaction_id}",
                outcome_source={
                    "kind": "player_narration",
                    "text": approved_narration,
                    "roll_id": roll.roll_id,
                },
                narrator_rights=roll.narrator_rights.value,
                player_id=message.author_id,
            )
        self._store.record_interaction_event(
            game_id=pending.game_id,
            scene_id=str(scene["scene_id"]),
            actor_role="player_character",
            kind="accepted_player_narration",
            text=approved_narration,
            causation_id=f"player-narration:{pending.interaction_id}",
            player_id=message.author_id,
            metadata={"roll_id": roll.roll_id, "source_event_id": message.event_id},
        )
        self._store.resolve_pending(
            interaction_id=pending.interaction_id,
            player_id=message.author_id,
            expected_revision=pending.revision,
        )
        if game.narrative_channel_id is None:
            return tr(locale, "narration_accepted")
        return HandlerResponse(
            tr(locale, "narration_accepted"),
            (
                OutboundDelivery(
                    game.narrative_channel_id,
                    approved_narration,
                    "player_narration",
                ),
            ),
        )

    async def _handle_action_declaration(self, *, message: IncomingMessage, game_id: str) -> str:
        locale = self._locale(game_id)
        if self._action_pipeline is None:
            return tr(locale, "action_pipeline_unavailable")
        character = self._store.character_for_player(game_id=game_id, player_id=message.author_id)
        scene = self._store.scene_projection(game_id=game_id, player_id=message.author_id)
        if character is None:
            return tr(locale, "character_game_required")
        if scene is None:
            return tr(locale, "character_scene_required")
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
            "temporary_bonuses": [
                {
                    "bonus_id": bonus.bonus_id,
                    "type": bonus.type.value,
                    "trigger": bonus.trigger,
                }
                for bonus in sheet.temporary_bonuses
            ],
        }
        manifest = manifest_for(PipelineName.ACTION_INTERPRETATION)
        assembled = self._assemble_context(
            manifest,
            {
                "session_brief": {
                    "game_id": game_id,
                    "locale": locale,
                    "participants_here": scene["participants"],
                },
                "actor_character": actor_projection,
                "current_scene": scene,
            },
            game_id=game_id,
            channel_id=message.channel_id,
            player_id=message.author_id,
        )
        try:
            result = await self._action_pipeline.run(
                task=f"Interpret the declaration:\n{message.content}",
                context=assembled,
            )
        except PipelineValidationError:
            logger.warning(
                "action_interpretation_invalid event_id=%s", message.event_id, exc_info=True
            )
            return tr(locale, manifest.on_invalid.value)
        if result.resolution is ActionResolution.CLARIFICATION:
            return redact_secret_leak(
                result.clarification_question or tr(locale, "clarify_action"),
                self._secret_plot_for_game(game_id),
                replacement=tr(locale, "clarify_action"),
            )
        if result.resolution is ActionResolution.AUTOMATIC:
            if self._consequence_pipeline is None:
                return tr(locale, "consequence_unavailable")
            try:
                await self._ensure_scene_consequence(
                    game_id=game_id,
                    scene=scene,
                    causation_id=f"automatic:{message.event_id}",
                    outcome_source={
                        "kind": "automatic_action",
                        "declaration": message.content,
                        "evidence": result.evidence,
                    },
                    narrator_rights=OutcomeAuthority.GM_AUTOMATIC.value,
                    player_id=message.author_id,
                )
            except Exception:
                logger.exception(
                    "Automatic consequence failed before a canonical patch for %s",
                    message.event_id,
                )
                return tr(
                    locale,
                    manifest_for(PipelineName.CONSEQUENCE_PLANNING).on_invalid.value,
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
                return tr(locale, "automatic_updated")
            assembled_narrative = self._assemble_context(
                manifest_for(PipelineName.OUTCOME_NARRATION),
                {
                    "session_brief": self._narrative_session_brief(game),
                    "current_scene": updated_scene,
                    "roll_result": {
                        "resolution": "automatic",
                        "declaration": message.content,
                    },
                },
                game_id=game_id,
                channel_id=message.channel_id,
                player_id=message.author_id,
            )
            try:
                narrative = await self._narrative_pipeline.run(
                    task="Narrate the automatic action outcome.",
                    context=assembled_narrative,
                )
                narrative_text = narrative.narrative
            except Exception:
                logger.exception(
                    "Narrative pipeline failed after automatic patch %s; using safe fallback",
                    message.event_id,
                )
                narrative_text = tr(locale, "narrative_fallback")
            narrative_text = redact_secret_leak(
                narrative_text,
                self._secret_plot_for_game(game_id),
                replacement=tr(locale, "narrative_fallback"),
            )
            return HandlerResponse(
                tr(locale, "automatic_done"),
                (OutboundDelivery(game.narrative_channel_id, narrative_text, "narrative"),),
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
                    bonus_ids=tuple(result.bonus_ids),
                ),
                prompt=tr(locale, "confirm_pool_prompt"),
                declaration=message.content,
            )
        except MechanicsError as error:
            return tr(locale, "pool_invalid", error=error)
        payload = pending.payload
        return format_pool_confirmation(
            pool_size=int(payload["pool_size"]),
            difficulty=int(payload["difficulty"]),
            reserve=sheet.reserve_current,
            locale=locale,
            sources=tuple(
                str(value)
                for value in (
                    *payload["trait_names"],
                    *payload["aspect_names"],
                    *([payload["flag"]] if payload["flag"] else []),
                )
            ),
        )

    async def _handle_pending_response(
        self, *, message: IncomingMessage, pending
    ) -> str | HandlerResponse:
        locale = self._locale(pending.game_id)
        normalized = message.content.strip().lower()
        if normalized in {"отмена", "cancel"}:
            return self._cancel_pending_interaction(message=message, pending=pending)
        deferred_action = pending.payload.get("compound_action")
        if pending.kind is PendingKind.CHOICE and deferred_action:
            if normalized in {"да", "yes", "confirm"}:
                self._store.resolve_pending(
                    interaction_id=pending.interaction_id,
                    player_id=message.author_id,
                    expected_revision=pending.revision,
                    answer=message.content,
                )
                return await self._handle_action_declaration(
                    message=replace(message, content=str(deferred_action)),
                    game_id=pending.game_id,
                )
            if normalized in {"нет", "no"}:
                return self._cancel_pending_interaction(message=message, pending=pending)
            return tr(locale, "compound_condition_reply")
        if pending.kind in {PendingKind.CHOICE, PendingKind.CLARIFICATION}:
            # A free-text answer (including "нет") is the answer itself, not a cancel.
            self._store.resolve_pending(
                interaction_id=pending.interaction_id,
                player_id=message.author_id,
                expected_revision=pending.revision,
                answer=message.content,
            )
            return tr(locale, "pending_answer_recorded")
        if normalized in {"нет", "no"}:
            return self._cancel_pending_interaction(message=message, pending=pending)
        try:
            reserve_spent = 0 if normalized in {"да", "yes", "confirm"} else int(normalized)
        except ValueError:
            if self._roll_confirmation_pipeline is None:
                return tr(locale, "reserve_usage")
            manifest = manifest_for(PipelineName.ROLL_CONFIRMATION)
            assembled = self._assemble_context(
                manifest,
                {
                    "pending_roll": pending.payload,
                    "player_response": message.content,
                },
                game_id=pending.game_id,
                channel_id=message.channel_id,
                player_id=message.author_id,
            )
            try:
                request = await self._roll_confirmation_pipeline.run(
                    task="Extract this natural-language roll confirmation.",
                    context=assembled,
                )
            except PipelineValidationError:
                logger.warning(
                    "roll_confirmation_invalid event_id=%s", message.event_id, exc_info=True
                )
                return tr(locale, manifest.on_invalid.value)
            if request.kind is RollConfirmationKind.CANCEL:
                return self._cancel_pending_interaction(message=message, pending=pending)
            reserve_spent = request.reserve_spent
        try:
            roll = self._actions.confirm_roll(
                interaction_id=pending.interaction_id,
                player_id=message.author_id,
                reserve_spent=reserve_spent,
                confirmation_event_id=message.event_id,
                die=self._die,
            )
        except (MechanicsError, ValueError) as error:
            return tr(locale, "confirmation_invalid", error=error)
        return await self._render_roll_outcome(message=message, roll=roll)

    async def _render_roll_outcome(
        self, *, message: IncomingMessage, roll
    ) -> str | HandlerResponse:
        game = self._store.game_state(roll.game_id)
        locale = game.locale if game is not None else "ru"
        mechanical = format_roll_result(
            dice=roll.dice,
            hits=roll.hits,
            difficulty=roll.difficulty,
            rights=roll.narrator_rights.value,
            reserve=roll.reserve_after,
            locale=locale,
        )
        if roll.narrator_rights.value.startswith("player_"):
            return mechanical + " " + tr(locale, "player_narrate")
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
            try:
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
                    player_id=message.author_id,
                )
            except Exception:
                logger.exception(
                    "Consequence pipeline failed for committed roll %s; "
                    "returning mechanics without a scene patch",
                    roll.roll_id,
                )
                return mechanical
            scene = self._store.scene_projection(game_id=roll.game_id, player_id=message.author_id)
        assembled = self._assemble_context(
            manifest_for(PipelineName.OUTCOME_NARRATION),
            {
                "session_brief": {
                    **self._narrative_session_brief(game),
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
            game_id=roll.game_id,
            channel_id=message.channel_id,
            player_id=message.author_id,
        )
        try:
            narrative = await self._narrative_pipeline.run(
                task="Narrate the resolved action outcome without changing its mechanics.",
                context=assembled,
            )
            narrative_text = narrative.narrative
        except Exception:
            logger.exception(
                "Narrative pipeline failed for roll %s; using safe fallback",
                roll.roll_id,
            )
            narrative_text = tr(locale, "narrative_fallback")
        narrative_text = redact_secret_leak(
            narrative_text,
            self._secret_plot_for_game(roll.game_id),
            replacement=tr(locale, "narrative_fallback"),
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
        player_id: str | None = None,
    ) -> None:
        if not self._store.has_scene_patch(causation_id):
            if self._consequence_pipeline is None:
                raise RuntimeError("consequence pipeline is not configured")
            if player_id is None:
                raise RuntimeError("outcome patch requires an acting player")
            game = self._store.game_state(game_id)
            character = self._store.character_for_player(
                game_id=game_id,
                player_id=player_id,
            )
            if game is None or character is None:
                raise RuntimeError("outcome patch actor context is missing")
            authority = OutcomeAuthority(narrator_rights)
            actor_projection = {
                "character_id": character.character_id,
                "revision": character.revision,
                "name": character.sheet.name,
                "conditions": [
                    {"text": item.text, "source": item.source} for item in character.conditions
                ],
                "plot_items": [
                    {"name": item.name, "description": item.description}
                    for item in character.plot_items
                ],
                "temporary_bonuses": [
                    {
                        "bonus_id": bonus.bonus_id,
                        "type": bonus.type.value,
                        "trigger": bonus.trigger,
                    }
                    for bonus in character.sheet.temporary_bonuses
                ],
            }
            assembled = self._assemble_context(
                manifest_for(PipelineName.CONSEQUENCE_PLANNING),
                {
                    "current_scene": scene,
                    "actor_character": actor_projection,
                    "allowed_scenes": self._store.scene_catalog(game_id),
                    "outcome_source": outcome_source,
                    "narrator_rights": narrator_rights,
                    "narrator_rights_policy": {
                        "level": game.narrator_rights_level.value,
                        "authority": authority.value,
                        "actor_only": True,
                        "minor_fact_changes": 1,
                        "minor_rewards": 1,
                        "significant_fact_changes": 2,
                        "significant_npc_changes": 1,
                        "significant_plot_item_changes": 1,
                        "significant_thread_changes": 1,
                        "significant_can_move_actor": False,
                        "significant_can_remove_npc": False,
                        "failure_can_grant_positive_bonus": False,
                    },
                },
                game_id=game_id,
                player_id=player_id,
            )
            revision_error: ValueError | None = None
            for attempt in range(2):
                task = "Produce the minimal persistent OutcomePatch for this outcome."
                if revision_error is not None:
                    task += (
                        "\nThe previous proposal was rejected by deterministic validation: "
                        f"{revision_error}. Revise it using only the exact supplied targets."
                    )
                plan = await self._consequence_pipeline.run(task=task, context=assembled)
                patch = plan.to_domain()
                try:
                    ensure_no_secret_fragments(
                        patch,
                        self._secret_plot_for_game(game_id),
                    )
                    enforce_narrator_rights_policy(
                        patch,
                        authority=authority,
                        level=game.narrator_rights_level,
                    )
                    self._store.apply_outcome_patch(
                        game_id=game_id,
                        scene_id=str(scene["scene_id"]),
                        expected_scene_revision=int(scene["scene_revision"]),
                        actor_character_id=character.character_id,
                        expected_actor_revision=character.revision,
                        causation_id=causation_id,
                        patch=patch,
                    )
                    break
                except (OutcomePolicyError, ValueError) as error:
                    revision_error = error
                    if attempt == 1:
                        raise
        await self._consider_reserve_recovery(
            game_id=game_id,
            scene=scene,
            causation_id=causation_id,
            outcome_source=outcome_source,
            player_id=player_id,
        )

    async def _consider_reserve_recovery(
        self,
        *,
        game_id: str,
        scene: dict[str, object],
        causation_id: str,
        outcome_source: dict[str, object],
        player_id: str | None,
    ) -> None:
        if self._reserve_recovery_pipeline is None:
            return
        game = self._store.game_state(game_id)
        if game is None:
            return
        current_scene = (
            self._store.scene_projection(game_id=game_id, player_id=player_id)
            if player_id is not None
            else None
        ) or scene
        assembled = self._assemble_context(
            manifest_for(PipelineName.RESERVE_RECOVERY),
            {
                "current_scene": current_scene,
                "outcome_source": outcome_source,
                "reserve_policy": game.reserve_recovery_mode.value,
                "characters": self._store.reserve_projection(game_id),
            },
            game_id=game_id,
            player_id=player_id,
        )
        try:
            decision = await self._reserve_recovery_pipeline.run(
                task="Decide whether this resolved outcome earns reserve recovery.",
                context=assembled,
            )
            if decision.safe_rest_completed and game.reserve_recovery_mode.allows_safe_rest:
                self._games.restore_reserve_for_safe_rest(
                    game_id=game_id,
                    reason=decision.safe_rest_reason or "completed safe rest",
                    causation_id=f"reserve-rest:{causation_id}",
                )
            if game.reserve_recovery_mode.allows_roleplay_award:
                for award in decision.awards:
                    self._games.award_reserve_die(
                        game_id=game_id,
                        player_id=award.player_id,
                        reason=award.reason,
                        causation_id=f"reserve-award:{causation_id}:{award.player_id}",
                    )
        except Exception:
            logger.exception("Reserve-recovery adjudication failed for %s", causation_id)
