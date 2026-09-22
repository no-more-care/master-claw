from __future__ import annotations

import hashlib
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
        self._store.prepare_game_and_bind_if_channel_available(
            game,
            channel_id=channel_id,
        )
        prepared = self._store.game_state(game_id)
        assert prepared is not None
        return prepared

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

    @staticmethod
    def _state_from_operation(result: dict[str, object]) -> GameState:
        return GameState(
            game_id=str(result["game_id"]),
            world_id=str(result["world_id"]),
            lifecycle=GameLifecycle(str(result["lifecycle"])),
            progression_enabled=bool(result["progression_enabled"]),
            narrator_rights_level=NarratorRightsLevel(str(result["narrator_rights_level"])),
            locale=str(result["locale"]),
            narrative_channel_id=(
                None
                if result.get("narrative_channel_id") is None
                else str(result["narrative_channel_id"])
            ),
            reserve_recovery_mode=ReserveRecoveryMode(str(result["reserve_recovery_mode"])),
            revision=int(result["revision"]),
        )

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

    def configure_progression(
        self,
        *,
        game_id: str,
        enabled: bool,
        event_id: str | None = None,
        channel_id: str | None = None,
    ) -> GameState:
        game = self._store.game_state(game_id)
        if game is None:
            raise ValueError(f"game does not exist: {game_id}")
        if event_id is not None:
            if channel_id is None:
                raise ValueError("event-scoped configuration requires a channel")
            return self._state_from_operation(
                self._store.configure_game_for_event(
                    event_id=event_id,
                    game_id=game_id,
                    channel_id=channel_id,
                    setting="progression",
                    value=enabled,
                    expected_revision=game.revision,
                )
            )
        self._store.set_progression_enabled(
            game_id=game_id, enabled=enabled, expected_revision=game.revision
        )
        updated = self._store.game_state(game_id)
        assert updated is not None
        return updated

    def configure_narrator_rights(
        self,
        *,
        game_id: str,
        level: NarratorRightsLevel,
        event_id: str | None = None,
        channel_id: str | None = None,
    ) -> GameState:
        game = self._store.game_state(game_id)
        if game is None:
            raise ValueError(f"game does not exist: {game_id}")
        if event_id is not None:
            if channel_id is None:
                raise ValueError("event-scoped configuration requires a channel")
            return self._state_from_operation(
                self._store.configure_game_for_event(
                    event_id=event_id,
                    game_id=game_id,
                    channel_id=channel_id,
                    setting="narrator_rights",
                    value=level,
                    expected_revision=game.revision,
                )
            )
        self._store.set_narrator_rights_level(
            game_id=game_id, level=level, expected_revision=game.revision
        )
        updated = self._store.game_state(game_id)
        assert updated is not None
        return updated

    def configure_reserve_recovery(
        self,
        *,
        game_id: str,
        mode: ReserveRecoveryMode,
        event_id: str | None = None,
        channel_id: str | None = None,
    ) -> GameState:
        game = self._require_game(game_id)
        if event_id is not None:
            if channel_id is None:
                raise ValueError("event-scoped configuration requires a channel")
            return self._state_from_operation(
                self._store.configure_game_for_event(
                    event_id=event_id,
                    game_id=game_id,
                    channel_id=channel_id,
                    setting="reserve_recovery",
                    value=mode,
                    expected_revision=game.revision,
                )
            )
        self._store.set_reserve_recovery_mode(
            game_id=game_id,
            mode=mode,
            expected_revision=game.revision,
        )
        updated = self._store.game_state(game_id)
        assert updated is not None
        return updated

    def configure_narrative_channel(
        self,
        *,
        game_id: str,
        narrative_channel_id: str,
        event_id: str | None = None,
        channel_id: str | None = None,
    ) -> GameState:
        game = self._require_game(game_id)
        if event_id is not None:
            if channel_id is None:
                raise ValueError("event-scoped configuration requires a channel")
            return self._state_from_operation(
                self._store.configure_game_for_event(
                    event_id=event_id,
                    game_id=game_id,
                    channel_id=channel_id,
                    setting="narrative_channel",
                    value=narrative_channel_id,
                    expected_revision=game.revision,
                )
            )
        self._store.set_narrative_channel(
            game_id=game_id,
            channel_id=narrative_channel_id,
            expected_revision=game.revision,
        )
        updated = self._store.game_state(game_id)
        assert updated is not None
        return updated

    @traced_stage("domain.game_start_gate", component="game_service")
    def start_game(
        self,
        game_id: str,
        *,
        started_at: datetime | None = None,
        event_id: str | None = None,
        channel_id: str | None = None,
    ) -> GameState:
        current = self._require_game(game_id)
        if current.lifecycle not in {GameLifecycle.PREPARING, GameLifecycle.ACTIVE}:
            raise ValueError(f"game cannot start from lifecycle {current.lifecycle.value}")
        report = self.readiness(game_id)
        if not report.ready:
            raise ValueError(f"game is not ready: {', '.join(report.missing)}")
        scene_id = self._store.first_scene_id(game_id)
        content = self._store.world_content(current.world_id) or {}
        locations = [item for item in content.get("locations") or [] if isinstance(item, dict)]
        for index, location in enumerate(locations):
            location_key = str(location.get("id") or f"location-{index}")
            location_scene_id = (
                f"opening_{game_id}"
                if index == 0
                else "location_"
                + game_id
                + "_"
                + hashlib.sha256(location_key.encode("utf-8")).hexdigest()[:12]
            )
            if self._store.scene_by_id(game_id=game_id, scene_id=location_scene_id) is None:
                self._store.create_scene(
                    scene_id=location_scene_id,
                    game_id=game_id,
                    title=str(location.get("name") or f"Location {index + 1}"),
                    state={
                        "world_location_id": location.get("id"),
                        "description": location.get("description", ""),
                        "facts": [],
                    },
                )
            if scene_id is None and index == 0:
                scene_id = location_scene_id
        if scene_id is None:
            scene_id = f"opening_{game_id}"
            self._store.create_scene(
                scene_id=scene_id,
                game_id=game_id,
                title="Opening Scene",
                state={"facts": []},
            )
        for character in self._store.character_roster(game_id):
            if character["scene_id"] is None:
                self._store.place_player(
                    game_id=game_id,
                    player_id=str(character["player_id"]),
                    scene_id=scene_id,
                )
        activity_started_at = started_at or datetime.now(UTC)
        if event_id is not None:
            if channel_id is None:
                raise ValueError("event-scoped transition requires a channel")
            return self._state_from_operation(
                self._store.transition_game_for_event(
                    event_id=event_id,
                    operation_type="start_game",
                    game_id=game_id,
                    channel_id=channel_id,
                    expected_revision=current.revision,
                    occurred_at=activity_started_at,
                )
            )
        if current.lifecycle is not GameLifecycle.ACTIVE:
            target = transition_game(current, GameLifecycle.ACTIVE)
            self._store.update_game_lifecycle(
                game_id=game_id,
                expected_revision=current.revision,
                lifecycle=target.lifecycle,
            )
        self._store.start_activity_clock(game_id=game_id, started_at=activity_started_at)
        refreshed = self._store.game_state(game_id)
        assert refreshed is not None
        return refreshed

    def pause_game(
        self,
        game_id: str,
        *,
        event_id: str | None = None,
        channel_id: str | None = None,
    ) -> GameState:
        current = self._require_game(game_id)
        if event_id is not None:
            if channel_id is None:
                raise ValueError("event-scoped transition requires a channel")
            return self._state_from_operation(
                self._store.transition_game_for_event(
                    event_id=event_id,
                    operation_type="pause_game",
                    game_id=game_id,
                    channel_id=channel_id,
                    expected_revision=current.revision,
                )
            )
        if current.lifecycle is GameLifecycle.PAUSED:
            self._store.pause_activity_clock(game_id=game_id)
            return current
        if current.lifecycle is not GameLifecycle.ACTIVE:
            raise ValueError(f"game cannot pause from lifecycle {current.lifecycle.value}")
        target = transition_game(current, GameLifecycle.PAUSED)
        self._store.update_game_lifecycle(
            game_id=game_id,
            expected_revision=current.revision,
            lifecycle=target.lifecycle,
        )
        self._store.pause_activity_clock(game_id=game_id)
        refreshed = self._store.game_state(game_id)
        assert refreshed is not None
        return refreshed

    def resume_game(
        self,
        game_id: str,
        *,
        resumed_at: datetime | None = None,
        event_id: str | None = None,
        channel_id: str | None = None,
    ) -> GameState:
        current = self._require_game(game_id)
        activity_started_at = resumed_at or datetime.now(UTC)
        if event_id is not None:
            if channel_id is None:
                raise ValueError("event-scoped transition requires a channel")
            return self._state_from_operation(
                self._store.transition_game_for_event(
                    event_id=event_id,
                    operation_type="resume_game",
                    game_id=game_id,
                    channel_id=channel_id,
                    expected_revision=current.revision,
                    occurred_at=activity_started_at,
                )
            )
        if current.lifecycle is GameLifecycle.ACTIVE:
            self._store.start_activity_clock(game_id=game_id, started_at=activity_started_at)
            return current
        if current.lifecycle is not GameLifecycle.PAUSED:
            raise ValueError(f"game cannot resume from lifecycle {current.lifecycle.value}")
        target = transition_game(current, GameLifecycle.ACTIVE)
        self._store.update_game_lifecycle(
            game_id=game_id,
            expected_revision=current.revision,
            lifecycle=target.lifecycle,
        )
        self._store.start_activity_clock(game_id=game_id, started_at=activity_started_at)
        refreshed = self._store.game_state(game_id)
        assert refreshed is not None
        return refreshed

    def finish_game(
        self,
        game_id: str,
        *,
        event_id: str | None = None,
        channel_id: str | None = None,
    ) -> GameState:
        current = self._require_game(game_id)
        if event_id is not None:
            if channel_id is None:
                raise ValueError("event-scoped transition requires a channel")
            return self._state_from_operation(
                self._store.transition_game_for_event(
                    event_id=event_id,
                    operation_type="finish_game",
                    game_id=game_id,
                    channel_id=channel_id,
                    expected_revision=current.revision,
                )
            )
        return self._store.finish_game_and_cleanup(
            game_id=game_id,
            expected_revision=current.revision,
        )

    def unbind_channel(
        self,
        *,
        channel_id: str,
        game_id: str,
        event_id: str | None = None,
        operation_type: str = "unbind_game",
    ) -> None:
        if event_id is not None:
            self._store.unbind_channel_for_event(
                event_id=event_id,
                operation_type=operation_type,
                channel_id=channel_id,
                game_id=game_id,
            )
            return
        self._store.unbind_channel(channel_id=channel_id, expected_game_id=game_id)
