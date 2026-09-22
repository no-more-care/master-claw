from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import replace

from masterclaw.app.i18n import tr
from masterclaw.domain.actions import RollRecord
from masterclaw.domain.mechanics import (
    NarratorRights,
    PoolProposal,
    reserve_after_roll,
    roll_pool,
    validate_pool,
)
from masterclaw.domain.models import GameLifecycle
from masterclaw.domain.state import (
    NarratorRightsLevel,
    PendingInteraction,
    PendingKind,
    PendingStatus,
)
from masterclaw.storage.sqlite import SQLiteStore
from masterclaw.telemetry import traced_stage


class ActionService:
    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    @traced_stage("domain.roll_proposal", component="action_service")
    def propose_roll(
        self,
        *,
        game_id: str,
        player_id: str,
        scene_id: str,
        proposal: PoolProposal,
        prompt: str,
        declaration: str = "",
        source_event_id: str | None = None,
        origin_channel_id: str | None = None,
        root_source_event_id: str | None = None,
    ) -> PendingInteraction:
        if proposal.reserve_spent != 0:
            raise ValueError("reserve is selected only in the confirmation response")
        character = self._store.character_for_player(game_id=game_id, player_id=player_id)
        if character is None:
            raise ValueError("player has no character in this game")
        scene = self._store.scene_projection(game_id=game_id, player_id=player_id)
        if scene is None or scene["scene_id"] != scene_id:
            raise ValueError("player is not located in the proposed roll scene")
        pool = validate_pool(character.sheet, proposal)
        pending = PendingInteraction(
            interaction_id=str(uuid.uuid4()),
            game_id=game_id,
            player_id=player_id,
            scene_id=scene_id,
            kind=PendingKind.POOL_CONFIRMATION,
            prompt=prompt,
            payload={
                "character_id": character.character_id,
                "character_revision": character.revision,
                "scene_revision": int(scene["scene_revision"]),
                "location_revision": int(scene["location_revision"]),
                "trait_names": list(proposal.trait_names),
                "aspect_names": list(proposal.aspect_names),
                "flag": proposal.flag,
                "bonus_ids": list(proposal.bonus_ids),
                "reserve_spent": proposal.reserve_spent,
                "difficulty": proposal.difficulty,
                "validated_difficulty": pool.difficulty,
                "pool_size": pool.size,
                "declaration": declaration.strip(),
                "deferred_confirmation_event_id": source_event_id,
                "prompt_source_event_id": source_event_id,
                **(
                    {"root_source_event_id": root_source_event_id}
                    if root_source_event_id is not None
                    else {}
                ),
            },
            origin_channel_id=origin_channel_id,
        )
        self._store.put_pending(pending)
        return pending

    @traced_stage("domain.roll_confirmation", component="action_service")
    def confirm_roll(
        self,
        *,
        interaction_id: str,
        player_id: str,
        reserve_spent: int = 0,
        confirmation_event_id: str | None = None,
        die: Callable[[], int] | None = None,
    ) -> RollRecord:
        existing = self._store.roll_for_interaction(interaction_id)
        if existing is not None:
            return existing
        pending = self._store.pending_by_id(interaction_id)
        if pending is None or pending.status is not PendingStatus.OPEN:
            raise ValueError("pending interaction is not open")
        if pending.player_id != player_id or pending.kind is not PendingKind.POOL_CONFIRMATION:
            raise ValueError("player cannot confirm this interaction")
        game = self._store.game_state(pending.game_id)
        if game is None or game.lifecycle is not GameLifecycle.ACTIVE:
            raise ValueError("rolls can be confirmed only while the game is active")
        character = self._store.character_for_player(game_id=pending.game_id, player_id=player_id)
        if character is None:
            raise ValueError("character no longer exists")
        payload = pending.payload
        if character.character_id != payload["character_id"]:
            raise ValueError("pending interaction character mismatch")
        if character.revision != payload["character_revision"]:
            raise ValueError("character changed after pool confirmation request")
        scene = self._store.scene_projection(game_id=pending.game_id, player_id=player_id)
        if scene is None or scene["scene_id"] != pending.scene_id:
            raise ValueError("player scene changed after pool confirmation request")
        if int(scene["scene_revision"]) != int(payload.get("scene_revision", -1)):
            raise ValueError("scene changed after pool confirmation request")
        if int(scene["location_revision"]) != int(payload.get("location_revision", -1)):
            raise ValueError("player location changed after pool confirmation request")
        proposal = PoolProposal(
            trait_names=tuple(payload["trait_names"]),
            aspect_names=tuple(payload["aspect_names"]),
            flag=payload["flag"],
            reserve_spent=reserve_spent,
            difficulty=int(payload["difficulty"]),
            bonus_ids=tuple(payload.get("bonus_ids", ())),
        )
        pool = validate_pool(character.sheet, proposal)
        help_dice = self._store.help_count(interaction_id)
        pool = replace(
            pool,
            size=pool.size + help_dice,
            components=pool.components + tuple("help" for _ in range(help_dice)),
        )
        if pool.size != payload["pool_size"] + reserve_spent + help_dice or pool.difficulty != int(
            payload.get("validated_difficulty", payload["difficulty"])
        ):
            raise ValueError("confirmed pool no longer matches proposed pool")
        result = roll_pool(pool, die=die)
        effective_rights = result.narrator_rights
        if game is not None and game.narrator_rights_level is NarratorRightsLevel.DISABLED:
            effective_rights = (
                NarratorRights.GM_SUCCESS
                if result.hits >= result.difficulty
                else NarratorRights.GM_FAILURE
            )
        reserve_after = reserve_after_roll(
            reserve_after_spend=pool.reserve_after_spend,
            reserve_spent=proposal.reserve_spent,
            hits=result.hits,
            difficulty=result.difficulty,
            reserve_maximum=character.sheet.reserve_maximum,
        )
        record = RollRecord(
            roll_id=str(uuid.uuid4()),
            interaction_id=interaction_id,
            confirmation_event_id=(confirmation_event_id or f"manual:{interaction_id}"),
            game_id=pending.game_id,
            character_id=character.character_id,
            pool_size=pool.size,
            reserve_spent=reserve_spent,
            help_dice=help_dice,
            difficulty=result.difficulty,
            dice=result.dice,
            hits=result.hits,
            narrator_rights=effective_rights,
            reserve_after=reserve_after,
        )
        self._store.commit_roll(
            record=record,
            character_revision=character.revision,
            pending_revision=pending.revision,
            narration_interaction_id=(
                str(uuid.uuid4())
                if effective_rights
                in {
                    NarratorRights.PLAYER_SUCCESS,
                    NarratorRights.PLAYER_FAILURE,
                }
                else None
            ),
            narration_prompt=tr(
                game.locale if game is not None else "ru",
                "player_narrate",
            ),
            consumed_bonus_ids=pool.bonus_ids,
        )
        return self._store.roll_for_interaction(interaction_id) or record
