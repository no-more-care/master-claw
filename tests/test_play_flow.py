import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from masterclaw.app.action_service import ActionService
from masterclaw.app.message_handler import MessageApplication
from masterclaw.app.orchestrator import ChannelOrchestrator
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.mechanics import CharacterSheet, Flag, FlagType, PoolProposal, Trait
from masterclaw.domain.models import (
    GameLifecycle,
    HandlerResponse,
    IncomingMessage,
    OutboundDelivery,
)
from masterclaw.domain.state import (
    GameState,
    PendingInteraction,
    PendingKind,
    PendingStatus,
    WorldState,
)
from masterclaw.pipelines.action import create_action_pipeline
from masterclaw.pipelines.base import CompletionResult, TransientProviderError
from masterclaw.pipelines.conversation_actions import create_roll_confirmation_pipeline
from masterclaw.pipelines.narrative import create_narrative_pipeline
from masterclaw.pipelines.state_decision import StateDecisionRouter
from masterclaw.storage.sqlite import SQLiteStore


class SequenceCompletion:
    def __init__(self, *responses: str) -> None:
        self.responses = iter(responses)
        self.calls = 0
        self.requests = []

    async def complete(self, **kwargs) -> CompletionResult:
        self.calls += 1
        self.requests.append(kwargs)
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
        proposal=PoolProposal(trait_names=("Trait 0",), difficulty=2),
        prompt="Confirm",
        declaration="I open the gate",
    )
    action.confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        confirmation_event_id="confirm-event",
        # A failed roll belongs to the system narrator, so replay must also
        # reconstruct the durable cross-channel delivery response.
        die=lambda: 1,
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


def setup_action_clarification_store(tmp_path, *, locale: str = "ru") -> SQLiteStore:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE, locale=locale))
    store.bind_channel(channel_id="game", game_id="game")
    store.create_scene(scene_id="room", game_id="game", title="Room", state={"facts": []})
    store.place_player(game_id="game", player_id="alice", scene_id="room")
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
    return store


def deliver_response_for_source(
    store: SQLiteStore,
    *,
    source_event_id: str,
    discord_message_id: str,
    content: str = "Confirm",
) -> None:
    source = IncomingMessage(
        event_id=source_event_id,
        channel_id="game",
        author_id="alice",
        content="source message",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert store.enqueue(source)
    claimed = store.claim_pending(channel_id="game", limit=1)
    assert [message.event_id for message in claimed] == [source_event_id]
    store.complete_batch(
        event_ids=[source_event_id],
        channel_id="game",
        contents=[],
        idempotency_key=f"test-response:{source_event_id}",
        payloads=[(content, None, source_event_id, "alice")],
    )
    response = next(
        row
        for row in store.pending_outbox(channel_id="game")
        if row["source_event_id"] == source_event_id and row["kind"] == "response"
    )
    store.mark_delivered(response["id"], discord_message_id=discord_message_id)


def test_replayed_initial_declaration_renders_the_committed_pool_without_rerouting(
    tmp_path,
) -> None:
    store = setup_action_clarification_store(tmp_path)
    states = SequenceCompletion(
        '{"command":"declare_action","argument":null,"confidence":1,"evidence":"action"}'
    )
    actions = SequenceCompletion(
        '{"resolution":"roll","trait_names":["Trait 0"],'
        '"aspect_names":["Aspect 0.0"],"flag":null,"bonus_ids":[],"difficulty":2,'
        '"evidence":["locked door"],"clarification_question":null}'
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(states),
        action_pipeline=create_action_pipeline(actions),
    )
    declaration = IncomingMessage(
        event_id="replayed-declaration",
        channel_id="game",
        author_id="alice",
        content="Открываю запертую дверь.",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    first = asyncio.run(app(declaration))
    pending_before = store.open_pending(game_id="game", player_id="alice")
    replayed = asyncio.run(app(declaration))
    pending_after = store.open_pending(game_id="game", player_id="alice")

    assert "Пул:" in first
    assert replayed == first
    assert pending_after == pending_before
    assert states.calls == 1
    assert actions.calls == 1


def test_reply_to_unrelated_discord_message_cannot_resolve_current_pending(tmp_path) -> None:
    store = setup_action_clarification_store(tmp_path)
    pending = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="room",
        proposal=PoolProposal(trait_names=("Trait 0",), difficulty=2),
        prompt="Confirm",
        declaration="Open the door",
        source_event_id="declaration",
        origin_channel_id="game",
    )
    deliver_response_for_source(
        store,
        source_event_id="declaration",
        discord_message_id="bot-prompt",
    )
    store.queue_system_notice(
        channel_id="game",
        key="attachment-warning",
        content="Attachments are unsupported",
        source_event_id="declaration",
        source_author_id="alice",
    )
    notice = next(
        row for row in store.pending_outbox(channel_id="game") if row["kind"] == "system_notice"
    )
    store.mark_delivered(notice["id"], discord_message_id="attachment-notice")
    states = SequenceCompletion(
        '{"command":"answer_pending","argument":null,"confidence":1,"evidence":"yes"}'
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(states),
        die=lambda: 4,
    )

    mismatch = asyncio.run(
        app(
            IncomingMessage(
                event_id="wrong-reply",
                channel_id="game",
                author_id="alice",
                content="да",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                reply_to_event_id="some-other-message",
            )
        )
    )

    assert "другому сообщению" in mismatch
    assert store.pending_by_id(pending.interaction_id).status is PendingStatus.OPEN
    assert states.calls == 0

    notice_mismatch = asyncio.run(
        app(
            IncomingMessage(
                event_id="notice-reply",
                channel_id="game",
                author_id="alice",
                content="да",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                reply_to_event_id="attachment-notice",
            )
        )
    )

    assert "другому сообщению" in notice_mismatch
    assert store.pending_by_id(pending.interaction_id).status is PendingStatus.OPEN
    assert states.calls == 0

    accepted = asyncio.run(
        app(
            IncomingMessage(
                event_id="right-reply",
                channel_id="game",
                author_id="alice",
                content="да",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                reply_to_event_id="bot-prompt",
            )
        )
    )

    assert "Успехов" in accepted
    assert store.pending_by_id(pending.interaction_id).status is PendingStatus.RESOLVED
    assert states.calls == 0


def test_compound_pool_accepts_reply_to_root_combined_response(tmp_path) -> None:
    store = setup_action_clarification_store(tmp_path)
    pending = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="room",
        proposal=PoolProposal(trait_names=("Trait 0",), difficulty=2),
        prompt="Confirm",
        declaration="Open the door",
        source_event_id="compound-root:part:1",
        root_source_event_id="compound-root",
        origin_channel_id="game",
    )
    deliver_response_for_source(
        store,
        source_event_id="compound-root",
        discord_message_id="combined-prompt",
        content="Earlier roleplay.\n\nConfirm the pool.",
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(SequenceCompletion()),
        die=lambda: 4,
    )

    accepted = asyncio.run(
        app(
            IncomingMessage(
                event_id="compound-confirm",
                channel_id="game",
                author_id="alice",
                content="yes",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                reply_to_event_id="combined-prompt",
            )
        )
    )

    assert "Успехов" in accepted
    assert store.pending_by_id(pending.interaction_id).status is PendingStatus.RESOLVED


