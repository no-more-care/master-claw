"""Prepare a canonical action interpretation without applying any game mechanics."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from masterclaw.app.decision_checkpoints import (
    DecisionPipeline,
    decision_input_fingerprint,
    run_checkpointed_decision,
)
from masterclaw.app.fiction_context import (
    FictionContextChangedError,
    FictionContextSnapshot,
    actor_character_projection,
)
from masterclaw.context.assembler import AssembledContext
from masterclaw.context.manifests import ContextManifest, PipelineName, manifest_for
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.models import IncomingMessage
from masterclaw.domain.state import PendingInteraction
from masterclaw.pipelines.action import ActionInterpretation, ActionResolution
from masterclaw.pipelines.base import PipelineValidationError
from masterclaw.storage.sqlite import SQLiteStore

logger = logging.getLogger(__name__)


class ActionContextAssembler(Protocol):
    """Existing shared enrichment/assembly boundary, not a handler or service locator."""

    def __call__(
        self,
        manifest: ContextManifest,
        projections: dict[str, object],
        *,
        game_id: str,
        channel_id: str,
        player_id: str,
    ) -> AssembledContext: ...


class MissingActionContext(StrEnum):
    CHARACTER = "character_game_required"
    SCENE = "character_scene_required"


@dataclass(frozen=True, slots=True)
class ActionPreparationSnapshot:
    context: FictionContextSnapshot
    character: CharacterState
    scene_json: str

    @property
    def scene(self) -> dict[str, object]:
        """Return a detached copy; callers cannot mutate the captured scene."""
        return json.loads(self.scene_json)


@dataclass(frozen=True, slots=True)
class PreparedAction:
    interpretation: ActionInterpretation
    snapshot: ActionPreparationSnapshot

    @property
    def context(self) -> FictionContextSnapshot:
        return self.snapshot.context


class ActionCapabilityReference(StrEnum):
    PROCEED = "proceed"
    BLOCKED = "blocked"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class ActionCapabilitySnapshot:
    declaration: str
    fiction: ActionPreparationSnapshot
    participants_json: str
    source_ids: tuple[str, ...] = ()


class ActionCapabilityObserver(Protocol):
    @property
    def enabled(self) -> bool: ...

    async def observe(
        self, snapshot: ActionCapabilitySnapshot, *, reference: ActionCapabilityReference
    ) -> object: ...


class _FreshInterpretation:
    """Runtime invocation marker; durable output type and payload remain the baseline's."""

    def __init__(self, pipeline: DecisionPipeline[ActionInterpretation]) -> None:
        self.pipeline = pipeline
        self.executed = False

    @property
    def output_type(self) -> type[ActionInterpretation]:
        return self.pipeline.output_type

    async def run(self, *, task: str, context: AssembledContext) -> ActionInterpretation:
        self.executed = True
        return await self.pipeline.run(task=task, context=context)


