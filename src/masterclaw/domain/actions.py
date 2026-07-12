from __future__ import annotations

from dataclasses import dataclass

from masterclaw.domain.mechanics import NarratorRights


@dataclass(frozen=True, slots=True)
class RollRecord:
    roll_id: str
    interaction_id: str
    confirmation_event_id: str
    game_id: str
    character_id: str
    pool_size: int
    reserve_spent: int
    help_dice: int
    difficulty: int
    dice: tuple[int, ...]
    hits: int
    narrator_rights: NarratorRights
    reserve_after: int
