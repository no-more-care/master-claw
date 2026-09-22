"""Narrator-rights assessment and publication text are distinct application ports."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from masterclaw.app.context_inputs import ContextInputCapture, ContextInputSnapshot
from masterclaw.app.fiction_context import FictionContextSnapshot
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.actions import RollRecord
from masterclaw.domain.models import IncomingMessage
from masterclaw.domain.state import PendingInteraction


class NarrationVerdict(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    UNCERTAIN = "uncertain"


class NarrationReason(StrEnum):
    LEGACY_ACCEPTED = "legacy_accepted"
    LEGACY_REJECTED = "legacy_rejected"
    UNDETERMINED = "undetermined"


@dataclass(frozen=True, slots=True)
class NarrationAssessment:
    verdict: NarrationVerdict
    reason: NarrationReason
    replayed: bool = field(default=False, compare=False)


@dataclass(frozen=True, slots=True)
class NarrationText:
    publication_text: str | None = None
    feedback_text: str | None = None

    def __post_init__(self) -> None:
        if (self.publication_text is None) == (self.feedback_text is None):
            raise ValueError("narration text requires either publication or feedback")


@dataclass(frozen=True, slots=True)
class NarrationReviewSnapshot:
    fiction: FictionContextSnapshot
    inputs: ContextInputSnapshot
    locale: str
    submitted_text: str
    roll_id: str
    hits: int
    difficulty: int
    narrator_rights: str
    narrator_rights_level: str
    pending_interaction_id: str
    pending_revision: int
    source_interaction_id: str
    source_interaction_revision: int
    source_scene_revision: int
    source_location_revision: int
    source_actor_revision: int


def legacy_v1_fingerprint_projection(snapshot: NarrationReviewSnapshot) -> dict[str, object]:
    # Compatibility boundary: v1 omits enriched projections/history, rights settings,
    # roll values and prompt/declaration. A full-snapshot hash needs an explicit migration.
    return {
        "stage": "player_narration_review",
        "submitted_narration": snapshot.submitted_text,
        "pending_interaction_id": snapshot.pending_interaction_id,
        "pending_revision": snapshot.pending_revision,
        "source_interaction_id": snapshot.source_interaction_id,
        "source_interaction_revision": snapshot.source_interaction_revision,
        "source_scene_revision": snapshot.source_scene_revision,
        "source_location_revision": snapshot.source_location_revision,
        "source_actor_revision": snapshot.source_actor_revision,
        "roll_id": snapshot.roll_id,
        **snapshot.fiction.as_mapping(),
    }


def capture_narration_review_snapshot(
    *,
    capture_context: ContextInputCapture,
    message: IncomingMessage,
    pending: PendingInteraction,
    source_pending: PendingInteraction,
    fiction: FictionContextSnapshot,
    roll: RollRecord,
    rights_level: str,
    locale: str,
    session_brief: dict[str, object],
    scene: dict[str, object],
    actor_projection: dict[str, object] | None,
) -> NarrationReviewSnapshot:
    inputs = capture_context(
        manifest_for(PipelineName.PLAYER_NARRATION_REVIEW),
        {
            "session_brief": session_brief,
            "current_scene": scene,
            "actor_character": actor_projection,
            "roll_result": {
                "hits": roll.hits,
                "difficulty": roll.difficulty,
                "narrator_rights": roll.narrator_rights.value,
                "narrator_rights_level": rights_level,
                "original_declaration": str(source_pending.payload.get("declaration", "")),
                "pending_prompt": pending.prompt,
            },
            "submitted_narration": message.content,
        },
        game_id=pending.game_id,
        channel_id=message.channel_id,
        player_id=message.author_id,
    )
    return NarrationReviewSnapshot(
        fiction=fiction,
        inputs=inputs,
        locale=locale,
        submitted_text=message.content,
        roll_id=roll.roll_id,
        hits=roll.hits,
        difficulty=roll.difficulty,
        narrator_rights=roll.narrator_rights.value,
        narrator_rights_level=rights_level,
        pending_interaction_id=pending.interaction_id,
        pending_revision=pending.revision,
        source_interaction_id=source_pending.interaction_id,
        source_interaction_revision=source_pending.revision,
        source_scene_revision=int(source_pending.payload["scene_revision"]),
        source_location_revision=int(source_pending.payload["location_revision"]),
        source_actor_revision=int(source_pending.payload["character_revision"]),
    )


class NarrationRightsDecider(Protocol):
    async def assess(
        self,
        snapshot: NarrationReviewSnapshot,
        checkpoint_event_id: str,
    ) -> NarrationAssessment: ...


class NarrationTextPort(Protocol):
    async def materialize(
        self,
        snapshot: NarrationReviewSnapshot,
        assessment: NarrationAssessment,
        checkpoint_event_id: str,
    ) -> NarrationText: ...
