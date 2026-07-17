import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from masterclaw.app.action_service import ActionService
from masterclaw.app.message_handler import MessageApplication
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.mechanics import CharacterSheet, Flag, FlagType, PoolProposal, Trait
from masterclaw.domain.models import GameLifecycle, HandlerResponse, IncomingMessage
from masterclaw.domain.state import GameState, PendingInteraction, PendingKind, WorldState
from masterclaw.pipelines.base import CompletionResult
from masterclaw.pipelines.player_narration import (
    PlayerNarrationReview,
    create_player_narration_pipeline,
)
from masterclaw.pipelines.state_decision import StateDecisionRouter
from masterclaw.storage.sqlite import SQLiteStore


class NeverIntent:
    async def complete(self, **kwargs) -> CompletionResult:
        return CompletionResult(
            '{"command":"answer_pending","argument":null,"confidence":1,'
            '"evidence":"submitted narration"}',
            used_tool=True,
        )


class Review:
    def __init__(self, accepted: bool) -> None:
        self.accepted = accepted

    async def complete(self, **kwargs) -> CompletionResult:
        accepted = "true" if self.accepted else "false"
        approved = '"Approved player narration."' if self.accepted else "null"
        return CompletionResult(
            (
                f'{{"accepted":{accepted},"reason":"rights check",'
                f'"scale_back_request":{("null" if self.accepted else '"Reduce the scale"')},'
                f'"approved_narration":{approved}}}'
            ),
            used_tool=True,
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


def setup_cancellable_pending(tmp_path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="game", game_id="game")
    store.put_pending(
        PendingInteraction(
            "narration-pending",
            "game",
            "alice",
            None,
            PendingKind.PLAYER_NARRATION,
            "Опишите результат",
            payload={"roll_id": "not-needed-for-cancellation"},
        )
    )
    return store


def app(store, accepted):
    return MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverIntent()),
        player_narration_pipeline=create_player_narration_pipeline(Review(accepted)),
    )


def make_pending_stale(store: SQLiteStore) -> None:
    pending = store.open_pending(game_id="game", player_id="alice")
    with store.transaction() as connection:
        connection.execute(
            "UPDATE pending_interactions SET created_at = ? WHERE interaction_id = ?",
            ("2026-01-01T00:00:00+00:00", pending.interaction_id),
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
    assert result.deliveries[0].content == "Approved player narration."
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


@pytest.mark.parametrize(
    "text",
    ["I turn to <@123> and open the door.", "@everyone the gate falls.", "<@&456> wins."],
)
def test_approved_player_narration_rejects_discord_mentions(text: str) -> None:
    with pytest.raises(ValidationError, match="Discord mention"):
        PlayerNarrationReview.model_validate(
            {
                "accepted": True,
                "reason": "within rights",
                "scale_back_request": None,
                "approved_narration": text,
            }
        )


def test_stale_player_narration_answer_is_still_reviewed_and_resolved(tmp_path) -> None:
    store = setup(tmp_path)
    make_pending_stale(store)

    result = asyncio.run(
        app(store, True)(
            IncomingMessage(
                event_id="stale-narration",
                channel_id="game",
                author_id="alice",
                content="Я распахиваю дверь и отступаю в сторону.",
                created_at=datetime(2026, 1, 2, 1, tzinfo=UTC),
            )
        )
    )

    assert isinstance(result, HandlerResponse)
    assert result.deliveries[0].content == "Approved player narration."
    assert store.open_pending(game_id="game", player_id="alice") is None


@pytest.mark.parametrize("stale", [False, True])
def test_fresh_and_stale_player_narration_can_be_cancelled_without_review(tmp_path, stale) -> None:
    store = setup_cancellable_pending(tmp_path)
    if stale:
        make_pending_stale(store)
    created_at = datetime(2026, 1, 2, 1, tzinfo=UTC) if stale else datetime.now(UTC)

    result = asyncio.run(
        app(store, True)(
            IncomingMessage(
                event_id=f"cancel-narration-{stale}",
                channel_id="game",
                author_id="alice",
                content="отмена",
                created_at=created_at,
            )
        )
    )

    assert "наррация результата отменена" in result.lower()
    assert store.open_pending(game_id="game", player_id="alice") is None
