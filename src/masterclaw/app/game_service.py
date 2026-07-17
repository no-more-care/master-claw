from __future__ import annotations

import re
from datetime import UTC, datetime

from masterclaw.domain.models import GameLifecycle
from masterclaw.domain.state import (
    GameState,
    NarratorRightsLevel,
    ReadinessReport,
    ReserveRecoveryMode,
    WorldState,
    transition_game,
)
from masterclaw.storage.sqlite import SQLiteStore
from masterclaw.telemetry import traced_stage

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

    @traced_stage("domain.game_preparation", component="game_service")
    def prepare_game(
        self,
        *,
        game_id: str,
        world_id: str,
        channel_id: str,
        progression_enabled: bool = False,
        locale: str = "ru",
        narrator_rights_level: NarratorRightsLevel = NarratorRightsLevel.MINOR,
        reserve_recovery_mode: ReserveRecoveryMode = ReserveRecoveryMode.BOTH,
    ) -> GameState:
        validate_id(game_id, field="game_id")
        if locale not in {"ru", "en"}:
            raise ValueError("v2 currently supports only ru and en")
        game = GameState(
            game_id=game_id,
            world_id=world_id,
            lifecycle=GameLifecycle.PREPARING,
            progression_enabled=progression_enabled,
            narrator_rights_level=narrator_rights_level,
            locale=locale,
            reserve_recovery_mode=reserve_recovery_mode,
        )
        self._store.create_game(game)
        self._store.bind_channel(channel_id=channel_id, game_id=game_id)
        return game

    def restore_reserve_for_safe_rest(
        self,
        *,
        game_id: str,
        reason: str,
        causation_id: str,
    ) -> int:
        game = self._require_game(game_id)
        if not game.reserve_recovery_mode.allows_safe_rest:
            raise ValueError("safe-rest reserve recovery is disabled for this game")
        if not reason.strip():
            raise ValueError("safe-rest context is required")
        return self._store.restore_reserve_for_safe_rest(
            game_id=game_id,
            reason=reason.strip(),
            causation_id=causation_id,
        )

    def award_reserve_die(
        self,
        *,
        game_id: str,
        player_id: str,
        reason: str,
        causation_id: str,
    ) -> tuple[int, int]:
        game = self._require_game(game_id)
        if not game.reserve_recovery_mode.allows_roleplay_award:
            raise ValueError("roleplay reserve awards are disabled for this game")
        if not reason.strip():
            raise ValueError("award context is required")
        return self._store.award_reserve_die(
            game_id=game_id,
            player_id=player_id,
            reason=reason.strip(),
            causation_id=causation_id,
        )

    def _require_game(self, game_id: str) -> GameState:
        game = self._store.game_state(game_id)
        if game is None:
            raise ValueError(f"game does not exist: {game_id}")
        return game

    @traced_stage("domain.readiness_gate", component="game_service")
    def readiness(self, game_id: str) -> ReadinessReport:
        game = self._store.game_state(game_id)
        if game is None:
            raise ValueError(f"game does not exist: {game_id}")
        missing: list[str] = []
        if game.narrative_channel_id is None:
            missing.append("narrative_channel")
        if self._store.character_count(game_id) == 0:
            missing.append("characters")
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

    def configure_reserve_recovery(self, *, game_id: str, mode: ReserveRecoveryMode) -> GameState:
        game = self._require_game(game_id)
        self._store.set_reserve_recovery_mode(
            game_id=game_id,
            mode=mode,
            expected_revision=game.revision,
        )
        updated = self._store.game_state(game_id)
        assert updated is not None
        return updated

    @traced_stage("domain.game_start_gate", component="game_service")
    def start_game(self, game_id: str, *, started_at: datetime | None = None) -> GameState:
        report = self.readiness(game_id)
        if not report.ready:
            raise ValueError(f"game is not ready: {', '.join(report.missing)}")
        current = self._store.game_state(game_id)
        assert current is not None
        scene_id = self._store.first_scene_id(game_id)
        if scene_id is None:
            content = self._store.world_content(current.world_id) or {}
            locations = content.get("locations") or []
            first_location = locations[0] if locations and isinstance(locations[0], dict) else {}
            scene_id = f"opening_{game_id}"
            self._store.create_scene(
                scene_id=scene_id,
                game_id=game_id,
                title=str(first_location.get("name") or "Opening Scene"),
                state={
                    "world_location_id": first_location.get("id"),
                    "description": first_location.get("description", ""),
                    "facts": [],
                },
            )
        for character in self._store.character_roster(game_id):
            if character["scene_id"] is None:
                self._store.place_player(
                    game_id=game_id,
                    player_id=str(character["player_id"]),
                    scene_id=scene_id,
                )
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
