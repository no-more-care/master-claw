from masterclaw.domain.models import ChannelState, GameLifecycle, OperatingMode
from masterclaw.domain.routing import ModeRouter


def test_unbound_channel_routes_to_world_management() -> None:
    decision = ModeRouter().route(command=None, channel=ChannelState("channel"))
    assert decision.mode is OperatingMode.WORLD_MANAGEMENT


def test_preparing_game_routes_to_preparation() -> None:
    channel = ChannelState("channel", "game", GameLifecycle.PREPARING)
    assert ModeRouter().route(command=None, channel=channel).mode is OperatingMode.PREPARATION


def test_active_game_routes_to_play() -> None:
    channel = ChannelState("channel", "game", GameLifecycle.ACTIVE)
    assert ModeRouter().route(command=None, channel=channel).mode is OperatingMode.PLAY


def test_explicit_world_command_overrides_active_game() -> None:
    channel = ChannelState("channel", "game", GameLifecycle.ACTIVE)
    decision = ModeRouter().route(command="/мир", channel=channel)
    assert decision.mode is OperatingMode.WORLD_MANAGEMENT
