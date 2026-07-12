from __future__ import annotations

from dataclasses import dataclass

from masterclaw.domain.mechanics import CharacterSheet


@dataclass(frozen=True, slots=True)
class Condition:
    text: str
    source: str


@dataclass(frozen=True, slots=True)
class PlotItem:
    name: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class CharacterState:
    character_id: str
    game_id: str
    player_id: str
    biography: str
    sheet: CharacterSheet
    conditions: tuple[Condition, ...] = ()
    plot_items: tuple[PlotItem, ...] = ()
    experience_earned: int = 0
    experience_spent: int = 0
    revision: int = 0

    def __post_init__(self) -> None:
        if self.experience_earned < 0 or self.experience_spent < 0:
            raise ValueError("experience cannot be negative")
        if self.experience_spent > self.experience_earned:
            raise ValueError("spent experience cannot exceed earned experience")

    @property
    def experience_available(self) -> int:
        return self.experience_earned - self.experience_spent
