from masterclaw.domain.models import ChannelState, GameLifecycle, OperatingMode
from masterclaw.domain.routing import ModeRouter


def test_unbound_channel_routes_to_world_management() -> None:
    decision = ModeRouter().route(channel=ChannelState("channel"))
    assert decision.mode is OperatingMode.WORLD_MANAGEMENT


def test_preparing_game_routes_to_preparation() -> None:
    channel = ChannelState("channel", "game", GameLifecycle.PREPARING)
    assert ModeRouter().route(channel=channel).mode is OperatingMode.PREPARATION


def test_active_game_routes_to_play() -> None:
    channel = ChannelState("channel", "game", GameLifecycle.ACTIVE)
    assert ModeRouter().route(channel=channel).mode is OperatingMode.PLAY


def test_active_game_state_is_independent_of_commands() -> None:
    channel = ChannelState("channel", "game", GameLifecycle.ACTIVE)
    decision = ModeRouter().route(channel=channel)
    assert decision.mode is OperatingMode.PLAY


def test_paused_game_routes_only_to_session_control() -> None:
    channel = ChannelState("channel", "game", GameLifecycle.PAUSED)
    assert ModeRouter().route(channel=channel).mode is OperatingMode.PAUSED


def test_finished_game_does_not_fall_back_to_preparation() -> None:
    channel = ChannelState("channel", "game", GameLifecycle.FINISHED)
    assert ModeRouter().route(channel=channel).mode is OperatingMode.FINISHED
