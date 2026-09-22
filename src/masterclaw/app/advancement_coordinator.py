from __future__ import annotations

from masterclaw.app.advancement_safety import (
    AdvancementSafetyDecider,
    SafetyVerdict,
    capture_advancement_safety_snapshot,
)
from masterclaw.app.progression_service import ProgressionService
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.progression import AdvancementPermit
from masterclaw.storage.sqlite import SQLiteStore
from masterclaw.telemetry import traced_stage


class AdvancementCoordinator:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        decider: AdvancementSafetyDecider,
    ) -> None:
        self._store = store
        self._decider = decider
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
        snapshot = capture_advancement_safety_snapshot(
            store=self._store,
            game=game,
            player_id=player_id,
            character=character,
            scene=scene,
            request=request,
        )
        assessment = await self._decider.assess(snapshot, checkpoint_event_id=checkpoint_event_id)
        if assessment.verdict is not SafetyVerdict.ALLOW:
            raise ValueError(f"advancement is not allowed now: {assessment.display_detail}")
        return AdvancementPermit(
            game_id=game_id,
            player_id=player_id,
            scene_id=snapshot.scene_id,
            scene_revision=snapshot.scene_revision,
            reason=assessment.display_detail,
        )
