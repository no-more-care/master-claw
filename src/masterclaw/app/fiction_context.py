from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from masterclaw.domain.characters import CharacterState


class FictionContextChangedError(RuntimeError):
    """The model output no longer belongs to the live actor/scene revisions."""


@dataclass(frozen=True, slots=True)
class FictionContextSnapshot:
    game_id: str
    player_id: str
    character_id: str
    character_revision: int
    scene_id: str
    scene_revision: int
    location_revision: int
    participants: tuple[str, ...]

    @classmethod
    def capture(
        cls,
        *,
        game_id: str,
        player_id: str,
        character: CharacterState,
        scene: Mapping[str, object],
    ) -> FictionContextSnapshot:
        return cls(
            game_id=game_id,
            player_id=player_id,
            character_id=character.character_id,
            character_revision=character.revision,
            scene_id=str(scene["scene_id"]),
            scene_revision=int(scene["scene_revision"]),
            location_revision=int(scene["location_revision"]),
            participants=tuple(str(value) for value in scene["participants"]),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> FictionContextSnapshot:
        raw_participants = value.get("participants", ())
        if not isinstance(raw_participants, (list, tuple)):
            raise ValueError("fiction context participants must be a list")
        return cls(
            game_id=str(value["game_id"]),
            player_id=str(value["player_id"]),
            character_id=str(value["character_id"]),
            character_revision=int(value["character_revision"]),
            scene_id=str(value["scene_id"]),
            scene_revision=int(value["scene_revision"]),
            location_revision=int(value["location_revision"]),
            participants=tuple(str(item) for item in raw_participants),
        )

    def as_mapping(self) -> dict[str, object]:
        return {
            "game_id": self.game_id,
            "player_id": self.player_id,
            "character_id": self.character_id,
            "character_revision": self.character_revision,
            "scene_id": self.scene_id,
            "scene_revision": self.scene_revision,
            "location_revision": self.location_revision,
            "participants": list(self.participants),
        }

    def matches(
        self,
        *,
        character: CharacterState | None,
        scene: Mapping[str, object] | None,
    ) -> bool:
        if character is None or scene is None:
            return False
        return (
            character.character_id == self.character_id
            and character.revision == self.character_revision
            and str(scene["scene_id"]) == self.scene_id
            and int(scene["scene_revision"]) == self.scene_revision
            and int(scene["location_revision"]) == self.location_revision
            and tuple(str(value) for value in scene["participants"]) == self.participants
        )


def actor_character_projection(character: CharacterState, *, player_id: str) -> dict[str, object]:
    return {
        "player_id": player_id,
        "character_id": character.character_id,
        "revision": character.revision,
        "name": character.sheet.name,
        "traits": [
            {
                "name": trait.name,
                "level": trait.level,
                "aspects": list(trait.aspects),
            }
            for trait in character.sheet.traits
        ],
        "flags": [flag.text for flag in character.sheet.flags],
        "reserve": character.sheet.reserve_current,
        "conditions": [item.text for item in character.conditions],
        "plot_items": [
            {"name": item.name, "description": item.description} for item in character.plot_items
        ],
        "temporary_bonuses": [
            {
                "bonus_id": bonus.bonus_id,
                "type": bonus.type.value,
                "trigger": bonus.trigger,
            }
            for bonus in character.sheet.temporary_bonuses
        ],
    }