@pytest.mark.parametrize("content", ["yes", "I open the other door"])
def test_non_reply_in_sibling_channel_does_not_consume_origin_channel_pending(
    tmp_path,
    content: str,
) -> None:
    store = setup_action_clarification_store(tmp_path)
    store.bind_channel(channel_id="sibling", game_id="game")
    pending = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="room",
        proposal=PoolProposal(trait_names=("Trait 0",), difficulty=2),
        prompt="Confirm",
        declaration="Open the door",
        source_event_id="declaration",
        origin_channel_id="game",
    )
    states = SequenceCompletion(
        '{"command":"clarify","argument":null,"confidence":1,'
        '"evidence":"the isolated sibling message is ambiguous"}'
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(states),
    )

    response = asyncio.run(
        app(
            IncomingMessage(
                event_id="sibling-yes",
                channel_id="sibling",
                author_id="alice",
                content=content,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    assert isinstance(response, str)
    assert "<#game>" in response
    assert store.pending_by_id(pending.interaction_id).status is PendingStatus.OPEN
    assert states.calls == 0


def test_sibling_channel_is_usable_after_pending_origin_channel_unbind(tmp_path) -> None:
    store = setup_action_clarification_store(tmp_path)
    store.bind_channel(channel_id="sibling", game_id="game")
    pending = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="room",
        proposal=PoolProposal(trait_names=("Trait 0",), difficulty=2),
        prompt="Confirm",
        declaration="Open the door",
        source_event_id="declaration",
        origin_channel_id="game",
    )
    store.unbind_channel(channel_id="game", expected_game_id="game")
    states = SequenceCompletion(
        '{"command":"clarify","argument":null,"confidence":1,"evidence":"unused"}'
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(states),
    )

    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="sibling-status",
                channel_id="sibling",
                author_id="alice",
                content="/status",
            )
        )
    )

    assert store.pending_by_id(pending.interaction_id).status is PendingStatus.CANCELLED
    assert "<#game>" not in response
    assert "World" in response
    assert states.calls == 0


def test_inherited_thread_reconciliation_does_not_leave_dangling_pending(tmp_path) -> None:
    store = setup_action_clarification_store(tmp_path)
    store.record_discord_channel(
        channel_id="game",
        guild_id="guild",
        parent_channel_id=None,
        kind="text",
    )
    store.record_discord_channel(
        channel_id="thread",
        guild_id="guild",
        parent_channel_id="game",
        kind="thread",
    )
    store.enable_channel_monitoring("game")
    assert store.inherit_thread_context(
        channel_id="thread",
        parent_channel_id="game",
        guild_id="guild",
    )
    pending = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="room",
        proposal=PoolProposal(trait_names=("Trait 0",), difficulty=2),
        prompt="Confirm",
        source_event_id="thread-declaration",
        origin_channel_id="thread",
    )

    assert store.disable_channel_monitoring("game")
    assert not store.inherit_thread_context(
        channel_id="thread",
        parent_channel_id="game",
        guild_id="guild",
    )
    assert store.pending_by_id(pending.interaction_id).status is PendingStatus.CANCELLED
    assert store.channel_state("thread").game_id is None

    store.enable_channel_monitoring("game")
    assert store.inherit_thread_context(
        channel_id="thread",
        parent_channel_id="game",
        guild_id="guild",
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            SequenceCompletion(
                '{"command":"clarify","argument":null,"confidence":1,"evidence":"unused"}'
            )
        ),
    )
    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="thread-status",
                channel_id="thread",
                author_id="alice",
                content="/status",
            )
        )
    )

    assert "World" in response
    assert "<#thread>" not in response


