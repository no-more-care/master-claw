from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import replace

from masterclaw.app.action_preparation import PreparedAction
from masterclaw.app.handlers.information import PreparedRoleplay
from masterclaw.app.handlers.play import (
    PreparedAdvancement,
    PreparedSceneConsequence,
)
from masterclaw.app.handlers.types import FictionContextChangedError
from masterclaw.app.i18n import tr
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.mechanics import OutcomeAuthority
from masterclaw.domain.models import HandlerResponse, IncomingMessage, OutboundDelivery
from masterclaw.domain.outcomes import OutcomePolicyError
from masterclaw.domain.state import PendingInteraction, PendingKind, PendingStatus
from masterclaw.pipelines.action import ActionResolution
from masterclaw.pipelines.base import PipelineValidationError
from masterclaw.pipelines.compound_play import PlayRequestPartKind

logger = logging.getLogger(__name__)


class CompoundPlayHandlers:
    async def _handle_compound_play(
        self,
        *,
        message: IncomingMessage,
        game_id: str,
        replacing_pending: PendingInteraction | None = None,
        clarification_answer: str | None = None,
    ) -> str | HandlerResponse:
        locale = self._locale(game_id)
        if not self._compound_planning.available:
            return tr(locale, "conversation_clarification")
        snapshot = self._compound_planning.capture(
            message=message,
            game_id=game_id,
            replacing_pending=replacing_pending,
            clarification_answer=clarification_answer,
        )
        original_request = snapshot.original_request
        manifest = manifest_for(PipelineName.COMPOUND_PLAY)
        try:
            plan = (await self._compound_planning.plan(snapshot)).plan
        except PipelineValidationError:
            logger.warning("compound_play_invalid event_id=%s", message.event_id, exc_info=True)
            return tr(locale, manifest.on_invalid.value)
        if any(part.kind is PlayRequestPartKind.HELP for part in plan.parts):
            # HELP mutates another open roll and needs the root inbox event as its durable
            # idempotency key. Synthetic compound-part ids cannot provide that contract.
            return tr(locale, "compound_help_standalone")
        if plan.clarification_question is not None:
            prior_answers = (
                list(replacing_pending.payload.get("clarification_answers", []))
                if replacing_pending is not None
                else []
            )
            if replacing_pending is not None and clarification_answer is not None:
                prior_answers.append(
                    {
                        "question": replacing_pending.prompt,
                        "answer": clarification_answer,
                    }
                )
            payload: dict[str, object] = {
                "continuation_type": "compound_plan",
                "original_request": original_request,
                "clarification_question": plan.clarification_question,
                "clarification_answers": prior_answers,
                "prompt_source_event_id": message.event_id,
                "source_event_id": str(
                    (replacing_pending.payload if replacing_pending is not None else {}).get(
                        "source_event_id", message.event_id
                    )
                ),
            }
            scene = self._store.scene_projection(game_id=game_id, player_id=message.author_id)
            if replacing_pending is None:
                self._store.put_pending(
                    PendingInteraction(
                        interaction_id=str(uuid.uuid4()),
                        game_id=game_id,
                        player_id=message.author_id,
                        scene_id=None if scene is None else str(scene["scene_id"]),
                        kind=PendingKind.CLARIFICATION,
                        prompt=plan.clarification_question,
                        payload=payload,
                        origin_channel_id=message.channel_id,
                    )
                )
            else:
                self._store.revise_pending(
                    interaction_id=replacing_pending.interaction_id,
                    player_id=message.author_id,
                    expected_revision=replacing_pending.revision,
                    kind=PendingKind.CLARIFICATION,
                    prompt=plan.clarification_question,
                    payload=payload,
                    origin_channel_id=message.channel_id,
                )
            return plan.clarification_question

        continuation_context = (
            None
            if replacing_pending is None
            else {
                "continuation_type": "compound_plan",
                "pending_question": replacing_pending.prompt,
                "player_answer": clarification_answer,
            }
        )
        part_messages = {
            index: replace(
                message,
                event_id=f"{message.event_id}:part:{index}",
                content=part.text,
            )
            for index, part in enumerate(plan.parts)
            if not (part.conditional_on_previous or part.depends_on_part is not None)
        }
        prepared_parts: dict[int, object] = {}
        try:
            for index, part in enumerate(plan.parts):
                if part.conditional_on_previous or part.depends_on_part is not None:
                    # A conditional action is intentionally interpreted only after confirmation.
                    continue
                part_message = part_messages[index]
                if part.kind is PlayRequestPartKind.ROLEPLAY:
                    prepared_parts[index] = await self._prepare_free_roleplay(
                        message=part_message,
                        game_id=game_id,
                        raise_on_invalid=True,
                    )
                elif part.kind is PlayRequestPartKind.SCENE_QUESTION:
                    prepared_parts[index] = await self._handle_scene_question(
                        message=part_message,
                        game_id=game_id,
                        raise_on_invalid=True,
                    )
                elif part.kind is PlayRequestPartKind.RULES_QUESTION:
                    prepared_parts[index] = await self._handle_rules_question(
                        message=part_message,
                        game_id=game_id,
                        raise_on_invalid=True,
                    )
                elif part.kind is PlayRequestPartKind.ADVANCEMENT:
                    prepared_parts[index] = await self._prepare_natural_advancement(
                        message=part_message,
                        game_id=game_id,
                        raise_on_invalid=True,
                    )
                elif part.kind is PlayRequestPartKind.ACTION:
                    prepared_parts[index] = await self._handle_action_declaration(
                        message=part_message,
                        game_id=game_id,
                        replacing_pending=replacing_pending,
                        continuation_context=continuation_context,
                        preflight_only=True,
                        raise_on_invalid=True,
                    )
        except PipelineValidationError:
            logger.warning("compound_part_invalid event_id=%s", message.event_id, exc_info=True)
            return tr(locale, manifest.on_invalid.value)
        except FictionContextChangedError:
            logger.warning(
                "compound_part_context_changed event_id=%s",
                message.event_id,
            )
            return tr(locale, "fiction_context_changed_retry")

        # Preflight the fallible consequence model before any earlier part is committed. Keep
        # the validated patch unapplied until the automatic action's position in plan order.
        automatic_consequences: dict[int, PreparedSceneConsequence | None] = {}
        for index, prepared in prepared_parts.items():
            if not (
                isinstance(prepared, PreparedAction)
                and prepared.interpretation.resolution is ActionResolution.AUTOMATIC
            ):
                continue
            scene = self._store.scene_projection(
                game_id=game_id,
                player_id=message.author_id,
            )
            character = self._store.character_for_player(
                game_id=game_id,
                player_id=message.author_id,
            )
            if not prepared.context.matches(character=character, scene=scene):
                return tr(locale, "fiction_context_changed_retry")
            assert scene is not None
            try:
                part_message = part_messages[index]
                automatic_consequences[index] = await self._prepare_scene_consequence(
                    game_id=game_id,
                    scene=scene,
                    causation_id=f"automatic:{part_message.event_id}",
                    outcome_source={
                        "kind": "automatic_action",
                        "declaration": part_message.content,
                        "evidence": prepared.interpretation.evidence,
                        "source_event_id": part_message.event_id,
                        "root_source_event_id": message.event_id,
                    },
                    narrator_rights=OutcomeAuthority.GM_AUTOMATIC.value,
                    player_id=message.author_id,
                )
            except (PipelineValidationError, OutcomePolicyError, ValueError):
                logger.warning(
                    "compound_automatic_consequence_invalid event_id=%s",
                    message.event_id,
                    exc_info=True,
                )
                return tr(
                    locale,
                    manifest_for(PipelineName.CONSEQUENCE_PLANNING).on_invalid.value,
                )

        # A later model preflight must not make an earlier roleplay/action snapshot stale.
        # Recheck every prepared fiction part before committing the first canonical effect.
        for prepared in prepared_parts.values():
            context = (
                prepared.context
                if isinstance(prepared, (PreparedAction, PreparedRoleplay))
                else None
            )
            if context is None:
                continue
            current_character = self._store.character_for_player(
                game_id=game_id,
                player_id=message.author_id,
            )
            current_scene = self._store.scene_projection(
                game_id=game_id,
                player_id=message.author_id,
            )
            if not context.matches(
                character=current_character,
                scene=current_scene,
            ):
                return tr(locale, "fiction_context_changed_retry")
        for prepared in automatic_consequences.values():
            if prepared is None:
                continue
            current_character = self._store.character_for_player(
                game_id=prepared.game_id,
                player_id=prepared.player_id,
            )
            current_scene = self._store.scene_by_id(
                game_id=prepared.game_id,
                scene_id=prepared.scene_id,
            )
            if (
                current_character is None
                or current_scene is None
                or current_character.character_id != prepared.actor_character_id
                or current_character.revision != prepared.expected_actor_revision
                or int(current_scene["scene_revision"]) != prepared.expected_scene_revision
            ):
                return tr(locale, "fiction_context_changed_retry")

        response_parts: list[str] = []
        deliveries: list[OutboundDelivery] = []
        completed_parts: dict[int, dict[str, str]] = {}
        deferred_roleplay_side_effects: list[PreparedRoleplay] = []
        automatic_action_index = next(iter(automatic_consequences), None)
        advancement_index = next(
            (
                index
                for index, part in enumerate(plan.parts)
                if part.kind is PlayRequestPartKind.ADVANCEMENT
            ),
            None,
        )
        deferred_primary_index = (
            automatic_action_index if automatic_action_index is not None else advancement_index
        )
        contains_action = any(part.kind is PlayRequestPartKind.ACTION for part in plan.parts)
        for index, part in enumerate(plan.parts):
            if part.conditional_on_previous or part.depends_on_part is not None:
                dependency_index = (
                    part.depends_on_part if part.depends_on_part is not None else index - 1
                )
                dependency = completed_parts.get(dependency_index)
                if dependency is None:
                    raise RuntimeError("conditional compound dependency was not completed")
                prompt = tr(locale, "compound_action_confirmation", action=part.text)
                scene = self._store.scene_projection(game_id=game_id, player_id=message.author_id)
                choice = PendingInteraction(
                    interaction_id=str(uuid.uuid4()),
                    game_id=game_id,
                    player_id=message.author_id,
                    scene_id=None if scene is None else str(scene["scene_id"]),
                    kind=PendingKind.CHOICE,
                    prompt=prompt,
                    payload={
                        "compound_action": part.text,
                        "prompt_source_event_id": message.event_id,
                        "source_event_id": (
                            message.event_id
                            if replacing_pending is None
                            else replacing_pending.payload.get("source_event_id", message.event_id)
                        ),
                        "condition_part_index": dependency_index,
                        "condition_request": dependency["request"],
                        "condition_answer": dependency["response"],
                        "condition_part_event_id": dependency["event_id"],
                    },
                    origin_channel_id=message.channel_id,
                )
                if replacing_pending is None:
                    self._store.put_pending(choice)
                else:
                    self._store.replace_pending(
                        current_interaction_id=replacing_pending.interaction_id,
                        player_id=message.author_id,
                        expected_revision=replacing_pending.revision,
                        replacement=choice,
                        answer=clarification_answer,
                        closed_by_event_id=message.event_id,
                    )
                response_parts.append(prompt)
                continue
            part_message = part_messages[index]
            if part.kind is PlayRequestPartKind.ROLEPLAY:
                prepared = prepared_parts[index]
                if isinstance(prepared, PreparedRoleplay):
                    defer_side_effects = (
                        deferred_primary_index is not None and index < deferred_primary_index
                    )
                    try:
                        result = await self._commit_prepared_roleplay(
                            prepared,
                            defer_activity_and_recovery=defer_side_effects,
                        )
                    except FictionContextChangedError:
                        return tr(locale, "fiction_context_changed_retry")
                    if defer_side_effects:
                        deferred_roleplay_side_effects.append(prepared)
                else:
                    result = str(prepared)
            elif part.kind is PlayRequestPartKind.SCENE_QUESTION:
                result = str(prepared_parts[index])
            elif part.kind is PlayRequestPartKind.RULES_QUESTION:
                result = str(prepared_parts[index])
            elif part.kind is PlayRequestPartKind.SCENE_STATUS:
                result = self._scene_status(game_id=game_id, player_id=message.author_id)
            elif part.kind is PlayRequestPartKind.CHARACTER_STATUS:
                result = self._character_status(game_id=game_id, player_id=message.author_id)
            elif part.kind is PlayRequestPartKind.GAME_STATUS:
                result = self._game_status(game_id)
            elif part.kind is PlayRequestPartKind.XP_STATUS:
                result = self._xp_status(game_id, player_id=message.author_id)
            elif part.kind is PlayRequestPartKind.HELP:
                # Defense in depth: even if a future refactor bypasses the early plan
                # rejection, never mutate HELP from a synthetic compound-part event.
                result = tr(locale, "compound_help_standalone")
            elif part.kind is PlayRequestPartKind.ADVANCEMENT:
                prepared = prepared_parts[index]
                if isinstance(prepared, PreparedAdvancement):
                    result = await self._apply_prepared_advancement(prepared)
                else:
                    result = str(prepared)
            else:
                prepared = prepared_parts[index]
                if isinstance(prepared, PreparedAction):
                    try:
                        result = await self._handle_action_declaration(
                            message=part_message,
                            game_id=game_id,
                            replacing_pending=replacing_pending,
                            continuation_context=continuation_context,
                            prepared_result=prepared,
                            raise_on_invalid=True,
                            strict_consequence=index in automatic_consequences,
                            prepared_consequence=automatic_consequences.get(index),
                            root_source_event_id=message.event_id,
                        )
                    except FictionContextChangedError:
                        return tr(locale, "fiction_context_changed_retry")
                    if isinstance(result, PreparedAction):
                        raise RuntimeError("prepared compound action was not applied")
                else:
                    result = str(prepared)
            if isinstance(result, HandlerResponse):
                if result.text.strip():
                    response_parts.append(result.text)
                deliveries.extend(result.deliveries)
                public_result = result.deliveries[-1].content if result.deliveries else result.text
            elif result.strip():
                response_parts.append(result)
                public_result = result
            else:
                public_result = ""
            completed_parts[index] = {
                "request": part.text,
                "response": public_result,
                "event_id": part_message.event_id,
            }

        for prepared in deferred_roleplay_side_effects:
            attributable_context = None
            if automatic_action_index is not None:
                automatic_message = part_messages[automatic_action_index]
                causation_id = f"automatic:{automatic_message.event_id}"
                event = self._store.domain_event_for_causation(
                    event_type="scene_patched",
                    causation_id=causation_id,
                )
                payload = None if event is None else event.get("payload")
                metadata = payload.get("effect_metadata") if isinstance(payload, Mapping) else None
                source_fiction = (
                    metadata.get("source_fiction") if isinstance(metadata, Mapping) else None
                )
                if (
                    isinstance(event, Mapping)
                    and isinstance(metadata, Mapping)
                    and metadata.get("root_source_event_id") == message.event_id
                    and source_fiction == prepared.context.as_mapping()
                ):
                    current, proven_context = self._post_effect_fiction_is_current(
                        event=event,
                        game_id=game_id,
                        player_id=message.author_id,
                        outcome_causation_id=causation_id,
                        activity_event_id=automatic_message.event_id,
                        activity_occurred_at=automatic_message.created_at,
                    )
                    if current:
                        attributable_context = proven_context
            elif advancement_index is not None:
                advancement_message = part_messages[advancement_index]
                event = self._store.domain_event_for_causation(
                    event_type="character_advanced",
                    causation_id=f"advancement:{advancement_message.event_id}",
                )
                payload = None if event is None else event.get("payload")
                if (
                    isinstance(event, Mapping)
                    and event.get("game_id") == game_id
                    and isinstance(payload, Mapping)
                    and payload.get("character_id") == prepared.context.character_id
                ):
                    proven_context = replace(
                        prepared.context,
                        character_revision=prepared.context.character_revision + 1,
                    )
                    if proven_context.matches(
                        character=self._store.character_for_player(
                            game_id=game_id,
                            player_id=message.author_id,
                        ),
                        scene=self._store.scene_projection(
                            game_id=game_id,
                            player_id=message.author_id,
                        ),
                    ):
                        attributable_context = proven_context
            await self._commit_prepared_roleplay_side_effects(
                prepared,
                attributable_context=attributable_context,
            )

        if replacing_pending is not None and not contains_action:
            current = self._store.pending_by_id(replacing_pending.interaction_id)
            if current is not None and current.status is PendingStatus.OPEN:
                self._store.resolve_pending(
                    interaction_id=current.interaction_id,
                    player_id=message.author_id,
                    expected_revision=current.revision,
                    answer=clarification_answer,
                    closed_by_event_id=message.event_id,
                )

        text = "\n\n".join(response_parts) or tr(locale, "conversation_clarification")
        if deliveries:
            return HandlerResponse(text=text, deliveries=tuple(deliveries))
        return text
