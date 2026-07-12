from __future__ import annotations

from dataclasses import dataclass

from .models import ChannelState, GameLifecycle, OperatingMode

WORLD_COMMANDS = frozenset({"world", "мир", "create-world", "создать-мир"})
PREPARATION_COMMANDS = frozenset({"prepare", "подготовка", "new-game", "новая-игра"})


@dataclass(frozen=True, slots=True)
class RouteDecision:
    mode: OperatingMode
    reason: str


class ModeRouter:
    """Deterministic top-level router. It never calls an LLM."""

    def route(self, *, command: str | None, channel: ChannelState) -> RouteDecision:
        normalized = (command or "").strip().lower().lstrip("/")
        if normalized in WORLD_COMMANDS:
            return RouteDecision(OperatingMode.WORLD_MANAGEMENT, "explicit_world_command")
        if normalized in PREPARATION_COMMANDS:
            return RouteDecision(OperatingMode.PREPARATION, "explicit_preparation_command")

        if channel.game_id is None:
            return RouteDecision(OperatingMode.WORLD_MANAGEMENT, "channel_has_no_game")
        if channel.lifecycle in {GameLifecycle.DRAFT, GameLifecycle.PREPARING}:
            return RouteDecision(OperatingMode.PREPARATION, "game_not_started")
        if channel.lifecycle is GameLifecycle.ACTIVE:
            return RouteDecision(OperatingMode.PLAY, "active_game")
        return RouteDecision(OperatingMode.PREPARATION, "inactive_game_requires_session_command")
