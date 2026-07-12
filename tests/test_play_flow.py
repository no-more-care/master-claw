import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from masterclaw.app.action_service import ActionService
from masterclaw.app.message_handler import MessageApplication
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.mechanics import CharacterSheet, Flag, FlagType, PoolProposal, Trait
from masterclaw.domain.models import GameLifecycle, HandlerResponse, IncomingMessage
from masterclaw.domain.state import GameState, WorldState
from masterclaw.pipelines.action import create_action_pipeline
from masterclaw.pipelines.intent import create_intent_pipeline
from masterclaw.pipelines.narrative import create_narrative_pipeline
from masterclaw.storage.sqlite import SQLiteStore


class SequenceCompletion:
    def __init__(self, *responses: str) -> None:
        self.responses = iter(responses)
        self.calls = 0

    async def complete(self, *, system: str, user: str) -> str:
        self.calls += 1
        return next(self.responses)


def test_declaration_to_confirmation_roll_and_dual_channel_narrative(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(
        GameState(
            "game",
            "world",
            GameLifecycle.ACTIVE,
            narrative_channel_id="narrative-channel",
        )
    )
    store.bind_channel(channel_id="game-channel", game_id="game")
    store.create_scene(
        scene_id="gate",
        game_id="game",
        title="Gate",
        state={"facts": ["The gate is locked"]},
    )
    store.place_player(game_id="game", player_id="alice", scene_id="gate")
    sheet = CharacterSheet(
        "Hero",
        tuple(Trait(f"Trait {i}", 3, tuple(f"Aspect {i}.{n}" for n in range(3))) for i in range(6)),
        (
            Flag("Friend", FlagType.RELATIONSHIP),
            Flag("Goal", FlagType.GOAL),
            Flag("Belief", FlagType.BELIEF),
        ),
        reserve_current=5,
    )
    store.create_character(CharacterState("hero", "game", "alice", "Bio", sheet))
    start = datetime(2026, 1, 1, tzinfo=UTC)
    store.start_activity_clock(game_id="game", started_at=start)

    intent_completion = SequenceCompletion(
        '{"intent":"action_declaration","confidence":0.98,"evidence":"opens locked gate"}'
    )
    action_completion = SequenceCompletion(
        '{"resolution":"roll","trait_names":["Trait 0"],'
        '"aspect_names":["Aspect 0.0"],"flag":null,"difficulty":2,'
        '"evidence":["locked gate"],"clarification_question":null}'
    )
    narrative_completion = SequenceCompletion(
        '{"narrative":"Замок сухо щёлкает, и створка ворот медленно поддаётся."}'
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        intent_pipeline=create_intent_pipeline(intent_completion),
        action_pipeline=create_action_pipeline(action_completion),
        narrative_pipeline=create_narrative_pipeline(narrative_completion),
        die=iter((4, 4, 1, 1)).__next__,
    )

    proposal = asyncio.run(
        app(
            IncomingMessage(
                event_id="declaration",
                channel_id="game-channel",
                author_id="alice",
                content="Я пытаюсь открыть запертые ворота.",
                created_at=start + timedelta(minutes=1),
            )
        )
    )
    assert "Пул: 2 куб." in proposal
    pending = store.open_pending(game_id="game", player_id="alice")
    assert pending is not None

    resolved = asyncio.run(
        app(
            IncomingMessage(
                event_id="confirm",
                channel_id="game-channel",
                author_id="alice",
                content="2",
                created_at=start + timedelta(minutes=2),
            )
        )
    )
    assert isinstance(resolved, HandlerResponse)
    assert "Права рассказчика" in resolved.text
    assert resolved.deliveries[0].channel_id == "narrative-channel"
    assert "Замок" in resolved.deliveries[0].content
    assert store.open_pending(game_id="game", player_id="alice") is None
    assert intent_completion.calls == 1
    assert action_completion.calls == 1
    assert narrative_completion.calls == 1


def test_committed_roll_can_resume_from_same_discord_event_without_open_pending(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(
        GameState(
            "game",
            "world",
            GameLifecycle.ACTIVE,
            narrative_channel_id="narrative-channel",
        )
    )
    store.bind_channel(channel_id="game-channel", game_id="game")
    store.create_scene(scene_id="gate", game_id="game", title="Gate")
    store.place_player(game_id="game", player_id="alice", scene_id="gate")
    sheet = CharacterSheet(
        "Hero",
        tuple(Trait(f"Trait {i}", 3, tuple(f"Aspect {i}.{n}" for n in range(3))) for i in range(6)),
        (
            Flag("Friend", FlagType.RELATIONSHIP),
            Flag("Goal", FlagType.GOAL),
            Flag("Belief", FlagType.BELIEF),
        ),
    )
    store.create_character(CharacterState("hero", "game", "alice", "Bio", sheet))
    action = ActionService(store)
    pending = action.propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="gate",
        proposal=PoolProposal(trait_names=("Trait 0",), difficulty=1),
        prompt="Confirm",
        declaration="I open the gate",
    )
    action.confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        confirmation_event_id="confirm-event",
        die=lambda: 4,
    )
    narrative = SequenceCompletion('{"narrative":"Ворота открываются."}')
    never_intent = SequenceCompletion()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        intent_pipeline=create_intent_pipeline(never_intent),
        narrative_pipeline=create_narrative_pipeline(narrative),
    )
    resumed = asyncio.run(
        app(
            IncomingMessage(
                event_id="confirm-event",
                channel_id="game-channel",
                author_id="alice",
                content="0",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )
    assert isinstance(resumed, HandlerResponse)
    assert "успехов" in resumed.text
    assert never_intent.calls == 0
