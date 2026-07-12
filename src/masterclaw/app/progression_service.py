from __future__ import annotations

from datetime import datetime

from masterclaw.domain.characters import CharacterState
from masterclaw.domain.progression import (
    ActivityUpdate,
    AdvancementPermit,
    learn_trait,
    raise_trait,
)
from masterclaw.storage.sqlite import SQLiteStore


class ProgressionService:
    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    def record_player_event(self, *, game_id: str, occurred_at: datetime) -> ActivityUpdate:
        return self._store.record_activity(game_id=game_id, occurred_at=occurred_at)

    def raise_character_trait(
        self,
        *,
        game_id: str,
        player_id: str,
        trait_name: str,
        new_aspect: str,
        permit: AdvancementPermit,
    ) -> CharacterState:
        character = self._require_authorized_character(
            game_id=game_id, player_id=player_id, permit=permit
        )
        result = raise_trait(
            character.sheet,
            trait_name=trait_name,
            new_aspect=new_aspect,
            available_xp=character.experience_available,
        )
        return self._store.update_character_progression(
            character_id=character.character_id,
            expected_revision=character.revision,
            sheet=result.sheet,
            xp_cost=result.xp_cost,
            description=result.description,
        )

    def learn_character_trait(
        self,
        *,
        game_id: str,
        player_id: str,
        trait_name: str,
        aspects: tuple[str, str],
        justification: str,
        permit: AdvancementPermit,
    ) -> CharacterState:
        character = self._require_authorized_character(
            game_id=game_id, player_id=player_id, permit=permit
        )
        result = learn_trait(
            character.sheet,
            trait_name=trait_name,
            aspects=aspects,
            justification=justification,
            available_xp=character.experience_available,
        )
        return self._store.update_character_progression(
            character_id=character.character_id,
            expected_revision=character.revision,
            sheet=result.sheet,
            xp_cost=result.xp_cost,
            description=f"{result.description}; justification: {justification.strip()}",
        )

    def _require_authorized_character(
        self,
        *,
        game_id: str,
        player_id: str,
        permit: AdvancementPermit,
    ) -> CharacterState:
        game = self._store.game_state(game_id)
        if game is None or not game.progression_enabled:
            raise ValueError("progression is disabled for this session")
        if permit.game_id != game_id or permit.player_id != player_id:
            raise ValueError("advancement permit does not match the request")
        character = self._store.character_for_player(game_id=game_id, player_id=player_id)
        if character is None:
            raise ValueError("player has no character in this game")
        scene = self._store.scene_projection(game_id=game_id, player_id=player_id)
        if (
            scene is None
            or scene["scene_id"] != permit.scene_id
            or scene["scene_revision"] != permit.scene_revision
        ):
            raise ValueError("scene changed after advancement was authorized")
        return character
