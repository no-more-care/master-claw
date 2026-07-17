from __future__ import annotations

from masterclaw.app.progression_service import ProgressionService
from masterclaw.context.assembler import ContextAssembler, ContextHistory
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.progression import AdvancementPermit
from masterclaw.pipelines.advancement import AdvancementSafetyDecision
from masterclaw.pipelines.base import BoundedJsonPipeline
from masterclaw.storage.sqlite import SQLiteStore
from masterclaw.telemetry import traced_stage


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
    ) -> CharacterState:
        permit = await self._authorize(
            game_id=game_id,
            player_id=player_id,
            request={"kind": "raise", "trait": trait_name, "new_aspect": new_aspect},
        )
        return self._progression.raise_character_trait(
            game_id=game_id,
            player_id=player_id,
            trait_name=trait_name,
            new_aspect=new_aspect,
            permit=permit,
        )

    async def learn_trait(
        self,
        *,
        game_id: str,
        player_id: str,
        trait_name: str,
        aspects: tuple[str, str],
        justification: str,
    ) -> CharacterState:
        permit = await self._authorize(
            game_id=game_id,
            player_id=player_id,
            request={
                "kind": "learn",
                "trait": trait_name,
                "aspects": aspects,
                "justification": justification,
            },
        )
        return self._progression.learn_character_trait(
            game_id=game_id,
            player_id=player_id,
            trait_name=trait_name,
            aspects=aspects,
            justification=justification,
            permit=permit,
        )

    @traced_stage("domain.advancement_gate", component="advancement_coordinator")
    async def _authorize(
        self, *, game_id: str, player_id: str, request: dict[str, object]
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
                "current_scene": scene,
                "actor_character": {
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
        decision = await self._pipeline.run(
            task="Decide whether advancement is currently fictionally allowed.",
            context=assembled,
        )
        if not decision.allowed:
            raise ValueError(f"advancement is not allowed now: {decision.reason}")
        return AdvancementPermit(
            game_id=game_id,
            player_id=player_id,
            scene_id=str(scene["scene_id"]),
            scene_revision=int(scene["scene_revision"]),
            reason=decision.reason,
        )
