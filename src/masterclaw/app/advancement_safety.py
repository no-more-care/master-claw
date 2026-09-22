"""Application safety seam; no model/provider types cross this boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.state import GameState
from masterclaw.storage.sqlite import SQLiteStore


class SafetyVerdict(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    UNCERTAIN = "uncertain"


class SafetyReason(StrEnum):
    LEGACY_ALLOWED = "legacy_allowed"
    LEGACY_DENIED = "legacy_denied"
    INSUFFICIENT_SIGNAL = "insufficient_signal"
    SAFE_WITH_DOWNTIME = "safe_with_downtime"
    UNSAFE = "unsafe"
    NO_DOWNTIME = "no_downtime"
    NO_LEARNING_OPPORTUNITY = "no_learning_opportunity"


@dataclass(frozen=True, slots=True)
class AdvancementSafetyAssessment:
    verdict: SafetyVerdict
    reason: SafetyReason
    display_detail: str
    evidence: tuple[str, ...] = ()
    # Runtime-only metadata; never part of the legacy durable checkpoint payload.
    replayed: bool = field(default=False, compare=False)


@dataclass(frozen=True, slots=True)
class AdvancementSafetySnapshot:
    """Detached immutable inputs, including exactly the manifest-bounded history.

    Canonical JSON strings prevent mutation through nested mappings/lists. Consumers
    decode private copies; they must not reread live game state during assessment.
    """

    game_id: str
    player_id: str
    scene_id: str
    scene_revision: int
    projections_json: str
    domain_events_json: str
    chat_messages_json: str
    legacy_identity_json: str


class AdvancementSafetyDecider(Protocol):
    async def assess(
        self,
        snapshot: AdvancementSafetySnapshot,
        *,
        checkpoint_event_id: str | None = None,
    ) -> AdvancementSafetyAssessment: ...


def _canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def capture_advancement_safety_snapshot(
    *,
    store: SQLiteStore,
    game: GameState,
    player_id: str,
    character: CharacterState,
    scene: dict[str, object],
    request: dict[str, object],
) -> AdvancementSafetySnapshot:
    """Capture once after the coordinator's existing eligibility checks."""
    manifest = manifest_for(PipelineName.ADVANCEMENT_SAFETY)
    scene_id = str(scene["scene_id"])
    scene_revision = int(scene["scene_revision"])
    return AdvancementSafetySnapshot(
        game_id=game.game_id,
        player_id=player_id,
        scene_id=scene_id,
        scene_revision=scene_revision,
        projections_json=_canonical_json(
            {
                "session_brief": {"game_id": game.game_id, "locale": game.locale},
                "current_scene": scene,
                "actor_character": {
                    "player_id": player_id,
                    "name": character.sheet.name,
                    "available_xp": character.experience_available,
                    "traits": [
                        {"name": trait.name, "level": trait.level}
                        for trait in character.sheet.traits
                    ],
                },
                "advancement_request": request,
            }
        ),
        domain_events_json=_canonical_json(
            store.recent_domain_events(game_id=game.game_id, limit=manifest.recent_domain_events)
        ),
        chat_messages_json=_canonical_json(
            store.recent_chat_messages(
                game_id=game.game_id, player_id=player_id, limit=manifest.recent_chat_messages
            )
        ),
        legacy_identity_json=_canonical_json(
            {
                "game_id": game.game_id,
                "player_id": player_id,
                "scene_id": scene_id,
                "scene_revision": scene_revision,
                "character_id": character.character_id,
                "traits": [
                    {"name": trait.name, "level": trait.level, "aspects": list(trait.aspects)}
                    for trait in character.sheet.traits
                ],
                "request": request,
            }
        ),
    )


def legacy_v1_fingerprint_projection(snapshot: AdvancementSafetySnapshot) -> dict[str, object]:
    """Exact pre-seam checkpoint identity, intentionally NOT full semantic identity.

    TODO(v2): include history/full projections with an explicit deployment/migration
    policy. Changing this subset now would reject existing in-flight checkpoints.
    """
    return json.loads(snapshot.legacy_identity_json)