class ActionPreparationService:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        assemble_context: ActionContextAssembler,
        pipeline: DecisionPipeline[ActionInterpretation] | None = None,
        observer: ActionCapabilityObserver | None = None,
    ) -> None:
        self._store = store
        self._assemble_context = assemble_context
        self._pipeline = pipeline
        self._observer = observer

    @property
    def available(self) -> bool:
        return self._pipeline is not None

    async def prepare(
        self,
        *,
        message: IncomingMessage,
        game_id: str,
        locale: str,
        replacing_pending: PendingInteraction | None = None,
        continuation_context: dict[str, object] | None = None,
        prepared_result: PreparedAction | None = None,
    ) -> PreparedAction | MissingActionContext:
        fresh = False
        if self._pipeline is None and prepared_result is None:
            raise PipelineValidationError("action interpretation pipeline is unavailable")
        character = self._store.character_for_player(game_id=game_id, player_id=message.author_id)
        scene = self._store.scene_projection(game_id=game_id, player_id=message.author_id)
        if character is None:
            return MissingActionContext.CHARACTER
        if scene is None:
            return MissingActionContext.SCENE
        if prepared_result is not None:
            if not prepared_result.context.matches(character=character, scene=scene):
                raise FictionContextChangedError("prepared action context changed before commit")
            result = prepared_result.interpretation
            context_snapshot = prepared_result.context
        else:
            context_snapshot = FictionContextSnapshot.capture(
                game_id=game_id, player_id=message.author_id, character=character, scene=scene
            )
            # Preserve the legacy projection read separately from the captured guard revision.
            projected_character = self._store.character_for_player(
                game_id=game_id, player_id=message.author_id
            )
            assert projected_character is not None
            assembled = self._assemble_context(
                manifest_for(PipelineName.ACTION_INTERPRETATION),
                {
                    "session_brief": {
                        "game_id": game_id,
                        "locale": locale,
                        "participants_here": scene["participants"],
                    },
                    "actor_character": actor_character_projection(
                        projected_character, player_id=message.author_id
                    ),
                    "current_scene": scene,
                },
                game_id=game_id,
                channel_id=message.channel_id,
                player_id=message.author_id,
            )
            task = f"Interpret the declaration:\n{message.content}"
            if continuation_context:
                task += (
                    "\nThe declaration continues a typed pending interaction. Use the following "
                    "question/answer context as data, preserve the original intent, and do not "
                    "reinterpret it as a separate action:\n"
                    + json.dumps(continuation_context, ensure_ascii=False, sort_keys=True)
                )
            assert self._pipeline is not None
            invocation = _FreshInterpretation(self._pipeline)
            result = await run_checkpointed_decision(
                store=self._store,
                event_id=message.event_id,
                pipeline_key="action_interpretation",
                pipeline=invocation,
                task=task,
                context=assembled,
                game_id=game_id,
                input_fingerprint=decision_input_fingerprint(
                    {
                        "stage": "action_interpretation",
                        "message": message.content,
                        "continuation_context": continuation_context or {},
                        "replacing_pending_id": (
                            None if replacing_pending is None else replacing_pending.interaction_id
                        ),
                        "replacing_pending_revision": (
                            None if replacing_pending is None else replacing_pending.revision
                        ),
                        **context_snapshot.as_mapping(),
                    }
                ),
            )
            current_character = self._store.character_for_player(
                game_id=game_id, player_id=message.author_id
            )
            current_scene = self._store.scene_projection(
                game_id=game_id, player_id=message.author_id
            )
            if not context_snapshot.matches(character=current_character, scene=current_scene):
                raise FictionContextChangedError(
                    "action interpretation context changed before commit"
                )
            assert current_character is not None and current_scene is not None
            character, scene = current_character, current_scene
            fresh = invocation.executed
        prepared = PreparedAction(
            interpretation=result,
            snapshot=ActionPreparationSnapshot(
                context=context_snapshot,
                character=character,
                scene_json=json.dumps(scene, ensure_ascii=False, sort_keys=True),
            ),
        )
        if fresh and self._observer is not None and self._observer.enabled:
            observer_executed = False
            try:
                participants = [
                    {"player_id": row["player_id"], "name": row["name"], "role": "player_character"}
                    for row in self._store.character_roster(game_id)
                    if row["player_id"] in context_snapshot.participants
                ]
                reference = (
                    ActionCapabilityReference.PROCEED
                    if result.resolution in {ActionResolution.ROLL, ActionResolution.AUTOMATIC}
                    else ActionCapabilityReference.BLOCKED
                    if result.resolution is ActionResolution.REJECTED
                    else ActionCapabilityReference.UNCERTAIN
                )
                observation_snapshot = ActionCapabilitySnapshot(
                    declaration=message.content,
                    fiction=prepared.snapshot,
                    participants_json=json.dumps(participants, ensure_ascii=False),
                    source_ids=tuple(
                        value
                        for value in (
                            message.event_id,
                            message.channel_id,
                            message.author_id,
                            message.guild_id,
                            message.parent_channel_id,
                            message.reply_to_event_id,
                            message.reply_to_author_id,
                            message.routing_game_id,
                            message.routing_scene_id,
                            *(attachment.attachment_id for attachment in message.attachments),
                        )
                        if value is not None
                    ),
                )
                observer_executed = True
                await self._observer.observe(observation_snapshot, reference=reference)
            except Exception:
                logger.warning("action_capability_observation_failed category=internal")
            # Shadow adds an await; protect exactly the same canonical revisions afterwards.
            if observer_executed and not context_snapshot.matches(
                character=self._store.character_for_player(
                    game_id=game_id, player_id=message.author_id
                ),
                scene=self._store.scene_projection(game_id=game_id, player_id=message.author_id),
            ):
                raise FictionContextChangedError(
                    "action interpretation context changed before commit"
                )
        return prepared
