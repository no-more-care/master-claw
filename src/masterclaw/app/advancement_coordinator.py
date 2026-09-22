from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from masterclaw.app.decision_checkpoints import (
    decision_input_fingerprint,
    decision_output_type_name,
)
from masterclaw.app.progression_service import ProgressionService
from masterclaw.context.assembler import ContextAssembler, ContextHistory
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.progression import AdvancementPermit
from masterclaw.pipelines.advancement import AdvancementSafetyDecision
from masterclaw.pipelines.base import BoundedJsonPipeline
from masterclaw.storage.sqlite import SQLiteStore
from masterclaw.telemetry import traced_stage


class AdvancementAuthorizationCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: AdvancementSafetyDecision
    scene_id: str
    scene_revision: int


class AdvancementCoordinator:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        context: ContextAssembler,
        safety_pipeline: BoundedJsonPipeline[AdvancementSafetyDecision],
    ) -> None:
        self._store = store
        self._context = context
        self._pipeline = safety_pipeline
        self._progression = ProgressionService(store)

    async def raise_trait(
        self,
        *,
        game_id: str,
        player_id: str,
        trait_name: str,
        new_aspect: str,
        causation_id: str | None = None,
        permit: AdvancementPermit | None = None,
        checkpoint_event_id: str | None = None,
    ) -> CharacterState:
        if causation_id is not None:
            replayed = self._store.character_for_advancement_causation(
                causation_id=causation_id,
                game_id=game_id,
                player_id=player_id,
            )
            if replayed is not None:
                return replayed
        if permit is None:
            permit = await self._authorize(
                game_id=game_id,
                player_id=player_id,
                request={"kind": "raise", "trait": trait_name, "new_aspect": new_aspect},
                checkpoint_event_id=checkpoint_event_id,
            )
        return self._progression.raise_character_trait(
            game_id=game_id,
            player_id=player_id,
            trait_name=trait_name,
            new_aspect=new_aspect,
            permit=permit,
            causation_id=causation_id,
        )

    async def learn_trait(
        self,
        *,
        game_id: str,
        player_id: str,
        trait_name: str,
        aspects: tuple[str, str],
        justification: str,
        causation_id: str | None = None,
        permit: AdvancementPermit | None = None,
        checkpoint_event_id: str | None = None,
    ) -> CharacterState:
        if causation_id is not None:
            replayed = self._store.character_for_advancement_causation(
                causation_id=causation_id,
                game_id=game_id,
                player_id=player_id,
            )
            if replayed is not None:
                return replayed
        if permit is None:
            permit = await self._authorize(
                game_id=game_id,
                player_id=player_id,
                request={
                    "kind": "learn",
                    "trait": trait_name,
                    "aspects": aspects,
                    "justification": justification,
                },
                checkpoint_event_id=checkpoint_event_id,
            )
        return self._progression.learn_character_trait(
            game_id=game_id,
            player_id=player_id,
            trait_name=trait_name,
            aspects=aspects,
            justification=justification,
            permit=permit,
            causation_id=causation_id,
        )

    async def authorize_request(
        self,
        *,
        game_id: str,
        player_id: str,
        request: dict[str, object],
        checkpoint_event_id: str | None = None,
    ) -> AdvancementPermit:
        """Authorize a request without applying it, for compound-request preflight."""

        return await self._authorize(
            game_id=game_id,
            player_id=player_id,
            request=request,
            checkpoint_event_id=checkpoint_event_id,
        )

    @traced_stage("domain.advancement_gate", component="advancement_coordinator")
    async def _authorize(
        self,
        *,
        game_id: str,
        player_id: str,
        request: dict[str, object],
        checkpoint_event_id: str | None = None,
    ) -> AdvancementPermit:
        game = self._store.game_state(game_id)
        if game is None or not game.progression_enabled:
            raise ValueError("progression is disabled for this session")
        character = self._store.character_for_player(game_id=game_id, player_id=player_id)
        scene = self._store.scene_projection(game_id=game_id, player_id=player_id)
        if character is None or scene is None:
            raise ValueError("character or current scene is missing")
        manifest = manifest_for(PipelineName.ADVANCEMENT_SAFETY)
        assembled = self._context.assemble(
            manifest,
            {
                "session_brief": {"game_id": game_id, "locale": game.locale},
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
            },
            history=ContextHistory(
                self._store.recent_domain_events(
                    game_id=game_id,
                    limit=manifest.recent_domain_events,
                ),
                self._store.recent_chat_messages(
                    game_id=game_id,
                    player_id=player_id,
                    limit=manifest.recent_chat_messages,
                ),
            ),
        )
        checkpoint: AdvancementAuthorizationCheckpoint | None = None
        input_fingerprint = decision_input_fingerprint(
            {
                "game_id": game_id,
                "player_id": player_id,
                "scene_id": str(scene["scene_id"]),
                "scene_revision": int(scene["scene_revision"]),
                "character_id": character.character_id,
                "traits": [
                    {
                        "name": trait.name,
                        "level": trait.level,
                        "aspects": list(trait.aspects),
                    }
                    for trait in character.sheet.traits
                ],
                "request": request,
            }
        )
        if checkpoint_event_id is not None:
            output_type = decision_output_type_name(AdvancementAuthorizationCheckpoint)
            payload = self._store.decision_checkpoint(
                event_id=checkpoint_event_id,
                pipeline_key="advancement_safety",
                output_type=output_type,
                game_id=game_id,
                input_fingerprint=input_fingerprint,
            )
            if payload is not None:
                checkpoint = AdvancementAuthorizationCheckpoint.model_validate(payload)
        if checkpoint is None:
            decision = await self._pipeline.run(
                task="Decide whether advancement is currently fictionally allowed.",
                context=assembled,
            )
            checkpoint = AdvancementAuthorizationCheckpoint(
                decision=decision,
                scene_id=str(scene["scene_id"]),
                scene_revision=int(scene["scene_revision"]),
            )
            if checkpoint_event_id is not None:
                canonical = self._store.checkpoint_decision(
                    event_id=checkpoint_event_id,
                    pipeline_key="advancement_safety",
                    output_type=decision_output_type_name(AdvancementAuthorizationCheckpoint),
                    payload=checkpoint.model_dump(mode="json"),
                    game_id=game_id,
                    input_fingerprint=input_fingerprint,
                )
                checkpoint = AdvancementAuthorizationCheckpoint.model_validate(canonical)
        decision = checkpoint.decision
        if not decision.allowed:
            raise ValueError(f"advancement is not allowed now: {decision.reason}")
        return AdvancementPermit(
            game_id=game_id,
            player_id=player_id,
            scene_id=checkpoint.scene_id,
            scene_revision=checkpoint.scene_revision,
            reason=decision.reason,
        )