@pytest.mark.parametrize(
    ("action_response", "expected_kind"),
    [
        (
            '{"resolution":"clarification","trait_names":[],"aspect_names":[],'
            '"flag":null,"bonus_ids":[],"difficulty":null,"evidence":[],'
            '"clarification_question":"Which door?"}',
            PendingKind.CLARIFICATION,
        ),
        (
            '{"resolution":"roll","trait_names":["Trait 0"],'
            '"aspect_names":["Aspect 0.0"],"flag":null,"bonus_ids":[],'
            '"difficulty":2,"evidence":["locked door"],"clarification_question":null}',
            PendingKind.POOL_CONFIRMATION,
        ),
    ],
)
def test_frozen_turn_cancels_only_the_pending_it_creates_after_channel_rebind(
    tmp_path,
    action_response: str,
    expected_kind: PendingKind,
) -> None:
    store = setup_action_clarification_store(tmp_path)
    store.create_world(WorldState("other-world", "Other World"))
    store.create_game(GameState("other-game", "other-world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="game", game_id="other-game")
    states = SequenceCompletion(
        '{"command":"declare_action","argument":null,"confidence":1,"evidence":"action"}'
    )
    actions = SequenceCompletion(action_response)
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(states),
        action_pipeline=create_action_pipeline(actions),
    )

    response = asyncio.run(
        app(
            IncomingMessage(
                event_id="late-frozen-action",
                channel_id="game",
                author_id="alice",
                content="I open the locked door.",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                routing_game_id="game",
                routing_lifecycle=GameLifecycle.ACTIVE,
                has_routing_snapshot=True,
            )
        )
    )

    assert "Созданный интерактивный шаг отменён" in response
    assert store.open_pending(game_id="game", player_id="alice") is None
    with store.connect() as connection:
        row = connection.execute(
            """SELECT kind, status, payload_json FROM pending_interactions
               WHERE game_id = 'game' AND player_id = 'alice'
               ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()
    assert row is not None
    assert PendingKind(row["kind"]) is expected_kind
    assert PendingStatus(row["status"]) is PendingStatus.CANCELLED
    assert store.activity_state("game")["last_event_at"] is None
    assert states.calls == 1
    assert actions.calls == 1


def test_frozen_player_narration_replay_is_cancelled_after_channel_rebind(tmp_path) -> None:
    store = setup_action_clarification_store(tmp_path)
    store.create_world(WorldState("other-world", "Other World"))
    store.create_game(GameState("other-game", "other-world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="game", game_id="other-game")
    pending = PendingInteraction(
        interaction_id="late-player-narration",
        game_id="game",
        player_id="alice",
        scene_id="room",
        kind=PendingKind.PLAYER_NARRATION,
        prompt="Describe the committed roll outcome.",
        payload={
            "roll_id": "committed-roll",
            "prompt_source_event_id": "late-narration-source",
        },
        origin_channel_id="game",
    )
    store.put_pending(pending)
    states = SequenceCompletion(
        '{"command":"answer_pending","argument":null,"confidence":1,"evidence":"answer"}'
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(states),
    )

    response = asyncio.run(
        app(
            IncomingMessage(
                event_id="late-narration-source",
                channel_id="game",
                author_id="alice",
                content="the confirmation replay",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                routing_game_id="game",
                routing_lifecycle=GameLifecycle.ACTIVE,
                has_routing_snapshot=True,
            )
        )
    )

    assert "Describe the committed roll outcome." in response
    assert "Созданный интерактивный шаг отменён" in response
    assert store.pending_by_id(pending.interaction_id).status is PendingStatus.CANCELLED
    assert states.calls == 0


def test_frozen_turn_replay_cancels_each_recreated_pending_after_completion_failure(
    tmp_path,
    monkeypatch,
) -> None:
    store = setup_action_clarification_store(tmp_path)
    store.create_world(WorldState("other-world", "Other World"))
    store.create_game(GameState("other-game", "other-world", GameLifecycle.ACTIVE))
    incoming = IncomingMessage.now(
        event_id="replayed-late-action",
        channel_id="game",
        author_id="alice",
        content="I open the locked door.",
    )
    assert store.enqueue(incoming)
    store.bind_channel(channel_id="game", game_id="other-game")
    state_response = (
        '{"command":"declare_action","argument":null,"confidence":1,"evidence":"action"}'
    )
    action_response = (
        '{"resolution":"roll","trait_names":["Trait 0"],'
        '"aspect_names":["Aspect 0.0"],"flag":null,"bonus_ids":[],'
        '"difficulty":2,"evidence":["locked door"],"clarification_question":null}'
    )
    states = SequenceCompletion(state_response, state_response)
    actions = SequenceCompletion(action_response, action_response)
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(states),
        action_pipeline=create_action_pipeline(actions),
    )
    original_complete = store.complete_batch
    completion_attempts = 0

    def fail_first_completion(**kwargs):
        nonlocal completion_attempts
        completion_attempts += 1
        if completion_attempts == 1:
            raise RuntimeError("simulated completion failure")
        return original_complete(**kwargs)

    monkeypatch.setattr(store, "complete_batch", fail_first_completion)
    orchestrator = ChannelOrchestrator(store, app)

    with pytest.raises(RuntimeError, match="simulated completion failure"):
        asyncio.run(orchestrator.process_available("game"))
    result = asyncio.run(orchestrator.process_available("game"))

    assert result is not None
    assert "Созданный интерактивный шаг отменён" in result.items[0].text
    assert completion_attempts == 2
    assert states.calls == 1
    assert actions.calls == 1
    assert store.open_pending(game_id="game", player_id="alice") is None
    with store.connect() as connection:
        rows = connection.execute(
            """SELECT status, payload_json FROM pending_interactions
               WHERE game_id = 'game' AND player_id = 'alice'
               ORDER BY created_at, interaction_id"""
        ).fetchall()
    assert len(rows) == 1
    assert all(PendingStatus(row["status"]) is PendingStatus.CANCELLED for row in rows)
    assert store.activity_state("game")["last_event_at"] is None


def test_journaled_pool_prompt_is_fenced_if_channel_rebinds_before_completion(
    tmp_path,
    monkeypatch,
) -> None:
    store = setup_action_clarification_store(tmp_path)
    bob_sheet = CharacterSheet(
        "Bob",
        tuple(
            Trait(
                f"Bob Trait {index}",
                3,
                tuple(f"Bob Aspect {index}.{aspect}" for aspect in range(3)),
            )
            for index in range(6)
        ),
        (
            Flag("Alice", FlagType.RELATIONSHIP),
            Flag("Help the group", FlagType.GOAL),
            Flag("We survive together", FlagType.BELIEF),
        ),
    )
    store.create_character(CharacterState("bob", "game", "bob", "Helper", bob_sheet))
    store.place_player(game_id="game", player_id="bob", scene_id="room")
    store.create_world(WorldState("other-world", "Other World"))
    store.create_game(GameState("other-game", "other-world", GameLifecycle.ACTIVE))
    incoming = IncomingMessage.now(
        event_id="journaled-pool-before-rebind",
        channel_id="game",
        author_id="alice",
        content="I open the locked door.",
    )
    assert store.enqueue(incoming)
    states = SequenceCompletion(
        '{"command":"declare_action","argument":null,"confidence":1,"evidence":"action"}'
    )
    actions = SequenceCompletion(
        '{"resolution":"roll","trait_names":["Trait 0"],'
        '"aspect_names":["Aspect 0.0"],"flag":null,"bonus_ids":[],"difficulty":2,'
        '"evidence":["locked door"],"clarification_question":null}'
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(states),
        action_pipeline=create_action_pipeline(actions),
    )
    original_complete = store.complete_batch
    completion_attempts = 0

    def fail_first_completion(**kwargs):
        nonlocal completion_attempts
        completion_attempts += 1
        if completion_attempts == 1:
            raise RuntimeError("simulated completion failure")
        return original_complete(**kwargs)

    monkeypatch.setattr(store, "complete_batch", fail_first_completion)
    orchestrator = ChannelOrchestrator(store, app)

    with pytest.raises(RuntimeError, match="simulated completion failure"):
        asyncio.run(orchestrator.process_available("game"))
    journaled = store.handler_result(incoming.event_id)
    pending = store.open_pending(game_id="game", player_id="alice")
    assert journaled is not None
    assert pending is not None
    store.offer_help(
        game_id="game",
        helper_player_id="bob",
        target_player_id="alice",
    )
    assert store.character_for_player(game_id="game", player_id="bob").sheet.reserve_current == 6

    store.bind_channel(channel_id="game", game_id="other-game")
    replayed = asyncio.run(orchestrator.process_available("game"))

    assert replayed is not None
    assert completion_attempts == 2
    assert states.calls == 1
    assert actions.calls == 1
    assert store.pending_by_id(pending.interaction_id).status is PendingStatus.CANCELLED
    assert store.character_for_player(game_id="game", player_id="bob").sheet.reserve_current == 7
    deliverable = store.pending_outbox(channel_id="game")
    assert [row["kind"] for row in deliverable] == ["system_notice"]
    with store.connect() as connection:
        fenced = connection.execute(
            """SELECT kind, attempts, error
               FROM outbox_messages
               WHERE source_event_id = ? AND kind = 'response'""",
            (incoming.event_id,),
        ).fetchone()
    assert fenced is None


def test_stale_route_notice_preserves_handler_deliveries(tmp_path) -> None:
    class PendingDeliveryApplication(MessageApplication):
        async def _dispatch(self, message, channel):
            self._store.put_pending(
                PendingInteraction(
                    interaction_id="pending-with-delivery",
                    game_id=str(channel.game_id),
                    player_id=message.author_id,
                    scene_id="room",
                    kind=PendingKind.CHOICE,
                    prompt="Choose.",
                    payload={"prompt_source_event_id": message.event_id},
                    origin_channel_id=message.channel_id,
                )
            )
            return HandlerResponse(
                "Choose.",
                (OutboundDelivery("narrative", "Earlier roleplay output.", "roleplay_reply"),),
            )

    store = setup_action_clarification_store(tmp_path)
    store.create_world(WorldState("other-world", "Other World"))
    store.create_game(GameState("other-game", "other-world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="game", game_id="other-game")
    app = PendingDeliveryApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            SequenceCompletion(
                '{"command":"clarify","argument":null,"confidence":1,"evidence":"unused"}'
            )
        ),
    )

    response = asyncio.run(
        app(
            IncomingMessage(
                event_id="late-delivery",
                channel_id="game",
                author_id="alice",
                content="compound turn",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                routing_game_id="game",
                routing_lifecycle=GameLifecycle.ACTIVE,
                has_routing_snapshot=True,
            )
        )
    )

    assert isinstance(response, HandlerResponse)
    assert "Созданный интерактивный шаг отменён" in response.text
    assert response.deliveries == (
        OutboundDelivery("narrative", "Earlier roleplay output.", "roleplay_reply"),
    )
    assert store.pending_by_id("pending-with-delivery").status is PendingStatus.CANCELLED


@pytest.mark.parametrize(
    "kind",
    [PendingKind.CLARIFICATION, PendingKind.CHOICE, PendingKind.PLAYER_NARRATION],
)
def test_replayed_pending_source_renders_committed_prompt_without_answering_it(
    tmp_path,
    kind: PendingKind,
) -> None:
    store = setup_action_clarification_store(tmp_path)
    pending = PendingInteraction(
        interaction_id=f"pending-{kind.value}",
        game_id="game",
        player_id="alice",
        scene_id="room",
        kind=kind,
        prompt=f"Current {kind.value} prompt",
        payload={"prompt_source_event_id": "source-replay"},
        origin_channel_id="game",
    )
    store.put_pending(pending)
    states = SequenceCompletion(
        '{"command":"answer_pending","argument":null,"confidence":1,"evidence":"answer"}'
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(states),
    )

    response = asyncio.run(
        app(
            IncomingMessage(
                event_id="source-replay",
                channel_id="game",
                author_id="alice",
                content="the message that installed this pending",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                reply_to_event_id="older-prompt",
            )
        )
    )

    assert f"Current {kind.value} prompt" in response
    assert store.pending_by_id(pending.interaction_id).status is PendingStatus.OPEN
    assert states.calls == 0


def test_deferred_pool_replay_precedes_reply_mismatch_check(tmp_path) -> None:
    store = setup_action_clarification_store(tmp_path)
    deliver_response_for_source(
        store,
        source_event_id="prior-question",
        discord_message_id="prior-prompt",
    )
    pending = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="room",
        proposal=PoolProposal(trait_names=("Trait 0",), difficulty=2),
        prompt="Confirm",
        declaration="Open the door",
        source_event_id="replacement-source",
        origin_channel_id="game",
    )
    states = SequenceCompletion(
        '{"command":"answer_pending","argument":null,"confidence":1,"evidence":"answer"}'
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(states),
    )

    response = asyncio.run(
        app(
            IncomingMessage(
                event_id="replacement-source",
                channel_id="game",
                author_id="alice",
                content="clarification answer",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                reply_to_event_id="prior-prompt",
            )
        )
    )

    assert "Пул:" in response
    assert store.pending_by_id(pending.interaction_id).status is PendingStatus.OPEN
    assert states.calls == 0


def test_impossible_action_is_rejected_without_roll_pending_or_scene_mutation(tmp_path) -> None:
    store = setup_action_clarification_store(tmp_path)
    before = store.scene_projection(game_id="game", player_id="alice")
    states = SequenceCompletion(
        '{"command":"declare_action","argument":null,"confidence":1,'
        '"evidence":"explicit impossible action"}'
    )
    actions = SequenceCompletion(
        '{"resolution":"rejected","trait_names":[],"aspect_names":[],'
        '"flag":null,"bonus_ids":[],"difficulty":null,'
        '"evidence":["the character is in an ordinary room on Earth"],'
        '"clarification_question":null,'
        '"rejection_reason":"Персонаж не может допрыгнуть из комнаты до Луны."}'
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(states),
        action_pipeline=create_action_pipeline(actions),
    )

    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="moon-jump",
                channel_id="game",
                author_id="alice",
                content="Прыгаю на Луну.",
            )
        )
    )

    after = store.scene_projection(game_id="game", player_id="alice")
    assert "Действие невозможно или недопустимо" in response
    assert "не может допрыгнуть" in response
    assert store.open_pending(game_id="game", player_id="alice") is None
    assert after["scene_revision"] == before["scene_revision"]
    assert after["location_revision"] == before["location_revision"]


def test_rejected_action_response_uses_english_game_locale(tmp_path) -> None:
    store = setup_action_clarification_store(tmp_path, locale="en")
    states = SequenceCompletion(
        '{"command":"declare_action","argument":null,"confidence":1,"evidence":"action"}'
    )
    actions = SequenceCompletion(
        '{"resolution":"rejected","trait_names":[],"aspect_names":[],'
        '"flag":null,"bonus_ids":[],"difficulty":null,'
        '"evidence":["the character is standing on Earth"],'
        '"clarification_question":null,'
        '"rejection_reason":"A person cannot jump from Earth to the Moon."}'
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(states),
        action_pipeline=create_action_pipeline(actions),
    )

    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="moon-jump-en",
                channel_id="game",
                author_id="alice",
                content="I jump to the Moon.",
            )
        )
    )

    assert "🎭 PLAY" in response
    assert "🎭 ИГРА" not in response
    assert "Participants" in response
    assert "The action is impossible or prohibited" in response
    assert "cannot jump from Earth" in response


def test_controlling_another_player_character_is_rejected_without_mutation(tmp_path) -> None:
    store = setup_action_clarification_store(tmp_path)
    bob_sheet = CharacterSheet(
        "Bob",
        tuple(
            Trait(
                f"Bob Trait {i}",
                3,
                tuple(f"Bob Aspect {i}.{n}" for n in range(3)),
            )
            for i in range(6)
        ),
        (
            Flag("Bob Friend", FlagType.RELATIONSHIP),
            Flag("Bob Goal", FlagType.GOAL),
            Flag("Bob Belief", FlagType.BELIEF),
        ),
    )
    store.create_character(CharacterState("bob-hero", "game", "bob", "Bob bio", bob_sheet))
    store.place_player(game_id="game", player_id="bob", scene_id="room")
    bob_before = store.character_for_player(game_id="game", player_id="bob")
    scene_before = store.scene_projection(game_id="game", player_id="alice")
    states = SequenceCompletion(
        '{"command":"declare_action","argument":null,"confidence":1,'
        '"evidence":"controls another participant"}'
    )
    actions = SequenceCompletion(
        '{"resolution":"rejected","trait_names":[],"aspect_names":[],'
        '"flag":null,"bonus_ids":[],"difficulty":null,'
        '"evidence":["Bob belongs to another player"],'
        '"clarification_question":null,'
        '"rejection_reason":"Нельзя объявлять действия за персонажа другого игрока."}'
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(states),
        action_pipeline=create_action_pipeline(actions),
    )

    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="control-bob",
                channel_id="game",
                author_id="alice",
                content="Заставляю Боба открыть дверь.",
            )
        )
    )

    bob_after = store.character_for_player(game_id="game", player_id="bob")
    scene_after = store.scene_projection(game_id="game", player_id="alice")
    assert "Нельзя объявлять действия" in response
    assert bob_after == bob_before
    assert scene_after["scene_revision"] == scene_before["scene_revision"]
    assert store.open_pending(game_id="game", player_id="alice") is None


def test_clarification_continuation_can_close_as_rejected(tmp_path) -> None:
    store = setup_action_clarification_store(tmp_path)
    states = SequenceCompletion(
        '{"command":"declare_action","argument":null,"confidence":1,"evidence":"action"}',
        '{"command":"answer_pending","argument":null,"confidence":1,"evidence":"answer"}',
    )
    actions = SequenceCompletion(
        '{"resolution":"clarification","trait_names":[],"aspect_names":[],'
        '"flag":null,"bonus_ids":[],"difficulty":null,"evidence":[],'
        '"clarification_question":"Кого именно вы заставляете открыть дверь?"}',
        '{"resolution":"rejected","trait_names":[],"aspect_names":[],'
        '"flag":null,"bonus_ids":[],"difficulty":null,'
        '"evidence":["the target is another player character"],'
        '"clarification_question":null,'
        '"rejection_reason":"Нельзя управлять персонажем другого игрока."}',
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(states),
        action_pipeline=create_action_pipeline(actions),
    )

    asyncio.run(
        app(
            IncomingMessage.now(
                event_id="ambiguous-control",
                channel_id="game",
                author_id="alice",
                content="Заставляю его открыть дверь.",
            )
        )
    )
    clarification = store.open_pending(game_id="game", player_id="alice")
    assert clarification is not None
    assert clarification.kind is PendingKind.CLARIFICATION

    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="identify-other-pc",
                channel_id="game",
                author_id="alice",
                content="Персонажа Боба, другого игрока.",
            )
        )
    )

    assert "Нельзя управлять персонажем другого игрока" in response
    assert store.open_pending(game_id="game", player_id="alice") is None
    resolved = store.pending_by_id(clarification.interaction_id)
    assert resolved.status is PendingStatus.RESOLVED
    assert resolved.payload["answer"] == "Персонажа Боба, другого игрока."


def test_action_clarification_is_typed_and_answer_replaces_it_atomically(tmp_path) -> None:
    store = setup_action_clarification_store(tmp_path)
    states = SequenceCompletion(
        '{"command":"declare_action","argument":null,"confidence":1,"evidence":"action"}',
        '{"command":"answer_pending","argument":null,"confidence":1,'
        '"evidence":"answers exact action question"}',
    )
    actions = SequenceCompletion(
        '{"resolution":"clarification","trait_names":[],"aspect_names":[],'
        '"flag":null,"bonus_ids":[],"difficulty":null,"evidence":[],'
        '"clarification_question":"Which tool do you use?"}',
        '{"resolution":"roll","trait_names":["Trait 0"],'
        '"aspect_names":["Aspect 0.0"],"flag":null,"bonus_ids":[],'
        '"difficulty":2,"evidence":["lockpicks"],"clarification_question":null}',
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(states),
        action_pipeline=create_action_pipeline(actions),
    )

    first = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="declare", channel_id="game", author_id="alice", content="I open the lock."
            )
        )
    )
    clarification = store.open_pending(game_id="game", player_id="alice")
    assert "Which tool" in first
    assert clarification.kind is PendingKind.CLARIFICATION
    assert clarification.payload["original_declaration"] == "I open the lock."
    assert clarification.prompt == "Which tool do you use?"

    second = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="answer",
                channel_id="game",
                author_id="alice",
                content="With my lockpicks.",
            )
        )
    )
    pool = store.open_pending(game_id="game", player_id="alice")
    assert "Пул" in second
    assert pool.kind is PendingKind.POOL_CONFIRMATION
    assert "With my lockpicks" in pool.payload["declaration"]
    resolved = store.pending_by_id(clarification.interaction_id)
    assert resolved.status is PendingStatus.RESOLVED
    assert resolved.payload["answer"] == "With my lockpicks."
    assert "Which tool do you use?" in actions.requests[-1]["task"]
    assert "With my lockpicks." in actions.requests[-1]["task"]
    roll = ActionService(store).confirm_roll(
        interaction_id=pool.interaction_id,
        player_id="alice",
        confirmation_event_id="confirm-continued-action",
        die=iter((4, 4)).__next__,
    )
    assert roll.difficulty == 2


def test_transient_clarification_continuation_keeps_pending_for_retry(tmp_path) -> None:
    class FlakyActions:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, **kwargs) -> CompletionResult:
            self.calls += 1
            if self.calls == 1:
                return CompletionResult(
                    '{"resolution":"clarification","trait_names":[],"aspect_names":[],'
                    '"flag":null,"bonus_ids":[],"difficulty":null,"evidence":[],'
                    '"clarification_question":"Which route?"}',
                    used_tool=True,
                )
            if self.calls == 2:
                raise TransientProviderError("temporary outage")
            return CompletionResult(
                '{"resolution":"roll","trait_names":["Trait 0"],'
                '"aspect_names":[],"flag":null,"bonus_ids":[],"difficulty":2,'
                '"evidence":["chosen route"],"clarification_question":null}',
                used_tool=True,
            )

    store = setup_action_clarification_store(tmp_path)
    states = SequenceCompletion(
        '{"command":"declare_action","argument":null,"confidence":1,"evidence":"action"}',
        '{"command":"answer_pending","argument":null,"confidence":1,"evidence":"answer"}',
        '{"command":"answer_pending","argument":null,"confidence":1,"evidence":"retry"}',
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(states),
        action_pipeline=create_action_pipeline(FlakyActions()),
    )
    asyncio.run(
        app(
            IncomingMessage.now(
                event_id="declare", channel_id="game", author_id="alice", content="I enter."
            )
        )
    )
    clarification = store.open_pending(game_id="game", player_id="alice")
    answer = IncomingMessage.now(
        event_id="answer",
        channel_id="game",
        author_id="alice",
        content="The left route.",
    )

    with pytest.raises(TransientProviderError):
        asyncio.run(app(answer))
    unchanged = store.open_pending(game_id="game", player_id="alice")
    assert unchanged.interaction_id == clarification.interaction_id
    assert unchanged.revision == clarification.revision

    retried = asyncio.run(app(answer))
    assert "Пул" in retried
    assert store.pending_by_id(clarification.interaction_id).status is PendingStatus.RESOLVED


def test_cancel_plus_new_intent_closes_old_pending_and_dispatches_remainder(tmp_path) -> None:
    store = setup_action_clarification_store(tmp_path)
    states = SequenceCompletion(
        '{"command":"declare_action","argument":null,"confidence":1,"evidence":"action"}',
        '{"command":"answer_pending","argument":null,"confidence":1,'
        '"evidence":"cancel and replace"}',
        '{"command":"declare_action","argument":null,"confidence":1,"evidence":"new action"}',
    )
    actions = SequenceCompletion(
        '{"resolution":"clarification","trait_names":[],"aspect_names":[],'
        '"flag":null,"bonus_ids":[],"difficulty":null,"evidence":[],'
        '"clarification_question":"Which door?"}',
        '{"resolution":"clarification","trait_names":[],"aspect_names":[],'
        '"flag":null,"bonus_ids":[],"difficulty":null,"evidence":[],'
        '"clarification_question":"How do you inspect it?"}',
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(states),
        action_pipeline=create_action_pipeline(actions),
    )
    asyncio.run(
        app(
            IncomingMessage.now(
                event_id="old", channel_id="game", author_id="alice", content="I open a door."
            )
        )
    )
    old = store.open_pending(game_id="game", player_id="alice")

    replacement = IncomingMessage.now(
        event_id="replace",
        channel_id="game",
        author_id="alice",
        content="cancel, I inspect the window.",
    )
    response = asyncio.run(app(replacement))

    assert "How do you inspect" in response
    assert store.pending_by_id(old.interaction_id).status is PendingStatus.CANCELLED
    current = store.open_pending(game_id="game", player_id="alice")
    assert current.payload["original_declaration"] == "I inspect the window."

    replayed = asyncio.run(app(replacement))

    assert "How do you inspect" in replayed
    assert store.open_pending(game_id="game", player_id="alice").interaction_id == (
        current.interaction_id
    )
    assert states.calls == 3
    assert actions.calls == 2


def test_frozen_cancel_remainder_keeps_ingress_game_after_live_rebind(tmp_path) -> None:
    store = setup_action_clarification_store(tmp_path)
    store.create_world(WorldState("other-world", "Other World"))
    store.create_game(GameState("other-game", "other-world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="game", game_id="other-game")
    pending = PendingInteraction(
        interaction_id="frozen-cancel-choice",
        game_id="game",
        player_id="alice",
        scene_id="room",
        kind=PendingKind.CHOICE,
        prompt="Choose an old-game route.",
        payload={"options": ["left", "right"]},
        origin_channel_id="game",
    )
    store.put_pending(pending)
    states = SequenceCompletion(
        '{"command":"answer_pending","argument":null,"confidence":1,'
        '"evidence":"cancel old step and continue"}'
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(states),
    )
    frozen = IncomingMessage(
        event_id="frozen-cancel-status",
        channel_id="game",
        author_id="alice",
        content="cancel, /status",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        routing_game_id="game",
        routing_lifecycle=GameLifecycle.ACTIVE,
        has_routing_snapshot=True,
    )

    first = asyncio.run(app(frozen))
    replayed = asyncio.run(app(frozen))

    assert "· World\n" in first
    assert "· World\n" in replayed
    assert "· Other World\n" not in first
    assert "· Other World\n" not in replayed
    assert store.pending_by_id(pending.interaction_id).status is PendingStatus.CANCELLED
    assert states.calls == 1


def test_expired_pool_replay_after_completion_failure_refunds_helper_once(
    tmp_path,
    monkeypatch,
) -> None:
    store = setup_action_clarification_store(tmp_path)
    bob_sheet = CharacterSheet(
        "Bob",
        tuple(
            Trait(
                f"Bob Trait {index}",
                3,
                tuple(f"Bob Aspect {index}.{aspect}" for aspect in range(3)),
            )
            for index in range(6)
        ),
        (
            Flag("Alice", FlagType.RELATIONSHIP),
            Flag("Help the group", FlagType.GOAL),
            Flag("We survive together", FlagType.BELIEF),
        ),
    )
    store.create_character(CharacterState("bob", "game", "bob", "Helper", bob_sheet))
    store.place_player(game_id="game", player_id="bob", scene_id="room")
    pending = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="room",
        proposal=PoolProposal(trait_names=("Trait 0",), difficulty=2),
        prompt="Confirm",
        source_event_id="declaration",
        origin_channel_id="game",
    )
    store.offer_help(
        game_id="game",
        helper_player_id="bob",
        target_player_id="alice",
    )
    assert (
        store.character_for_player(
            game_id="game",
            player_id="bob",
        ).sheet.reserve_current
        == 6
    )
    with store.transaction() as connection:
        connection.execute(
            "UPDATE pending_interactions SET created_at = ? WHERE interaction_id = ?",
            ("2026-01-01T00:00:00+00:00", pending.interaction_id),
        )
    incoming = IncomingMessage(
        event_id="expire-event",
        channel_id="game",
        author_id="alice",
        content="What is happening?",
        created_at=datetime(2026, 1, 2, 1, tzinfo=UTC),
    )
    assert store.enqueue(incoming)
    states = SequenceCompletion()
    application = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(states),
    )
    original_complete = store.complete_batch
    completion_attempts = 0

    def fail_first_completion(**kwargs):
        nonlocal completion_attempts
        completion_attempts += 1
        if completion_attempts == 1:
            raise RuntimeError("simulated completion failure")
        return original_complete(**kwargs)

    monkeypatch.setattr(store, "complete_batch", fail_first_completion)
    orchestrator = ChannelOrchestrator(store, application)

    with pytest.raises(RuntimeError, match="simulated completion failure"):
        asyncio.run(orchestrator.process_available("game"))
    replayed = asyncio.run(orchestrator.process_available("game"))

    assert replayed is not None
    assert "подтверждение пула отменено" in replayed.items[0].text.lower()
    assert completion_attempts == 2
    assert states.calls == 0
    assert store.pending_by_id(pending.interaction_id).status is PendingStatus.EXPIRED
    assert (
        store.character_for_player(
            game_id="game",
            player_id="bob",
        ).sheet.reserve_current
        == 7
    )


def test_expired_non_pool_continues_once_and_replays_synthetic_pool_after_completion_failure(
    tmp_path,
    monkeypatch,
) -> None:
    store = setup_action_clarification_store(tmp_path)
    store.put_pending(
        PendingInteraction(
            "stale-choice",
            "game",
            "alice",
            "room",
            PendingKind.CHOICE,
            "Choose an old route.",
            payload={"options": ["left", "right"]},
            origin_channel_id="game",
        )
    )
    with store.transaction() as connection:
        connection.execute(
            "UPDATE pending_interactions SET created_at = ? WHERE interaction_id = ?",
            ("2026-01-01T00:00:00+00:00", "stale-choice"),
        )
    states = SequenceCompletion(
        '{"command":"declare_action","argument":null,"confidence":1,'
        '"evidence":"a new action after the obsolete choice"}'
    )
    actions = SequenceCompletion(
        '{"resolution":"roll","trait_names":["Trait 0"],'
        '"aspect_names":["Aspect 0.0"],"flag":null,"bonus_ids":[],'
        '"difficulty":2,"evidence":["locked door"],"clarification_question":null}'
    )
    application = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(states),
        action_pipeline=create_action_pipeline(actions),
    )
    incoming = IncomingMessage(
        event_id="replace-stale-choice",
        channel_id="game",
        author_id="alice",
        content="I open the locked door.",
        created_at=datetime(2026, 1, 2, 1, tzinfo=UTC),
    )
    assert store.enqueue(incoming)
    original_complete = store.complete_batch
    completion_attempts = 0
    handled_responses: list[str | HandlerResponse] = []

    def fail_first_completion(**kwargs):
        nonlocal completion_attempts
        completion_attempts += 1
        if completion_attempts == 1:
            raise RuntimeError("simulated completion failure")
        return original_complete(**kwargs)

    monkeypatch.setattr(store, "complete_batch", fail_first_completion)

    async def recording_handler(message: IncomingMessage) -> str | HandlerResponse:
        response = await application(message)
        handled_responses.append(response)
        return response

    orchestrator = ChannelOrchestrator(store, recording_handler)

    with pytest.raises(RuntimeError, match="simulated completion failure"):
        asyncio.run(orchestrator.process_available("game"))
    committed_pool = store.open_pending(game_id="game", player_id="alice")
    replayed = asyncio.run(orchestrator.process_available("game"))

    assert replayed is not None
    assert len(handled_responses) == 1
    original_text = (
        handled_responses[0].text
        if isinstance(handled_responses[0], HandlerResponse)
        else handled_responses[0]
    )
    assert replayed.items[0].text == original_text
    assert completion_attempts == 2
    assert states.calls == 1
    assert actions.calls == 1
    assert store.pending_by_id("stale-choice").status is PendingStatus.EXPIRED
    assert committed_pool is not None
    assert committed_pool.kind is PendingKind.POOL_CONFIRMATION
    assert committed_pool.payload["prompt_source_event_id"] == ("replace-stale-choice:after-expire")
    current = store.open_pending(game_id="game", player_id="alice")
    assert current is not None
    assert current.interaction_id == committed_pool.interaction_id


def test_frozen_stale_expiry_continuation_keeps_ingress_game_on_direct_retry(
    tmp_path,
) -> None:
    store = setup_action_clarification_store(tmp_path)
    store.create_world(WorldState("other-world", "Other World"))
    store.create_game(GameState("other-game", "other-world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="game", game_id="other-game")
    pending = PendingInteraction(
        interaction_id="frozen-stale-choice",
        game_id="game",
        player_id="alice",
        scene_id="room",
        kind=PendingKind.CHOICE,
        prompt="Choose an obsolete old-game route.",
        payload={"options": ["left", "right"]},
        origin_channel_id="game",
    )
    store.put_pending(pending)
    with store.transaction() as connection:
        connection.execute(
            "UPDATE pending_interactions SET created_at = ? WHERE interaction_id = ?",
            ("2026-01-01T00:00:00+00:00", pending.interaction_id),
        )
    states = SequenceCompletion()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(states),
    )
    frozen = IncomingMessage(
        event_id="frozen-expire-status",
        channel_id="game",
        author_id="alice",
        content="/status",
        created_at=datetime(2026, 1, 2, 1, tzinfo=UTC),
        routing_game_id="game",
        routing_lifecycle=GameLifecycle.ACTIVE,
        has_routing_snapshot=True,
    )

    first = asyncio.run(app(frozen))
    replayed = asyncio.run(app(frozen))

    assert "· World\n" in first
    assert "· World\n" in replayed
    assert "· Other World\n" not in first
    assert "· Other World\n" not in replayed
    assert store.pending_by_id(pending.interaction_id).status is PendingStatus.EXPIRED
    assert states.calls == 0
