from __future__ import annotations

import re
from datetime import UTC, datetime

from masterclaw.domain.models import GameLifecycle
from masterclaw.domain.state import (
    GameState,
    NarratorRightsLevel,
    ReadinessReport,
    WorldState,
    transition_game,
)
from masterclaw.storage.sqlite import SQLiteStore

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


def validate_id(value: str, *, field: str) -> None:
    if not ID_PATTERN.fullmatch(value):
        raise ValueError(f"{field} must match {ID_PATTERN.pattern}")


class GameService:
    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    def create_world(self, *, world_id: str, title: str) -> WorldState:
        validate_id(world_id, field="world_id")
        if not title.strip():
            raise ValueError("world title cannot be empty")
        world = WorldState(world_id=world_id, title=title.strip())
        self._store.create_world(world)
        return world

    def prepare_game(
        self,
        *,
        game_id: str,
        world_id: str,
        channel_id: str,
        progression_enabled: bool = False,
        locale: str = "ru",
    ) -> GameState:
        validate_id(game_id, field="game_id")
        if locale not in {"ru", "en"}:
            raise ValueError("v2 currently supports only ru and en")
        game = GameState(
            game_id=game_id,
            world_id=world_id,
            lifecycle=GameLifecycle.PREPARING,
            progression_enabled=progression_enabled,
            locale=locale,
        )
        self._store.create_game(game)
        self._store.bind_channel(channel_id=channel_id, game_id=game_id)
        return game

    def readiness(self, game_id: str) -> ReadinessReport:
        game = self._store.game_state(game_id)
        if game is None:
            raise ValueError(f"game does not exist: {game_id}")
        missing: list[str] = []
        if game.narrative_channel_id is None:
            missing.append("narrative_channel")
        if self._store.scene_count(game_id) == 0:
            missing.append("initial_scene")
        if self._store.character_count(game_id) == 0:
            missing.append("characters")
        elif self._store.unplaced_character_count(game_id) > 0:
            missing.append("player_locations")
        return ReadinessReport(not missing, tuple(missing))

    def configure_progression(self, *, game_id: str, enabled: bool) -> GameState:
        game = self._store.game_state(game_id)
        if game is None:
            raise ValueError(f"game does not exist: {game_id}")
        self._store.set_progression_enabled(
            game_id=game_id, enabled=enabled, expected_revision=game.revision
        )
        updated = self._store.game_state(game_id)
        assert updated is not None
        return updated

    def configure_narrator_rights(self, *, game_id: str, level: NarratorRightsLevel) -> GameState:
        game = self._store.game_state(game_id)
        if game is None:
            raise ValueError(f"game does not exist: {game_id}")
        self._store.set_narrator_rights_level(
            game_id=game_id, level=level, expected_revision=game.revision
        )
        updated = self._store.game_state(game_id)
        assert updated is not None
        return updated

    def start_game(self, game_id: str, *, started_at: datetime | None = None) -> GameState:
        report = self.readiness(game_id)
        if not report.ready:
            raise ValueError(f"game is not ready: {', '.join(report.missing)}")
        current = self._store.game_state(game_id)
        assert current is not None
        target = transition_game(current, GameLifecycle.ACTIVE)
        self._store.update_game_lifecycle(
            game_id=game_id,
            expected_revision=current.revision,
            lifecycle=target.lifecycle,
        )
        self._store.start_activity_clock(
            game_id=game_id, started_at=started_at or datetime.now(UTC)
        )
        refreshed = self._store.game_state(game_id)
        assert refreshed is not None
        return refreshed
