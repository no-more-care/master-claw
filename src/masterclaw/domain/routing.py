from __future__ import annotations

from dataclasses import dataclass

from .models import ChannelState, GameLifecycle, OperatingMode


@dataclass(frozen=True, slots=True)
class RouteDecision:
    mode: OperatingMode
    reason: str


class ModeRouter:
    """Deterministic top-level router. It never calls an LLM."""

    def route(self, *, channel: ChannelState) -> RouteDecision:
        if channel.game_id is None:
            return RouteDecision(OperatingMode.WORLD_MANAGEMENT, "channel_has_no_game")
        if channel.lifecycle in {GameLifecycle.DRAFT, GameLifecycle.PREPARING}:
            return RouteDecision(OperatingMode.PREPARATION, "game_not_started")
        if channel.lifecycle is GameLifecycle.ACTIVE:
            return RouteDecision(OperatingMode.PLAY, "active_game")
        if channel.lifecycle is GameLifecycle.PAUSED:
            return RouteDecision(OperatingMode.PAUSED, "paused_game")
        if channel.lifecycle is GameLifecycle.FINISHED:
            return RouteDecision(OperatingMode.FINISHED, "finished_game")
        return RouteDecision(OperatingMode.PREPARATION, "game_not_started")
