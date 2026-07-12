import asyncio
from datetime import UTC, datetime
from pathlib import Path

from masterclaw.app.action_service import ActionService
from masterclaw.app.message_handler import MessageApplication
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.mechanics import CharacterSheet, Flag, FlagType, PoolProposal, Trait
from masterclaw.domain.models import GameLifecycle, HandlerResponse, IncomingMessage
from masterclaw.domain.state import GameState, PendingKind, WorldState
from masterclaw.pipelines.intent import create_intent_pipeline
from masterclaw.pipelines.player_narration import create_player_narration_pipeline
from masterclaw.storage.sqlite import SQLiteStore


class NeverIntent:
    async def complete(self, *, system: str, user: str) -> str:
        raise AssertionError("open player narration pending is routed without intent LLM")


class Review:
    def __init__(self, accepted: bool) -> None:
        self.accepted = accepted

    async def complete(self, *, system: str, user: str) -> str:
        accepted = "true" if self.accepted else "false"
        return (
            f'{{"accepted":{accepted},"reason":"rights check",'
            f'"scale_back_request":{("null" if self.accepted else '"Reduce the scale"')}}}'
        )


def setup(tmp_path):
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(
        GameState(
            "game",
            "world",
            GameLifecycle.ACTIVE,
            narrative_channel_id="narrative",
        )
    )
    store.bind_channel(channel_id="game", game_id="game")
    store.create_scene(scene_id="room", game_id="game", title="Room")
    store.place_player(game_id="game", player_id="alice", scene_id="room")
    sheet = CharacterSheet(
        "Hero",
        tuple(Trait(f"T{i}", 3, tuple(f"A{i}.{n}" for n in range(3))) for i in range(6)),
        (
            Flag("Friend", FlagType.RELATIONSHIP),
            Flag("Goal", FlagType.GOAL),
            Flag("Belief", FlagType.BELIEF),
        ),
    )
    store.create_character(CharacterState("hero", "game", "alice", "Bio", sheet))
    pending = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="room",
        proposal=PoolProposal(trait_names=("T0",), aspect_names=("A0.0",), difficulty=1),
        prompt="Confirm",
        declaration="Act",
    )
    ActionService(store).confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        confirmation_event_id="confirm",
        die=lambda: 4,
    )
    narration_pending = store.open_pending(game_id="game", player_id="alice")
    assert narration_pending.kind is PendingKind.PLAYER_NARRATION
    return store


def app(store, accepted):
    return MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        intent_pipeline=create_intent_pipeline(NeverIntent()),
        player_narration_pipeline=create_player_narration_pipeline(Review(accepted)),
    )


def test_accepted_player_narration_is_published_and_resolves_pending(tmp_path) -> None:
    store = setup(tmp_path)
    result = asyncio.run(
        app(store, True)(
            IncomingMessage(
                event_id="narration",
                channel_id="game",
                author_id="alice",
                content="Я распахиваю дверь и отступаю в сторону.",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )
    assert isinstance(result, HandlerResponse)
    assert result.deliveries[0].channel_id == "narrative"
    assert store.open_pending(game_id="game", player_id="alice") is None


def test_rejected_player_narration_keeps_pending_open(tmp_path) -> None:
    store = setup(tmp_path)
    result = asyncio.run(
        app(store, False)(
            IncomingMessage(
                event_id="narration",
                channel_id="game",
                author_id="alice",
                content="Я становлюсь королём всего мира.",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )
    assert "Reduce the scale" in result
    assert store.open_pending(game_id="game", player_id="alice") is not None
