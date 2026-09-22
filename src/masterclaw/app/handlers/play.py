from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace

from masterclaw.app.action_preparation import MissingActionContext, PreparedAction
from masterclaw.app.decision_checkpoints import (
    DecisionContextChangedError,
    decision_input_fingerprint,
    decision_output_type_name,
    run_checkpointed_decision,
)
from masterclaw.app.handlers.types import (
    FictionContextChangedError,
    FictionContextSnapshot,
)
from masterclaw.app.i18n import tr
from masterclaw.app.player_narration_review import (
    NarrationVerdict,
    capture_narration_review_snapshot,
)
from masterclaw.app.response_format import format_pool_confirmation, format_roll_result
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.mechanics import (
    MechanicsError,
    OutcomeAuthority,
    PoolProposal,
    validate_pool,
)
from masterclaw.domain.models import (
    HandlerResponse,
    IncomingMessage,
    OutboundDelivery,
)
from masterclaw.domain.outcomes import (
    CanonicalOutcomePatch,
    OutcomePolicyError,
    enforce_narrator_rights_policy,
)
from masterclaw.domain.progression import AdvancementPermit
from masterclaw.domain.state import (
    NarratorRightsLevel,
    PendingInteraction,
    PendingKind,
    PendingStatus,
)
from masterclaw.domain.text_safety import (
    SecretLeakError,
    ensure_no_secret_fragments,
    hidden_secret_plot,
    redact_secret_leak,
    secret_fact_catalog,
)
from masterclaw.pipelines.action import ActionResolution
from masterclaw.pipelines.base import PipelineValidationError, TransientProviderError
from masterclaw.pipelines.conversation_actions import (
    AdvancementKind,
    AdvancementRequest,
    RollConfirmationKind,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PreparedAdvancement:
    """An authorized advancement whose model calls have completed without applying XP."""

    message: IncomingMessage
    game_id: str
    request: AdvancementRequest
    permit: AdvancementPermit | None


@dataclass(frozen=True, slots=True)
class PreparedSceneConsequence:
    """A validated consequence plan whose canonical patch has not been committed."""

    game_id: str
    scene_id: str
    expected_scene_revision: int
    actor_character_id: str
    expected_actor_revision: int
    expected_actor_location_revision: int
    expected_scene_participants: tuple[str, ...]
    causation_id: str
    patch: CanonicalOutcomePatch
    secret_reveals: dict[str, str]
    scene: dict[str, object]
    outcome_source: dict[str, object]
    player_id: str


class PlayHandlers:
    _CANCEL_WORDS = frozenset({"отмена", "cancel"})

    @staticmethod
    def _cancelled_remainder(content: str) -> str | None:
        match = re.match(
            r"^\s*(?:отмена|cancel)\b(?:\s*[,;:.—-]\s*|\s+)(?P<rest>.+?)\s*$",
            content,
            flags=re.IGNORECASE,
        )
        return None if match is None else match.group("rest").strip()

    @staticmethod
    def _combine_responses(prefix: str, response: str | HandlerResponse) -> str | HandlerResponse:
        text = f"{prefix}\n\n{response.text if isinstance(response, HandlerResponse) else response}"
        if isinstance(response, HandlerResponse):
            return HandlerResponse(
                text=text,
                deliveries=response.deliveries,
                completion_game_id=response.completion_game_id,
                render_live_status=response.render_live_status,
            )
        return text

    def _recovery_adjusted_fiction_snapshot(
        self,
        *,
        snapshot: FictionContextSnapshot,
        outcome_causation_id: str,
    ) -> FictionContextSnapshot:
        """Accept only actor revisions proven to come from this outcome's recovery."""

        revision_delta = 0
        rest = self._store.domain_event_for_causation(
            event_type="reserve_safe_rest",
            causation_id=f"reserve-rest:{outcome_causation_id}",
        )
        if rest is not None:
            if rest.get("game_id") != snapshot.game_id:
                raise RuntimeError("reserve rest replay belongs to another game")
            payload = rest.get("payload")
            if not isinstance(payload, Mapping):
                raise RuntimeError("reserve rest replay payload is invalid")
            restored = payload.get("restored", ())
            if not isinstance(restored, list):
                raise RuntimeError("reserve rest replay targets are invalid")
            restored_ids: list[str] = []
            for item in restored:
                if not isinstance(item, Mapping):
                    raise RuntimeError("reserve rest replay target is invalid")
                character_id = item.get("character_id")
                before = item.get("before")
                after = item.get("after")
                if (
                    not isinstance(character_id, str)
                    or not character_id
                    or isinstance(before, bool)
                    or not isinstance(before, int)
                    or isinstance(after, bool)
                    or not isinstance(after, int)
                    or not 0 <= before < after <= 7
                ):
                    raise RuntimeError("reserve rest replay target is invalid")
                restored_ids.append(character_id)
            if len(restored_ids) != len(set(restored_ids)):
                raise RuntimeError("reserve rest replay targets are duplicated")
            revision_delta += int(snapshot.character_id in restored_ids)
        award = self._store.domain_event_for_causation(
            event_type="reserve_roleplay_award",
            causation_id=(f"reserve-award:{outcome_causation_id}:{snapshot.player_id}"),
        )
        if award is not None:
            if award.get("game_id") != snapshot.game_id:
                raise RuntimeError("reserve award replay belongs to another game")
            payload = award.get("payload")
            if not isinstance(payload, Mapping):
                raise RuntimeError("reserve award replay payload is invalid")
            before = payload.get("before")
            after = payload.get("after")
            if (
                payload.get("player_id") != snapshot.player_id
                or payload.get("character_id") != snapshot.character_id
                or isinstance(before, bool)
                or not isinstance(before, int)
                or isinstance(after, bool)
                or not isinstance(after, int)
                or not 0 <= before <= after <= 7
                or after not in (before, before + 1)
            ):
                raise RuntimeError("reserve award replay payload is invalid")
            if after != before:
                revision_delta += 1
        if revision_delta > 1:
            raise RuntimeError("reserve recovery replay revision delta is invalid")
        if revision_delta == 0:
            return snapshot
        return replace(
            snapshot,
            character_revision=snapshot.character_revision + revision_delta,
        )

    def _activity_adjusted_fiction_snapshot(
        self,
        *,
        snapshot: FictionContextSnapshot,
        event_id: str,
        occurred_at,
    ) -> FictionContextSnapshot:
        activity = self._store.activity_record_for_causation(
            causation_id=f"activity:{event_id}",
            game_id=snapshot.game_id,
            occurred_at=occurred_at,
        )
        if activity is None:
            return snapshot
        _, affected_character_ids = activity
        if snapshot.character_id not in affected_character_ids:
            return snapshot
        return replace(
            snapshot,
            character_revision=snapshot.character_revision + 1,
        )

    def _post_effect_fiction_is_current(
        self,
        *,
        event: Mapping[str, object],
        game_id: str,
        player_id: str,
        outcome_causation_id: str | None = None,
        activity_event_id: str | None = None,
        activity_occurred_at=None,
    ) -> tuple[bool, FictionContextSnapshot | None]:
        """Compare live fiction with the exact snapshot committed by an outcome patch."""

        payload = event.get("payload")
        if event.get("game_id") != game_id or not isinstance(payload, Mapping):
            return False, None
        raw_snapshot = payload.get("post_effect_fiction")
        if not isinstance(raw_snapshot, Mapping):
            return False, None
        try:
            snapshot = FictionContextSnapshot.from_mapping(raw_snapshot)
            if outcome_causation_id is not None:
                snapshot = self._recovery_adjusted_fiction_snapshot(
                    snapshot=snapshot,
                    outcome_causation_id=outcome_causation_id,
                )
            if (activity_event_id is None) != (activity_occurred_at is None):
                raise RuntimeError("activity replay identity is incomplete")
            if activity_event_id is not None:
                snapshot = self._activity_adjusted_fiction_snapshot(
                    snapshot=snapshot,
                    event_id=activity_event_id,
                    occurred_at=activity_occurred_at,
                )
            patched_scene_id = str(payload["scene_id"])
            patched_scene_revision = int(payload["scene_revision"])
        except (KeyError, TypeError, ValueError):
            return False, None
        actor = self._store.character_for_player(
            game_id=game_id,
            player_id=player_id,
        )
        current_scene = self._store.scene_projection(
            game_id=game_id,
            player_id=player_id,
        )
        patched_scene = self._store.scene_by_id(
            game_id=game_id,
            scene_id=patched_scene_id,
        )
        current = (
            snapshot.game_id == game_id
            and snapshot.player_id == player_id
            and snapshot.character_id == str(payload.get("actor_character_id") or "")
            and patched_scene is not None
            and int(patched_scene["scene_revision"]) == patched_scene_revision
            and snapshot.matches(character=actor, scene=current_scene)
        )
        return current, snapshot

    async def _replay_committed_automatic_action(
        self,
        *,
        message: IncomingMessage,
        game_id: str,
        event: Mapping[str, object],
        replacing_pending: PendingInteraction | None,
        continuation_context: dict[str, object] | None,
    ) -> str | HandlerResponse:
        """Resume post-effect work without reinterpreting an already committed action."""

        locale = self._locale(game_id)
        if event.get("game_id") != game_id:
            raise RuntimeError("committed automatic action belongs to another game")
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            raise RuntimeError("committed automatic action payload is invalid")
        metadata = payload.get("effect_metadata")
        if (
            not isinstance(metadata, Mapping)
            or metadata.get("kind") != "automatic_action"
            or metadata.get("source_event_id") != message.event_id
        ):
            raise RuntimeError("committed automatic action replay metadata is invalid")
        declaration = metadata.get("declaration")
        source_message_content = (continuation_context or {}).get(
            "player_answer"
        ) or message.content
        if (
            not isinstance(declaration, str)
            or metadata.get("source_message_content") != source_message_content
        ):
            raise RuntimeError("committed automatic action declaration mismatch")

        scene_id = str(payload.get("scene_id") or "")
        patched_scene = self._store.scene_by_id(game_id=game_id, scene_id=scene_id)
        current_projection = self._store.scene_projection(
            game_id=game_id,
            player_id=message.author_id,
        )
        post_effect_context_is_current, _ = self._post_effect_fiction_is_current(
            event=event,
            game_id=game_id,
            player_id=message.author_id,
            outcome_causation_id=f"automatic:{message.event_id}",
            activity_event_id=message.event_id,
            activity_occurred_at=message.created_at,
        )
        causation_id = f"automatic:{message.event_id}"
        if (
            post_effect_context_is_current
            or self._store.reserve_recovery_decision(causation_id) is not None
        ):
            await self._consider_reserve_recovery(
                game_id=game_id,
                scene=current_projection or dict(patched_scene or {}),
                causation_id=causation_id,
                outcome_source={
                    "kind": "automatic_action",
                    "declaration": declaration,
                    "evidence": list(metadata.get("evidence") or ()),
                },
                player_id=message.author_id,
            )
        else:
            logger.warning(
                "automatic_recovery_skipped_stale_context event_id=%s",
                message.event_id,
            )

        response_mode = metadata.get("response_mode")
        if response_mode == "inline":
            self._store.record_activity(
                game_id=game_id,
                occurred_at=message.created_at,
                causation_id=f"activity:{message.event_id}",
            )
            if replacing_pending is not None:
                self._store.resolve_pending(
                    interaction_id=replacing_pending.interaction_id,
                    player_id=message.author_id,
                    expected_revision=replacing_pending.revision,
                    answer=str(
                        (continuation_context or {}).get("player_answer") or message.content
                    ),
                    closed_by_event_id=message.event_id,
                )
            return tr(locale, "automatic_updated")
        target_channel_id = metadata.get("original_target_channel_id")
        if response_mode != "delivery" or not isinstance(target_channel_id, str):
            raise RuntimeError("committed automatic action response envelope is invalid")
        narration_context_is_current, narration_context = self._post_effect_fiction_is_current(
            event=event,
            game_id=game_id,
            player_id=message.author_id,
            outcome_causation_id=f"automatic:{message.event_id}",
            activity_event_id=message.event_id,
            activity_occurred_at=message.created_at,
        )
        _, narration_decision_context = self._post_effect_fiction_is_current(
            event=event,
            game_id=game_id,
            player_id=message.author_id,
            outcome_causation_id=f"automatic:{message.event_id}",
        )
        game = self._store.game_state(game_id)
        updated_scene = self._store.scene_projection(
            game_id=game_id,
            player_id=message.author_id,
        )
        updated_actor = self._actor_character_projection(
            game_id=game_id,
            player_id=message.author_id,
        )
        if (
            narration_context_is_current
            and narration_context is not None
            and narration_decision_context is not None
            and self._narrative_pipeline is not None
            and game is not None
            and updated_scene is not None
            and updated_actor is not None
        ):
            assembled = self._assemble_context(
                manifest_for(PipelineName.OUTCOME_NARRATION),
                {
                    "session_brief": self._narrative_session_brief(game),
                    "current_scene": updated_scene,
                    "actor_character": updated_actor,
                    "roll_result": {
                        "resolution": "automatic",
                        "declaration": declaration,
                    },
                },
                game_id=game_id,
                channel_id=message.channel_id,
                player_id=message.author_id,
            )
            try:
                narrative = await run_checkpointed_decision(
                    store=self._store,
                    event_id=message.event_id,
                    pipeline_key="outcome_narration:automatic",
                    pipeline=self._narrative_pipeline,
                    task="Narrate the automatic action outcome.",
                    context=assembled,
                    game_id=game_id,
                    input_fingerprint=decision_input_fingerprint(
                        {
                            "stage": "outcome_narration:automatic",
                            "declaration": declaration,
                            **narration_decision_context.as_mapping(),
                        }
                    ),
                )
                still_current, _ = self._post_effect_fiction_is_current(
                    event=event,
                    game_id=game_id,
                    player_id=message.author_id,
                    outcome_causation_id=f"automatic:{message.event_id}",
                    activity_event_id=message.event_id,
                    activity_occurred_at=message.created_at,
                )
                narrative_text = (
                    narrative.narrative if still_current else tr(locale, "narrative_fallback")
                )
            except Exception:
                logger.exception(
                    "Narrative replay failed after automatic patch %s; using safe fallback",
                    message.event_id,
                )
                narrative_text = tr(locale, "narrative_fallback")
        else:
            narrative_text = tr(locale, "narrative_fallback")
        narrative_text = redact_secret_leak(
            narrative_text,
            self._secret_plot_for_game(game_id),
            replacement=tr(locale, "narrative_fallback"),
        )
        self._store.record_activity(
            game_id=game_id,
            occurred_at=message.created_at,
            causation_id=f"activity:{message.event_id}",
        )
        if replacing_pending is not None:
            self._store.resolve_pending(
                interaction_id=replacing_pending.interaction_id,
                player_id=message.author_id,
                expected_revision=replacing_pending.revision,
                answer=str((continuation_context or {}).get("player_answer") or message.content),
                closed_by_event_id=message.event_id,
            )
        return HandlerResponse(
            tr(locale, "automatic_done"),
            (OutboundDelivery(target_channel_id, narrative_text, "narrative"),),
        )

    async def _cancel_pending_and_continue(
        self, *, message: IncomingMessage, pending, remainder: str
    ) -> str | HandlerResponse:
        cancelled = self._cancel_pending_interaction(message=message, pending=pending)
        channel = self._channel_for_message(message)
        continuation = await self._dispatch(
            replace(
                message,
                event_id=f"{message.event_id}:after-cancel",
                content=remainder,
            ),
            channel,
        )
        return self._combine_responses(cancelled, continuation)

    def _cancelled_pending_text(self, *, pending: PendingInteraction) -> str:
        locale = self._locale(pending.game_id)
        if pending.kind is PendingKind.POOL_CONFIRMATION:
            return tr(locale, "roll_cancelled")
        if pending.kind is PendingKind.PLAYER_NARRATION:
            return tr(locale, "narration_cancelled")
        return tr(locale, "pending_cancelled")

    def _cancel_pending_interaction(self, *, message: IncomingMessage, pending) -> str:
        self._store.cancel_pending(
            interaction_id=pending.interaction_id,
            player_id=message.author_id,
            expected_revision=pending.revision,
            closed_by_event_id=message.event_id,
        )
        return self._cancelled_pending_text(pending=pending)

    async def _replay_closed_pending(
        self,
        *,
        message: IncomingMessage,
        pending: PendingInteraction,
    ) -> str | HandlerResponse:
        """Reconstruct a terminal pending response without treating the event as a new turn."""
        committed_automatic = self._store.domain_event_for_causation(
            event_type="scene_patched",
            causation_id=f"automatic:{message.event_id}",
        )
        if committed_automatic is not None:
            return await self._replay_committed_automatic_action(
                message=message,
                game_id=pending.game_id,
                event=committed_automatic,
                replacing_pending=None,
                continuation_context=None,
            )
        if pending.status is PendingStatus.CANCELLED:
            cancelled = self._cancelled_pending_text(pending=pending)
            remainder = self._cancelled_remainder(message.content)
            if remainder is None:
                return cancelled
            channel = self._channel_for_message(message)
            continuation = await self._dispatch(
                replace(
                    message,
                    event_id=f"{message.event_id}:after-cancel",
                    content=remainder,
                ),
                channel,
            )
            return self._combine_responses(cancelled, continuation)
        if (
            pending.status is PendingStatus.EXPIRED
            and pending.kind is PendingKind.POOL_CONFIRMATION
        ):
            return tr(self._locale(pending.game_id), "pending_pool_expired")
        if pending.status is PendingStatus.EXPIRED:
            channel = self._channel_for_message(message)
            return await self._dispatch(
                replace(
                    message,
                    event_id=f"{message.event_id}:after-expire",
                ),
                channel,
            )
        if pending.kind is PendingKind.POOL_CONFIRMATION:
            roll = self._store.roll_for_confirmation_event(message.event_id)
            if roll is not None:
                return await self._render_roll_outcome(message=message, roll=roll)
        if pending.kind is PendingKind.PLAYER_NARRATION:
            committed = self._store.domain_event_for_causation(
                event_type="scene_patched",
                causation_id=f"player-narration:{pending.interaction_id}",
            )
            if committed is not None:
                return await self._replay_committed_player_narration(
                    message=message,
                    pending=pending,
                    event=committed,
                )
            approved_narration = str(pending.payload.get("answer") or "").strip()
            game = self._store.game_state(pending.game_id)
            accepted = tr(self._locale(pending.game_id), "narration_accepted")
            if approved_narration and game is not None and game.narrative_channel_id is not None:
                return HandlerResponse(
                    accepted,
                    (
                        OutboundDelivery(
                            game.narrative_channel_id,
                            approved_narration,
                            "player_narration",
                        ),
                    ),
                )
            return accepted
        return tr(self._locale(pending.game_id), "pending_answer_recorded")

    async def _handle_natural_advancement(self, *, message: IncomingMessage, game_id: str) -> str:
        prepared = await self._prepare_natural_advancement(message=message, game_id=game_id)
        if isinstance(prepared, str):
            return prepared
        return await self._apply_prepared_advancement(prepared)

    async def _prepare_natural_advancement(
        self,
        *,
        message: IncomingMessage,
        game_id: str,
        raise_on_invalid: bool = False,
    ) -> PreparedAdvancement | str:
        """Run intake and safety models without applying a compound advancement yet."""

        locale = self._locale(game_id)
        if self._advancement is None or self._advancement_intake_pipeline is None:
            if raise_on_invalid:
                raise PipelineValidationError("advancement pipeline is unavailable")
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
            request = await run_checkpointed_decision(
                store=self._store,
                event_id=message.event_id,
                pipeline_key="advancement_intake",
                pipeline=self._advancement_intake_pipeline,
                task="Extract the requested character advancement.",
                context=assembled,
                game_id=game_id,
            )
        except PipelineValidationError:
            logger.warning(
                "advancement_intake_invalid event_id=%s", message.event_id, exc_info=True
            )
            if raise_on_invalid:
                raise
            return tr(locale, manifest.on_invalid.value)
        causation_id = f"advancement:{message.event_id}"
        replayed = self._store.character_for_advancement_causation(
            causation_id=causation_id,
            game_id=game_id,
            player_id=message.author_id,
        )
        try:
            permit = (
                None
                if replayed is not None
                else await self._advancement.authorize_request(
                    game_id=game_id,
                    player_id=message.author_id,
                    request=self._advancement_request_projection(request),
                    checkpoint_event_id=message.event_id,
                )
            )
        except PipelineValidationError:
            logger.warning(
                "advancement_safety_invalid event_id=%s", message.event_id, exc_info=True
            )
            if raise_on_invalid:
                raise
            return tr(
                locale,
                manifest_for(PipelineName.ADVANCEMENT_SAFETY).on_invalid.value,
            )
        except ValueError as error:
            return tr(locale, "advancement_rejected", error=error)
        return PreparedAdvancement(
            message=message,
            game_id=game_id,
            request=request,
            permit=permit,
        )

    async def _apply_prepared_advancement(self, prepared: PreparedAdvancement) -> str:
        """Apply an already authorized advancement without another model call."""

        locale = self._locale(prepared.game_id)
        request = prepared.request
        assert self._advancement is not None
        try:
            if request.kind is AdvancementKind.RAISE:
                updated = await self._advancement.raise_trait(
                    game_id=prepared.game_id,
                    player_id=prepared.message.author_id,
                    trait_name=request.trait_name,
                    new_aspect=request.aspects[0],
                    causation_id=f"advancement:{prepared.message.event_id}",
                    permit=prepared.permit,
                )
                return tr(
                    locale,
                    "trait_raised",
                    trait=request.trait_name,
                    xp=updated.experience_available,
                )
            updated = await self._advancement.learn_trait(
                game_id=prepared.game_id,
                player_id=prepared.message.author_id,
                trait_name=request.trait_name,
                aspects=(request.aspects[0], request.aspects[1]),
                justification=request.justification or "",
                causation_id=f"advancement:{prepared.message.event_id}",
                permit=prepared.permit,
            )
            return tr(
                locale,
                "trait_learned",
                trait=request.trait_name,
                xp=updated.experience_available,
            )
        except ValueError as error:
            return tr(locale, "advancement_rejected", error=error)

    @staticmethod
    def _advancement_request_projection(request: AdvancementRequest) -> dict[str, object]:
        if request.kind is AdvancementKind.RAISE:
            return {
                "kind": "raise",
                "trait": request.trait_name,
                "new_aspect": request.aspects[0],
            }
        return {
            "kind": "learn",
            "trait": request.trait_name,
            "aspects": tuple(request.aspects),
            "justification": request.justification or "",
        }

    def _player_narration_source_context(
        self,
        *,
        pending: PendingInteraction,
        roll,
        character,
        scene: Mapping[str, object],
    ) -> tuple[FictionContextSnapshot, PendingInteraction]:
        """Bind a narration answer to the exact roll fiction it is completing."""

        source_pending = self._store.pending_by_id(roll.interaction_id)
        if source_pending is None:
            raise FictionContextChangedError("player narration lost its source interaction")
        payload = source_pending.payload
        try:
            source_scene_revision = int(payload["scene_revision"])
            source_location_revision = int(payload["location_revision"])
            source_actor_revision = int(payload["character_revision"])
        except (KeyError, TypeError, ValueError) as error:
            raise FictionContextChangedError(
                "player narration source revisions are missing"
            ) from error
        if (
            pending.game_id != source_pending.game_id
            or pending.player_id != source_pending.player_id
            or pending.scene_id is None
            or pending.scene_id != source_pending.scene_id
            or str(scene["scene_id"]) != pending.scene_id
            or int(scene["scene_revision"]) != source_scene_revision
            or int(scene["location_revision"]) != source_location_revision
            or character.character_id != roll.character_id
            or character.character_id != payload.get("character_id")
            or character.revision != source_actor_revision + 1
        ):
            raise FictionContextChangedError(
                "player narration source fiction changed after the roll"
            )
        return (
            FictionContextSnapshot.capture(
                game_id=pending.game_id,
                player_id=pending.player_id,
                character=character,
                scene=scene,
            ),
            source_pending,
        )

    def _player_narration_effect_context_is_current(
        self,
        *,
        event: Mapping[str, object],
        source_context: FictionContextSnapshot,
        activity_event_id: str,
        activity_occurred_at,
    ) -> bool:
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            return False
        try:
            identity_matches = (
                event.get("game_id") == source_context.game_id
                and str(payload["scene_id"]) == source_context.scene_id
                and str(payload["actor_character_id"]) == source_context.character_id
            )
        except (KeyError, TypeError, ValueError):
            return False
        if not identity_matches:
            return False
        outcome_causation_id = str(event.get("causation_id") or "")
        if not outcome_causation_id.startswith("player-narration:"):
            return False
        current, _ = self._post_effect_fiction_is_current(
            event=event,
            game_id=source_context.game_id,
            player_id=source_context.player_id,
            outcome_causation_id=outcome_causation_id,
            activity_event_id=activity_event_id,
            activity_occurred_at=activity_occurred_at,
        )
        return current

    async def _replay_committed_player_narration(
        self,
        *,
        message: IncomingMessage,
        pending: PendingInteraction,
        event: Mapping[str, object],
    ) -> str | HandlerResponse:
        """Finish a committed narration effect from its immutable event envelope."""

        locale = self._locale(pending.game_id)
        payload = event.get("payload")
        if event.get("game_id") != pending.game_id or not isinstance(payload, Mapping):
            raise RuntimeError("committed player narration belongs to another game")
        metadata = payload.get("effect_metadata")
        if not isinstance(metadata, Mapping) or metadata.get("kind") != "player_narration":
            raise RuntimeError("committed player narration metadata is invalid")
        raw_context = metadata.get("source_fiction")
        if not isinstance(raw_context, Mapping):
            raise RuntimeError("committed player narration source fiction is missing")
        source_context = FictionContextSnapshot.from_mapping(raw_context)
        approved_narration = metadata.get("approved_narration")
        submitted_narration = metadata.get("submitted_narration")
        roll_id = metadata.get("roll_id")
        if (
            source_context.game_id != pending.game_id
            or source_context.player_id != message.author_id
            or metadata.get("source_event_id") != message.event_id
            or metadata.get("pending_interaction_id") != pending.interaction_id
            or metadata.get("player_id") != message.author_id
            or pending.payload.get("roll_id") != roll_id
            or not isinstance(approved_narration, str)
            or not approved_narration.strip()
            or submitted_narration != message.content
        ):
            raise RuntimeError("committed player narration replay identity mismatch")
        try:
            expected_pending_revision = int(metadata["pending_revision"])
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("committed player narration pending revision is invalid") from error
        if pending.status is PendingStatus.OPEN and pending.revision != expected_pending_revision:
            raise RuntimeError("committed player narration pending revision changed")

        causation_id = f"player-narration:{pending.interaction_id}"
        context_is_current = self._player_narration_effect_context_is_current(
            event=event,
            source_context=source_context,
            activity_event_id=message.event_id,
            activity_occurred_at=message.created_at,
        )
        if context_is_current or self._store.reserve_recovery_decision(causation_id) is not None:
            await self._consider_reserve_recovery(
                game_id=pending.game_id,
                scene=(
                    self._store.scene_by_id(
                        game_id=pending.game_id,
                        scene_id=source_context.scene_id,
                    )
                    or {}
                ),
                causation_id=causation_id,
                outcome_source={
                    "kind": "player_narration",
                    "text": approved_narration,
                    "roll_id": str(roll_id),
                },
                player_id=message.author_id,
            )
        else:
            logger.warning(
                "player_narration_recovery_skipped_stale_context event_id=%s",
                message.event_id,
            )

        # Recovery can await a provider. Re-read every canonical revision before recording or
        # publishing the old narration; a concurrent move or patch must fail closed.
        context_is_current = self._player_narration_effect_context_is_current(
            event=event,
            source_context=source_context,
            activity_event_id=message.event_id,
            activity_occurred_at=message.created_at,
        )
        if context_is_current:
            self._store.record_interaction_event(
                game_id=pending.game_id,
                scene_id=source_context.scene_id,
                actor_role="player_character",
                kind="accepted_player_narration",
                text=approved_narration,
                causation_id=causation_id,
                player_id=message.author_id,
                metadata={
                    "roll_id": str(roll_id),
                    "source_event_id": message.event_id,
                    "fiction_context": source_context.as_mapping(),
                },
            )
        if pending.status is PendingStatus.OPEN:
            self._store.resolve_pending(
                interaction_id=pending.interaction_id,
                player_id=message.author_id,
                expected_revision=pending.revision,
                answer=approved_narration if context_is_current else None,
                closed_by_event_id=message.event_id,
            )
        elif (
            pending.status is not PendingStatus.RESOLVED
            or pending.payload.get("closed_by_event_id") != message.event_id
        ):
            context_is_current = False
        self._store.record_activity(
            game_id=pending.game_id,
            occurred_at=message.created_at,
            causation_id=f"activity:{message.event_id}",
        )
        if not context_is_current:
            logger.warning(
                "player_narration_publication_skipped_stale_context event_id=%s",
                message.event_id,
            )
            return tr(locale, "player_narration_committed_context_changed")

        response_mode = metadata.get("response_mode")
        if response_mode == "inline":
            return tr(locale, "narration_accepted")
        target_channel_id = metadata.get("original_target_channel_id")
        if response_mode != "delivery" or not isinstance(target_channel_id, str):
            raise RuntimeError("committed player narration response envelope is invalid")
        return HandlerResponse(
            tr(locale, "narration_accepted"),
            (
                OutboundDelivery(
                    target_channel_id,
                    approved_narration,
                    "player_narration",
                ),
            ),
        )

    async def _handle_player_narration(
        self, *, message: IncomingMessage, pending
    ) -> str | HandlerResponse:
        locale = self._locale(pending.game_id)
        normalized = message.content.strip().casefold()
        remainder = self._cancelled_remainder(message.content)
        if remainder is not None:
            return await self._cancel_pending_and_continue(
                message=message,
                pending=pending,
                remainder=remainder,
            )
        if normalized in self._CANCEL_WORDS:
            return self._cancel_pending_interaction(message=message, pending=pending)
        if re.match(
            r"^\s*(?:теперь|вместо\s+этого|затем|следом|now|instead|next)\b",
            message.content,
            flags=re.IGNORECASE,
        ):
            # A clearly signalled new turn must never be published as the outcome of an old roll.
            return tr(locale, "pending_reminder", prompt=pending.prompt)
        causation_id = f"player-narration:{pending.interaction_id}"
        committed = self._store.domain_event_for_causation(
            event_type="scene_patched",
            causation_id=causation_id,
        )
        if committed is not None:
            return await self._replay_committed_player_narration(
                message=message,
                pending=pending,
                event=committed,
            )
        if self._narration_rights_decider is None:
            return tr(locale, "rights_review_unavailable")
        game = self._store.game_state(pending.game_id)
        scene = self._store.scene_projection(game_id=pending.game_id, player_id=message.author_id)
        roll = self._store.roll_by_id(str(pending.payload.get("roll_id", "")))
        actor = self._store.character_for_player(
            game_id=pending.game_id,
            player_id=message.author_id,
        )
        if game is None or scene is None or roll is None or actor is None:
            return tr(locale, "rights_context_missing")
        try:
            source_context, source_pending = self._player_narration_source_context(
                pending=pending,
                roll=roll,
                character=actor,
                scene=scene,
            )
        except FictionContextChangedError:
            logger.warning(
                "player_narration_source_context_changed event_id=%s",
                message.event_id,
            )
            return tr(locale, "fiction_context_changed_retry")
        manifest = manifest_for(PipelineName.PLAYER_NARRATION_REVIEW)
        snapshot = capture_narration_review_snapshot(
            capture_context=self._capture_context_inputs,
            message=message,
            pending=pending,
            source_pending=source_pending,
            fiction=source_context,
            roll=roll,
            rights_level=game.narrator_rights_level.value,
            locale=locale,
            session_brief=self._narrative_session_brief(game),
            scene=scene,
            actor_projection=self._actor_character_projection(
                game_id=pending.game_id,
                player_id=message.author_id,
            ),
        )
        try:
            assessment = await self._narration_rights_decider.assess(snapshot, message.event_id)
            text = await self._narration_text_port.materialize(
                snapshot,
                assessment,
                message.event_id,
            )
        except DecisionContextChangedError:
            logger.warning(
                "player_narration_review_context_changed event_id=%s",
                message.event_id,
            )
            return tr(locale, "fiction_context_changed_retry")
        except PipelineValidationError:
            logger.warning("player_narration_invalid event_id=%s", message.event_id, exc_info=True)
            return tr(locale, manifest.on_invalid.value)
        current_actor = self._store.character_for_player(
            game_id=pending.game_id,
            player_id=message.author_id,
        )
        current_scene = self._store.scene_projection(
            game_id=pending.game_id,
            player_id=message.author_id,
        )
        if not source_context.matches(
            character=current_actor,
            scene=current_scene,
        ):
            logger.warning(
                "player_narration_context_changed_after_review event_id=%s",
                message.event_id,
            )
            return tr(locale, "fiction_context_changed_retry")
        assert current_scene is not None
        scene = current_scene
        if assessment.verdict is not NarrationVerdict.ALLOW:
            return redact_secret_leak(
                text.feedback_text or tr(locale, manifest.on_invalid.value),
                self._secret_plot_for_game(pending.game_id),
                replacement=tr(locale, manifest.on_invalid.value),
            )
        approved_narration = text.publication_text
        if approved_narration is None:
            return tr(locale, manifest.on_invalid.value)
        try:
            ensure_no_secret_fragments(
                approved_narration,
                self._secret_plot_for_game(pending.game_id),
            )
        except SecretLeakError:
            return tr(locale, manifest.on_invalid.value)
        response_mode = "delivery" if game.narrative_channel_id is not None else "inline"
        if self._consequence_pipeline is not None:
            try:
                await self._ensure_scene_consequence(
                    game_id=pending.game_id,
                    scene=scene,
                    causation_id=causation_id,
                    outcome_source={
                        "kind": "player_narration",
                        "text": approved_narration,
                        "submitted_narration": message.content,
                        "roll_id": roll.roll_id,
                        "hits": roll.hits,
                        "difficulty": roll.difficulty,
                        "source_event_id": message.event_id,
                        "pending_interaction_id": pending.interaction_id,
                        "pending_revision": pending.revision,
                        "source_interaction_id": source_pending.interaction_id,
                        "source_interaction_revision": source_pending.revision,
                        "player_id": message.author_id,
                        "source_fiction": source_context.as_mapping(),
                        "response_mode": response_mode,
                        "original_target_channel_id": game.narrative_channel_id,
                    },
                    narrator_rights=roll.narrator_rights.value,
                    player_id=message.author_id,
                )
            except TransientProviderError:
                if not self._has_durable_inbox_event(
                    message.event_id
                ) or not self._store.provider_retry_exhausted(message.event_id):
                    raise
                logger.warning(
                    "player_narration_consequence_provider_exhausted event_id=%s",
                    message.event_id,
                    exc_info=True,
                )
                return tr(locale, "player_narration_consequence_provider_unavailable")
            except (PipelineValidationError, OutcomePolicyError, ValueError):
                logger.warning(
                    "player_narration_consequence_invalid event_id=%s",
                    message.event_id,
                    exc_info=True,
                )
                return tr(locale, "player_narration_consequence_retry")
            except RuntimeError as error:
                if str(error) not in {
                    "scene revision conflict",
                    "actor character revision conflict",
                    "actor location revision conflict",
                    "scene participants changed",
                    "actor is no longer in the patched scene",
                    "prepared consequence actor context changed before commit",
                }:
                    raise
                logger.warning(
                    "player_narration_consequence_context_changed event_id=%s",
                    message.event_id,
                )
                return tr(locale, "fiction_context_changed_retry")
            committed = self._store.domain_event_for_causation(
                event_type="scene_patched",
                causation_id=causation_id,
            )
            if committed is None:
                raise RuntimeError("player narration consequence lost its canonical event")
            return await self._replay_committed_player_narration(
                message=message,
                pending=pending,
                event=committed,
            )
        if not source_context.matches(
            character=self._store.character_for_player(
                game_id=pending.game_id,
                player_id=message.author_id,
            ),
            scene=self._store.scene_projection(
                game_id=pending.game_id,
                player_id=message.author_id,
            ),
        ):
            return tr(locale, "fiction_context_changed_retry")
        self._store.record_interaction_event(
            game_id=pending.game_id,
            scene_id=source_context.scene_id,
            actor_role="player_character",
            kind="accepted_player_narration",
            text=approved_narration,
            causation_id=causation_id,
            player_id=message.author_id,
            metadata={
                "roll_id": roll.roll_id,
                "source_event_id": message.event_id,
                "fiction_context": source_context.as_mapping(),
                "response_mode": response_mode,
                "original_target_channel_id": game.narrative_channel_id,
            },
        )
        self._store.record_activity(
            game_id=pending.game_id,
            occurred_at=message.created_at,
            causation_id=f"activity:{message.event_id}",
        )
        self._store.resolve_pending(
            interaction_id=pending.interaction_id,
            player_id=message.author_id,
            expected_revision=pending.revision,
            answer=approved_narration,
            closed_by_event_id=message.event_id,
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

    async def _handle_action_declaration(
        self,
        *,
        message: IncomingMessage,
        game_id: str,
        replacing_pending: PendingInteraction | None = None,
        continuation_context: dict[str, object] | None = None,
        prepared_result: PreparedAction | None = None,
        preflight_only: bool = False,
        raise_on_invalid: bool = False,
        strict_consequence: bool = False,
        prepared_consequence: PreparedSceneConsequence | None = None,
        root_source_event_id: str | None = None,
    ) -> str | HandlerResponse | PreparedAction:
        locale = self._locale(game_id)
        committed_automatic = self._store.domain_event_for_causation(
            event_type="scene_patched",
            causation_id=f"automatic:{message.event_id}",
        )
        if committed_automatic is not None:
            return await self._replay_committed_automatic_action(
                message=message,
                game_id=game_id,
                event=committed_automatic,
                replacing_pending=replacing_pending,
                continuation_context=continuation_context,
            )
        if not self._action_preparation.available and prepared_result is None:
            if raise_on_invalid:
                raise PipelineValidationError("action interpretation pipeline is unavailable")
            return tr(locale, "action_pipeline_unavailable")
        try:
            preparation = await self._action_preparation.prepare(
                message=message,
                game_id=game_id,
                locale=locale,
                replacing_pending=replacing_pending,
                continuation_context=continuation_context,
                prepared_result=prepared_result,
            )
        except DecisionContextChangedError as error:
            logger.warning("action_interpretation_context_changed event_id=%s", message.event_id)
            if raise_on_invalid:
                raise FictionContextChangedError(str(error)) from error
            return tr(locale, "fiction_context_changed_retry")
        except PipelineValidationError:
            logger.warning(
                "action_interpretation_invalid event_id=%s", message.event_id, exc_info=True
            )
            if raise_on_invalid:
                raise
            return tr(locale, manifest_for(PipelineName.ACTION_INTERPRETATION).on_invalid.value)
        except FictionContextChangedError:
            if raise_on_invalid:
                raise
            return tr(locale, "fiction_context_changed_retry")
        if isinstance(preparation, MissingActionContext):
            return tr(locale, preparation.value)
        prepared_action = preparation
        result = preparation.interpretation
        character = preparation.snapshot.character
        scene = preparation.snapshot.scene
        sheet = character.sheet
        if preflight_only:
            if result.resolution is ActionResolution.AUTOMATIC:
                if self._consequence_pipeline is None:
                    if raise_on_invalid:
                        raise PipelineValidationError("consequence pipeline is unavailable")
                    return tr(locale, "consequence_unavailable")
            elif result.resolution is ActionResolution.ROLL:
                proposal = PoolProposal(
                    trait_names=tuple(result.trait_names),
                    aspect_names=tuple(result.aspect_names),
                    flag=result.flag,
                    reserve_spent=0,
                    difficulty=int(result.difficulty),
                    bonus_ids=tuple(result.bonus_ids),
                )
                try:
                    validate_pool(sheet, proposal)
                except MechanicsError as error:
                    return tr(locale, "pool_invalid", error=error)
            return prepared_action
        if result.resolution is ActionResolution.REJECTED:
            reason = redact_secret_leak(
                result.rejection_reason or tr(locale, "action_rejected_safe_reason"),
                self._secret_plot_for_game(game_id),
                replacement=tr(locale, "action_rejected_safe_reason"),
            )
            if replacing_pending is not None:
                self._store.resolve_pending(
                    interaction_id=replacing_pending.interaction_id,
                    player_id=message.author_id,
                    expected_revision=replacing_pending.revision,
                    answer=str(
                        (continuation_context or {}).get("player_answer") or message.content
                    ),
                    closed_by_event_id=message.event_id,
                )
            return tr(locale, "action_rejected", reason=reason)
        if result.resolution is ActionResolution.CLARIFICATION:
            question = redact_secret_leak(
                result.clarification_question or tr(locale, "clarify_action"),
                self._secret_plot_for_game(game_id),
                replacement=tr(locale, "clarify_action"),
            )
            previous_payload = {} if replacing_pending is None else replacing_pending.payload
            original_declaration = str(
                previous_payload.get("original_declaration") or message.content
            ).strip()
            answers = list(previous_payload.get("clarification_answers", []))
            if continuation_context:
                prior_question = continuation_context.get("pending_question")
                player_answer = continuation_context.get("player_answer")
                if prior_question is not None and player_answer is not None:
                    answers.append(
                        {
                            "question": str(prior_question),
                            "answer": str(player_answer),
                        }
                    )
            payload: dict[str, object] = {
                "continuation_type": "action_clarification",
                "original_declaration": original_declaration,
                "clarification_question": question,
                "clarification_answers": answers,
                "source_event_id": str(previous_payload.get("source_event_id") or message.event_id),
                "prompt_source_event_id": message.event_id,
            }
            if replacing_pending is None:
                self._store.put_pending(
                    PendingInteraction(
                        interaction_id=str(uuid.uuid4()),
                        game_id=game_id,
                        player_id=message.author_id,
                        scene_id=str(scene["scene_id"]),
                        kind=PendingKind.CLARIFICATION,
                        prompt=question,
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
                    prompt=question,
                    payload=payload,
                    origin_channel_id=message.channel_id,
                )
            return question
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
                        "source_event_id": message.event_id,
                        "root_source_event_id": root_source_event_id,
                        "source_message_content": str(
                            (continuation_context or {}).get("player_answer") or message.content
                        ),
                        "replacing_pending_interaction_id": (
                            None if replacing_pending is None else replacing_pending.interaction_id
                        ),
                        "replacing_pending_revision": (
                            None if replacing_pending is None else replacing_pending.revision
                        ),
                    },
                    narrator_rights=OutcomeAuthority.GM_AUTOMATIC.value,
                    player_id=message.author_id,
                    prepared_consequence=prepared_consequence,
                )
            except (PipelineValidationError, OutcomePolicyError, ValueError):
                logger.exception(
                    "Automatic consequence failed before a canonical patch for %s",
                    message.event_id,
                )
                if strict_consequence:
                    raise
                return tr(
                    locale,
                    manifest_for(PipelineName.CONSEQUENCE_PLANNING).on_invalid.value,
                )
            except RuntimeError as error:
                if str(error) not in {
                    "scene revision conflict",
                    "actor character revision conflict",
                    "actor location revision conflict",
                    "scene participants changed",
                    "actor is no longer in the patched scene",
                    "prepared consequence actor context changed before commit",
                }:
                    raise
                logger.warning(
                    "automatic_consequence_context_changed event_id=%s",
                    message.event_id,
                )
                if strict_consequence:
                    raise FictionContextChangedError(str(error)) from error
                return tr(locale, "fiction_context_changed_retry")
            committed_automatic = self._store.domain_event_for_causation(
                event_type="scene_patched",
                causation_id=f"automatic:{message.event_id}",
            )
            if committed_automatic is None:
                raise RuntimeError("automatic consequence lost its canonical event")
            return await self._replay_committed_automatic_action(
                message=message,
                game_id=game_id,
                event=committed_automatic,
                replacing_pending=replacing_pending,
                continuation_context=continuation_context,
            )
        proposal = PoolProposal(
            trait_names=tuple(result.trait_names),
            aspect_names=tuple(result.aspect_names),
            flag=result.flag,
            reserve_spent=0,
            difficulty=int(result.difficulty),
            bonus_ids=tuple(result.bonus_ids),
        )
        try:
            # Validate before crediting the turn, then refresh state after activity: crossing an
            # XP interval increments character revision and the new pool must capture that value.
            validate_pool(sheet, proposal)
            self._store.record_activity(
                game_id=game_id,
                occurred_at=message.created_at,
                causation_id=f"activity:{message.event_id}",
            )
            character = self._store.character_for_player(
                game_id=game_id,
                player_id=message.author_id,
            )
            scene = self._store.scene_projection(game_id=game_id, player_id=message.author_id)
            if character is None or scene is None:
                raise ValueError("accepted action lost its actor context")
            sheet = character.sheet
            if replacing_pending is None:
                pending = self._actions.propose_roll(
                    game_id=game_id,
                    player_id=message.author_id,
                    scene_id=str(scene["scene_id"]),
                    proposal=proposal,
                    prompt=tr(locale, "confirm_pool_prompt"),
                    declaration=message.content,
                    source_event_id=message.event_id,
                    origin_channel_id=message.channel_id,
                    root_source_event_id=root_source_event_id,
                )
            else:
                pool = validate_pool(sheet, proposal)
                declaration = message.content.strip()
                player_answer = (continuation_context or {}).get("player_answer")
                if (
                    player_answer is not None
                    and (continuation_context or {}).get("continuation_type")
                    == "action_clarification"
                ):
                    declaration += f"\nClarification answer: {str(player_answer).strip()}"
                pending = PendingInteraction(
                    interaction_id=str(uuid.uuid4()),
                    game_id=game_id,
                    player_id=message.author_id,
                    scene_id=str(scene["scene_id"]),
                    kind=PendingKind.POOL_CONFIRMATION,
                    prompt=tr(locale, "confirm_pool_prompt"),
                    payload={
                        "character_id": character.character_id,
                        "character_revision": character.revision,
                        "scene_revision": int(scene["scene_revision"]),
                        "location_revision": int(scene["location_revision"]),
                        "trait_names": list(proposal.trait_names),
                        "aspect_names": list(proposal.aspect_names),
                        "flag": proposal.flag,
                        "bonus_ids": list(proposal.bonus_ids),
                        "reserve_spent": 0,
                        "difficulty": proposal.difficulty,
                        "validated_difficulty": pool.difficulty,
                        "pool_size": pool.size,
                        "declaration": declaration,
                        "continuation_context": continuation_context or {},
                        # If a crash happens after the atomic replacement, replaying the same
                        # inbox event must show this pool, not confirm it accidentally.
                        "deferred_confirmation_event_id": message.event_id,
                        "prompt_source_event_id": message.event_id,
                        **(
                            {"root_source_event_id": root_source_event_id}
                            if root_source_event_id is not None
                            else {}
                        ),
                    },
                    origin_channel_id=message.channel_id,
                )
                self._store.replace_pending(
                    current_interaction_id=replacing_pending.interaction_id,
                    player_id=message.author_id,
                    expected_revision=replacing_pending.revision,
                    replacement=pending,
                    answer=(None if player_answer is None else str(player_answer)),
                    closed_by_event_id=message.event_id,
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
        normalized = message.content.strip().casefold()
        remainder = self._cancelled_remainder(message.content)
        if remainder is not None:
            return await self._cancel_pending_and_continue(
                message=message,
                pending=pending,
                remainder=remainder,
            )
        if normalized in self._CANCEL_WORDS:
            return self._cancel_pending_interaction(message=message, pending=pending)
        if pending.kind is PendingKind.POOL_CONFIRMATION and message.event_id in {
            pending.payload.get("deferred_confirmation_event_id"),
            pending.payload.get("root_source_event_id"),
        }:
            character = self._store.character_for_player(
                game_id=pending.game_id,
                player_id=message.author_id,
            )
            reserve = 0 if character is None else character.sheet.reserve_current
            return format_pool_confirmation(
                pool_size=int(pending.payload["pool_size"]),
                difficulty=int(pending.payload["difficulty"]),
                reserve=reserve,
                locale=locale,
                sources=tuple(
                    str(value)
                    for value in (
                        *pending.payload["trait_names"],
                        *pending.payload["aspect_names"],
                        *([pending.payload["flag"]] if pending.payload.get("flag") else []),
                    )
                ),
            )
        deferred_action = pending.payload.get("compound_action")
        if pending.kind is PendingKind.CHOICE and deferred_action:
            if normalized in {"да", "yes", "confirm"}:
                return await self._handle_action_declaration(
                    message=replace(message, content=str(deferred_action)),
                    game_id=pending.game_id,
                    replacing_pending=pending,
                    continuation_context={
                        "continuation_type": "compound_condition",
                        "pending_question": pending.prompt,
                        "condition_request": pending.payload.get("condition_request"),
                        "condition_answer": pending.payload.get("condition_answer"),
                        "condition_part_event_id": pending.payload.get("condition_part_event_id"),
                        "player_answer": message.content,
                    },
                )
            if normalized in {"нет", "no"}:
                return self._cancel_pending_interaction(message=message, pending=pending)
            return tr(locale, "compound_condition_reply")
        if (
            pending.kind is PendingKind.CLARIFICATION
            and pending.payload.get("continuation_type") == "action_clarification"
        ):
            return await self._handle_action_declaration(
                message=replace(
                    message,
                    content=str(pending.payload.get("original_declaration") or message.content),
                ),
                game_id=pending.game_id,
                replacing_pending=pending,
                continuation_context={
                    "continuation_type": "action_clarification",
                    "pending_question": pending.prompt,
                    "prior_answers": pending.payload.get("clarification_answers", []),
                    "player_answer": message.content,
                },
            )
        if (
            pending.kind is PendingKind.CLARIFICATION
            and pending.payload.get("continuation_type") == "compound_plan"
        ):
            return await self._handle_compound_play(
                message=replace(
                    message,
                    content=str(pending.payload.get("original_request") or message.content),
                ),
                game_id=pending.game_id,
                replacing_pending=pending,
                clarification_answer=message.content,
            )
        if pending.kind in {PendingKind.CHOICE, PendingKind.CLARIFICATION}:
            # A free-text answer (including "нет") is the answer itself, not a cancel.
            self._store.resolve_pending(
                interaction_id=pending.interaction_id,
                player_id=message.author_id,
                expected_revision=pending.revision,
                answer=message.content,
                closed_by_event_id=message.event_id,
            )
            self._store.record_activity(
                game_id=pending.game_id,
                occurred_at=message.created_at,
                causation_id=f"activity:{message.event_id}",
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
                request = await run_checkpointed_decision(
                    store=self._store,
                    event_id=message.event_id,
                    pipeline_key="roll_confirmation",
                    pipeline=self._roll_confirmation_pipeline,
                    task="Extract this natural-language roll confirmation.",
                    context=assembled,
                    game_id=pending.game_id,
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
        player_owned = roll.narrator_rights.value.startswith("player_")
        if player_owned and (
            game is None or game.narrator_rights_level is not NarratorRightsLevel.DISABLED
        ):
            return mechanical + " " + tr(locale, "player_narrate")
        if game is None:
            raise RuntimeError("committed roll game no longer exists")
        # `disabled` means the system GM narrates every outcome. Preserve the immutable
        # success/failure result, but transfer only authorship to the corresponding GM branch so
        # the canonical consequence is not trapped behind a player narration they cannot submit.
        effective_authority = roll.narrator_rights
        if player_owned:
            effective_authority = (
                OutcomeAuthority.GM_SUCCESS
                if roll.hits >= roll.difficulty
                else OutcomeAuthority.GM_FAILURE
            )
        resolved_pending = self._store.pending_by_id(roll.interaction_id)
        if resolved_pending is None:
            raise RuntimeError("committed roll lost its source interaction")
        declaration = str(resolved_pending.payload.get("declaration", ""))
        consequence_causation_id = f"roll:{roll.roll_id}"
        consequence_committed = self._store.has_scene_patch(consequence_causation_id)
        scene = self._store.scene_projection(game_id=roll.game_id, player_id=message.author_id)
        character = self._store.character_for_player(
            game_id=roll.game_id,
            player_id=message.author_id,
        )
        if consequence_committed:
            committed_event = self._store.domain_event_for_causation(
                event_type="scene_patched",
                causation_id=consequence_causation_id,
            )
            if committed_event is None:
                raise RuntimeError("committed roll patch marker disappeared")
            context_is_current, _ = self._post_effect_fiction_is_current(
                event=committed_event,
                game_id=roll.game_id,
                player_id=message.author_id,
                outcome_causation_id=consequence_causation_id,
            )
            recovery_is_checkpointed = (
                self._store.reserve_recovery_decision(consequence_causation_id) is not None
            )
            if context_is_current or recovery_is_checkpointed:
                await self._consider_reserve_recovery(
                    game_id=roll.game_id,
                    scene=scene
                    or self._store.scene_by_id(
                        game_id=roll.game_id,
                        scene_id=str(resolved_pending.scene_id or ""),
                    )
                    or {},
                    causation_id=consequence_causation_id,
                    outcome_source={"kind": "roll", "declaration": declaration},
                    player_id=message.author_id,
                )
            else:
                logger.warning(
                    "committed_roll_recovery_skipped_stale_context roll_id=%s",
                    roll.roll_id,
                )
            return f"{mechanical}\n\n{tr(locale, 'committed_roll_consequence_saved_prose_skipped')}"
        if not consequence_committed:
            payload = resolved_pending.payload
            source_context_is_current = (
                resolved_pending.scene_id is not None
                and scene is not None
                and str(scene["scene_id"]) == resolved_pending.scene_id
                and int(scene["scene_revision"]) == int(payload.get("scene_revision", -1))
                and int(scene["location_revision"]) == int(payload.get("location_revision", -1))
                and character is not None
                and character.character_id == roll.character_id
                and character.revision == int(payload.get("character_revision", -2)) + 1
            )
            if not source_context_is_current:
                logger.warning(
                    "committed_roll_source_context_changed roll_id=%s",
                    roll.roll_id,
                )
                return f"{mechanical}\n\n{tr(locale, 'committed_roll_consequence_skipped')}"
        if scene is None:
            raise RuntimeError("committed roll actor has no current scene")
        committed_event: Mapping[str, object] | None = None
        narration_context: FictionContextSnapshot | None = None
        if self._consequence_pipeline is not None:
            try:
                await self._ensure_scene_consequence(
                    game_id=roll.game_id,
                    scene=scene,
                    causation_id=consequence_causation_id,
                    outcome_source={
                        "kind": "roll",
                        "roll_id": roll.roll_id,
                        "declaration": declaration,
                        "hits": roll.hits,
                        "difficulty": roll.difficulty,
                        "mechanical_narrator_rights": roll.narrator_rights.value,
                    },
                    narrator_rights=effective_authority.value,
                    player_id=message.author_id,
                )
            except TransientProviderError:
                if not self._has_durable_inbox_event(
                    message.event_id
                ) or not self._store.provider_retry_exhausted(message.event_id):
                    raise
                logger.warning(
                    "committed_roll_consequence_provider_exhausted roll_id=%s",
                    roll.roll_id,
                    exc_info=True,
                )
                return (
                    f"{mechanical}\n\n"
                    f"{tr(locale, 'committed_roll_consequence_provider_unavailable')}"
                )
            except (PipelineValidationError, OutcomePolicyError, ValueError):
                logger.warning(
                    "committed_roll_consequence_invalid roll_id=%s",
                    roll.roll_id,
                    exc_info=True,
                )
                return f"{mechanical}\n\n{tr(locale, 'committed_roll_consequence_skipped')}"
            except RuntimeError as error:
                if str(error) not in {
                    "scene revision conflict",
                    "actor character revision conflict",
                    "actor location revision conflict",
                    "scene participants changed",
                    "actor is no longer in the patched scene",
                    "prepared consequence actor context changed before commit",
                }:
                    raise
                logger.warning(
                    "committed_roll_consequence_context_changed roll_id=%s",
                    roll.roll_id,
                )
                return f"{mechanical}\n\n{tr(locale, 'committed_roll_consequence_skipped')}"
            committed_event = self._store.domain_event_for_causation(
                event_type="scene_patched",
                causation_id=consequence_causation_id,
            )
            if committed_event is None:
                raise RuntimeError("committed roll consequence lost its canonical event")
            context_is_current, narration_context = self._post_effect_fiction_is_current(
                event=committed_event,
                game_id=roll.game_id,
                player_id=message.author_id,
                outcome_causation_id=consequence_causation_id,
            )
            if not context_is_current or narration_context is None:
                logger.warning(
                    "committed_roll_post_effect_context_changed roll_id=%s",
                    roll.roll_id,
                )
                return (
                    f"{mechanical}\n\n"
                    f"{tr(locale, 'committed_roll_consequence_saved_prose_skipped')}"
                )
            scene = self._store.scene_projection(game_id=roll.game_id, player_id=message.author_id)
        if self._narrative_pipeline is None or game.narrative_channel_id is None:
            return mechanical
        character = self._store.character_for_player(
            game_id=roll.game_id,
            player_id=message.author_id,
        )
        if scene is None or character is None:
            raise RuntimeError("committed roll actor context disappeared")
        if narration_context is None:
            narration_context = FictionContextSnapshot.capture(
                game_id=roll.game_id,
                player_id=message.author_id,
                character=character,
                scene=scene,
            )
        actor_projection = self._actor_character_projection(
            game_id=roll.game_id,
            player_id=message.author_id,
        )
        if actor_projection is None:
            raise RuntimeError("committed roll actor character no longer exists")
        assembled = self._assemble_context(
            manifest_for(PipelineName.OUTCOME_NARRATION),
            {
                "session_brief": {
                    **self._narrative_session_brief(game),
                    "participants_here": scene["participants"],
                },
                "actor_character": actor_projection,
                "current_scene": scene,
                "roll_result": {
                    "declaration": declaration,
                    "dice": roll.dice,
                    "hits": roll.hits,
                    "difficulty": roll.difficulty,
                    "narrator_rights": effective_authority.value,
                    "mechanical_narrator_rights": roll.narrator_rights.value,
                    "narrator_rights_level": game.narrator_rights_level.value,
                },
            },
            game_id=roll.game_id,
            channel_id=message.channel_id,
            player_id=message.author_id,
        )
        try:
            narrative = await run_checkpointed_decision(
                store=self._store,
                event_id=message.event_id,
                pipeline_key=f"outcome_narration:roll:{roll.roll_id}",
                pipeline=self._narrative_pipeline,
                task="Narrate the resolved action outcome without changing its mechanics.",
                context=assembled,
                game_id=roll.game_id,
                input_fingerprint=decision_input_fingerprint(
                    {
                        "stage": "outcome_narration:roll",
                        "roll_id": roll.roll_id,
                        "declaration": declaration,
                        **narration_context.as_mapping(),
                    }
                ),
            )
            if committed_event is not None:
                still_current, _ = self._post_effect_fiction_is_current(
                    event=committed_event,
                    game_id=roll.game_id,
                    player_id=message.author_id,
                    outcome_causation_id=consequence_causation_id,
                )
            else:
                still_current = narration_context.matches(
                    character=self._store.character_for_player(
                        game_id=roll.game_id,
                        player_id=message.author_id,
                    ),
                    scene=self._store.scene_projection(
                        game_id=roll.game_id,
                        player_id=message.author_id,
                    ),
                )
            narrative_text = (
                narrative.narrative if still_current else tr(locale, "narrative_fallback")
            )
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
        prepared_consequence: PreparedSceneConsequence | None = None,
    ) -> None:
        if not self._store.has_scene_patch(causation_id):
            prepared = prepared_consequence or await self._prepare_scene_consequence(
                game_id=game_id,
                scene=scene,
                causation_id=causation_id,
                outcome_source=outcome_source,
                narrator_rights=narrator_rights,
                player_id=player_id,
            )
            if prepared is not None:
                if (
                    prepared.game_id != game_id
                    or prepared.scene_id != str(scene["scene_id"])
                    or prepared.causation_id != causation_id
                    or prepared.player_id != player_id
                ):
                    raise ValueError("prepared consequence belongs to another action")
                current_scene = self._store.scene_by_id(
                    game_id=game_id,
                    scene_id=prepared.scene_id,
                )
                character = self._store.character_for_player(
                    game_id=game_id,
                    player_id=prepared.player_id,
                )
                if (
                    current_scene is None
                    or character is None
                    or character.character_id != prepared.actor_character_id
                ):
                    raise RuntimeError("prepared consequence actor context changed before commit")
                source_fiction = {
                    "game_id": game_id,
                    "player_id": str(player_id),
                    "character_id": prepared.actor_character_id,
                    "character_revision": prepared.expected_actor_revision,
                    "scene_id": prepared.scene_id,
                    "scene_revision": prepared.expected_scene_revision,
                    "location_revision": prepared.expected_actor_location_revision,
                    "participants": list(prepared.expected_scene_participants),
                }
                outcome_kind = outcome_source.get("kind")
                effect_metadata: dict[str, object] | None = None
                if outcome_kind == "automatic_action":
                    game = self._store.game_state(game_id)
                    effect_metadata = {
                        "kind": "automatic_action",
                        "declaration": str(outcome_source.get("declaration") or ""),
                        "evidence": list(outcome_source.get("evidence") or ()),
                        "source_event_id": str(outcome_source.get("source_event_id") or ""),
                        "root_source_event_id": outcome_source.get("root_source_event_id"),
                        "source_message_content": str(
                            outcome_source.get("source_message_content") or ""
                        ),
                        "replacing_pending_interaction_id": outcome_source.get(
                            "replacing_pending_interaction_id"
                        ),
                        "replacing_pending_revision": outcome_source.get(
                            "replacing_pending_revision"
                        ),
                        "source_fiction": source_fiction,
                        "response_mode": (
                            "delivery"
                            if (
                                game is not None
                                and game.narrative_channel_id is not None
                                and self._narrative_pipeline is not None
                            )
                            else "inline"
                        ),
                        "original_target_channel_id": (
                            None if game is None else game.narrative_channel_id
                        ),
                    }
                elif outcome_kind == "player_narration":
                    raw_source_fiction = outcome_source.get("source_fiction")
                    if not isinstance(raw_source_fiction, Mapping):
                        raise ValueError("player narration source fiction is missing")
                    canonical_source = FictionContextSnapshot.from_mapping(raw_source_fiction)
                    if canonical_source.as_mapping() != source_fiction:
                        raise ValueError("player narration source fiction mismatch")
                    effect_metadata = {
                        "kind": "player_narration",
                        "source_event_id": str(outcome_source.get("source_event_id") or ""),
                        "pending_interaction_id": str(
                            outcome_source.get("pending_interaction_id") or ""
                        ),
                        "pending_revision": int(outcome_source.get("pending_revision", -1)),
                        "source_interaction_id": str(
                            outcome_source.get("source_interaction_id") or ""
                        ),
                        "source_interaction_revision": int(
                            outcome_source.get("source_interaction_revision", -1)
                        ),
                        "roll_id": str(outcome_source.get("roll_id") or ""),
                        "player_id": str(outcome_source.get("player_id") or ""),
                        "approved_narration": str(outcome_source.get("text") or ""),
                        "submitted_narration": str(outcome_source.get("submitted_narration") or ""),
                        "source_fiction": canonical_source.as_mapping(),
                        "response_mode": str(outcome_source.get("response_mode") or ""),
                        "original_target_channel_id": outcome_source.get(
                            "original_target_channel_id"
                        ),
                    }
                elif outcome_kind == "roll":
                    effect_metadata = {
                        "kind": "roll",
                        "roll_id": str(outcome_source.get("roll_id") or ""),
                        "source_fiction": source_fiction,
                    }
                self._store.apply_outcome_patch(
                    game_id=game_id,
                    scene_id=prepared.scene_id,
                    expected_scene_revision=prepared.expected_scene_revision,
                    actor_character_id=prepared.actor_character_id,
                    expected_actor_revision=prepared.expected_actor_revision,
                    expected_actor_location_revision=(prepared.expected_actor_location_revision),
                    expected_scene_participants=prepared.expected_scene_participants,
                    causation_id=causation_id,
                    patch=prepared.patch,
                    secret_reveals=prepared.secret_reveals,
                    effect_metadata=effect_metadata,
                )
        await self._consider_reserve_recovery(
            game_id=game_id,
            scene=scene,
            causation_id=causation_id,
            outcome_source=outcome_source,
            player_id=player_id,
        )

    async def _prepare_scene_consequence(
        self,
        *,
        game_id: str,
        scene: dict[str, object],
        causation_id: str,
        outcome_source: dict[str, object],
        narrator_rights: str,
        player_id: str | None,
    ) -> PreparedSceneConsequence | None:
        """Run consequence generation and policy checks without changing canonical state."""

        if self._store.has_scene_patch(causation_id):
            return None
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
        hidden_secret = self._secret_plot_for_game(game_id)
        secret_catalog = {fact.secret_id: fact.text for fact in secret_fact_catalog(hidden_secret)}
        raw_allowed_reveals = outcome_source.get("allowed_secret_ids", ())
        allowed_reveal_ids = (
            {
                str(secret_id)
                for secret_id in raw_allowed_reveals
                if str(secret_id) in secret_catalog
            }
            if isinstance(raw_allowed_reveals, (list, tuple, set, frozenset))
            else set()
        )
        gm_world_context = self._world_context_projection(
            game_id=game_id,
            scene=scene,
            include_secret=True,
        )
        gm_world_context["secret_catalog"] = [
            {"secret_id": secret_id, "text": secret_catalog[secret_id]}
            for secret_id in sorted(allowed_reveal_ids)
        ]
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
                "gm_world_context": gm_world_context,
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

        def validate_plan(plan) -> PreparedSceneConsequence:
            patch = plan.to_domain()
            reveal_ids = tuple(plan.reveal_secret_ids)
            if reveal_ids and authority not in {
                OutcomeAuthority.PLAYER_SUCCESS,
                OutcomeAuthority.GM_SUCCESS,
                OutcomeAuthority.GM_AUTOMATIC,
            }:
                raise ValueError("a failed outcome cannot reveal a hidden fact")
            unknown_reveals = set(reveal_ids) - secret_catalog.keys()
            if unknown_reveals:
                raise ValueError("secret reveal id is not an unrevealed supplied fact")
            unauthorized_reveals = set(reveal_ids) - allowed_reveal_ids
            if unauthorized_reveals:
                raise ValueError("secret reveal id is not authorized by the resolved outcome")
            secret_reveals = {secret_id: secret_catalog[secret_id] for secret_id in reveal_ids}
            if secret_reveals:
                patch = replace(
                    patch,
                    add_facts=tuple(dict.fromkeys((*patch.add_facts, *secret_reveals.values()))),
                )
            ensure_no_secret_fragments(
                patch,
                hidden_secret_plot(hidden_secret, set(secret_reveals)),
            )
            enforce_narrator_rights_policy(
                patch,
                authority=authority,
                level=game.narrator_rights_level,
            )
            return PreparedSceneConsequence(
                game_id=game_id,
                scene_id=str(scene["scene_id"]),
                expected_scene_revision=int(scene["scene_revision"]),
                actor_character_id=character.character_id,
                expected_actor_revision=character.revision,
                expected_actor_location_revision=int(scene["location_revision"]),
                expected_scene_participants=tuple(
                    str(participant) for participant in scene["participants"]
                ),
                causation_id=causation_id,
                patch=patch,
                secret_reveals=secret_reveals,
                scene=scene,
                outcome_source=outcome_source,
                player_id=player_id,
            )

        output_type = self._consequence_pipeline.output_type
        output_type_name = decision_output_type_name(output_type)
        input_fingerprint = decision_input_fingerprint(
            {
                "game_id": game_id,
                "scene_id": str(scene["scene_id"]),
                "scene_revision": int(scene["scene_revision"]),
                "location_revision": int(scene["location_revision"]),
                "scene_participants": list(scene["participants"]),
                "actor_character_id": character.character_id,
                "actor_revision": character.revision,
                "outcome_source": outcome_source,
                "narrator_rights": narrator_rights,
                "player_id": player_id,
            }
        )
        checkpoint = self._store.decision_checkpoint(
            event_id=causation_id,
            pipeline_key="consequence_planning",
            output_type=output_type_name,
            game_id=game_id,
            input_fingerprint=input_fingerprint,
        )
        if checkpoint is not None:
            return validate_plan(output_type.model_validate(checkpoint))

        revision_error: ValueError | None = None
        for attempt in range(2):
            task = "Produce the minimal persistent OutcomePatch for this outcome."
            if revision_error is not None:
                task += (
                    "\nThe previous proposal was rejected by deterministic validation: "
                    f"{revision_error}. Revise it using only the exact supplied targets."
                )
            plan = await self._consequence_pipeline.run(task=task, context=assembled)
            try:
                validate_plan(plan)
                canonical = self._store.checkpoint_decision(
                    event_id=causation_id,
                    pipeline_key="consequence_planning",
                    output_type=output_type_name,
                    payload=plan.model_dump(mode="json"),
                    game_id=game_id,
                    input_fingerprint=input_fingerprint,
                )
                return validate_plan(output_type.model_validate(canonical))
            except (OutcomePolicyError, ValueError) as error:
                revision_error = error
                if attempt == 1:
                    raise
        raise AssertionError("consequence planning loop did not return")

    async def _consider_reserve_recovery(
        self,
        *,
        game_id: str,
        scene: dict[str, object],
        causation_id: str,
        outcome_source: dict[str, object],
        player_id: str | None,
    ) -> None:
        game = self._store.game_state(game_id)
        if game is None:
            return
        try:
            canonical = self._store.reserve_recovery_decision(causation_id)
            if canonical is None:
                if self._reserve_recovery_pipeline is None:
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
                        "actor_character": (
                            None
                            if player_id is None
                            else self._actor_character_projection(
                                game_id=game_id,
                                player_id=player_id,
                            )
                        ),
                        "characters": self._store.reserve_projection(game_id),
                    },
                    game_id=game_id,
                    player_id=player_id,
                )
                decision = await self._reserve_recovery_pipeline.run(
                    task="Decide whether this resolved outcome earns reserve recovery.",
                    context=assembled,
                )
                canonical = self._store.checkpoint_reserve_recovery_decision(
                    game_id=game_id,
                    causation_id=causation_id,
                    safe_rest_completed=(
                        decision.safe_rest_completed and game.reserve_recovery_mode.allows_safe_rest
                    ),
                    safe_rest_reason=decision.safe_rest_reason,
                    awards=(
                        tuple((award.player_id, award.reason) for award in decision.awards)
                        if game.reserve_recovery_mode.allows_roleplay_award
                        else ()
                    ),
                )
            if bool(canonical["safe_rest_completed"]):
                self._games.restore_reserve_for_safe_rest(
                    game_id=game_id,
                    reason=str(canonical["safe_rest_reason"] or "completed safe rest"),
                    causation_id=f"reserve-rest:{causation_id}",
                )
            for award in canonical["awards"]:
                award_payload = dict(award)
                award_player_id = str(award_payload["player_id"])
                self._games.award_reserve_die(
                    game_id=game_id,
                    player_id=award_player_id,
                    reason=str(award_payload["reason"]),
                    causation_id=f"reserve-award:{causation_id}:{award_player_id}",
                )
        except Exception:
            logger.exception("Reserve-recovery adjudication failed for %s", causation_id)
