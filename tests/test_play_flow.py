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
from masterclaw.pipelines.base import CompletionResult
from masterclaw.pipelines.conversation_actions import create_roll_confirmation_pipeline
from masterclaw.pipelines.narrative import create_narrative_pipeline
from masterclaw.pipelines.state_decision import StateDecisionRouter
from masterclaw.storage.sqlite import SQLiteStore


class SequenceCompletion:
    def __init__(self, *responses: str) -> None:
        self.responses = iter(responses)
        self.calls = 0

    async def complete(self, **kwargs) -> CompletionResult:
        self.calls += 1
        return CompletionResult(next(self.responses), used_tool=True)


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
        '{"command":"declare_action","argument":null,"confidence":0.98,'
        '"evidence":"opens locked gate"}',
        '{"command":"answer_pending","argument":null,"confidence":0.99,'
        '"evidence":"confirms with reserve"}',
    )
    action_completion = SequenceCompletion(
        '{"resolution":"roll","trait_names":["Trait 0"],'
        '"aspect_names":["Aspect 0.0"],"flag":null,"difficulty":2,'
        '"evidence":["locked gate"],"clarification_question":null}'
    )
    narrative_completion = SequenceCompletion(
        '{"narrative":"Замок сухо щёлкает, и створка ворот медленно поддаётся."}'
    )
    confirmation_completion = SequenceCompletion('{"kind":"confirm","reserve_spent":2}')
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(intent_completion),
        action_pipeline=create_action_pipeline(action_completion),
        narrative_pipeline=create_narrative_pipeline(narrative_completion),
        roll_confirmation_pipeline=create_roll_confirmation_pipeline(confirmation_completion),
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
                content="Да, добавлю два куба из запаса",
                created_at=start + timedelta(minutes=2),
            )
        )
    )
    assert isinstance(resolved, HandlerResponse)
    assert "Права рассказчика" in resolved.text
    assert resolved.deliveries[0].channel_id == "narrative-channel"
    assert "Замок" in resolved.deliveries[0].content
    assert store.open_pending(game_id="game", player_id="alice") is None
    assert intent_completion.calls == 2
    assert action_completion.calls == 1
    assert narrative_completion.calls == 1
    assert confirmation_completion.calls == 1
    assert (
        sum(
            completion.calls
            for completion in (
                intent_completion,
                action_completion,
                narrative_completion,
                confirmation_completion,
            )
        )
        <= 7
    )


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
        state_router=StateDecisionRouter(never_intent),
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
    assert "Успехов" in resumed.text
    assert never_intent.calls == 0


def test_invalid_action_interpretation_requests_clarification(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="game-channel", game_id="game")
    store.create_scene(scene_id="gate", game_id="game", title="Gate", state={"facts": []})
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
    state_completion = SequenceCompletion(
        '{"command":"declare_action","argument":null,"confidence":0.9,'
        '"evidence":"action declaration"}'
    )
    invalid_action = SequenceCompletion("not-json", "still-not-json")
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(state_completion),
        action_pipeline=create_action_pipeline(invalid_action),
    )

    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="bad-action",
                channel_id="game-channel",
                author_id="alice",
                content="Я делаю что-то сложное.",
            )
        )
    )

    assert "Уточните, что именно делает персонаж" in response
    assert store.open_pending(game_id="game", player_id="alice") is None
