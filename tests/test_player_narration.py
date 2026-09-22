import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from masterclaw.app.action_service import ActionService
from masterclaw.app.i18n import tr
from masterclaw.app.message_handler import MessageApplication
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.mechanics import CharacterSheet, Flag, FlagType, PoolProposal, Trait
from masterclaw.domain.models import GameLifecycle, HandlerResponse, IncomingMessage
from masterclaw.domain.state import GameState, PendingInteraction, PendingKind, WorldState
from masterclaw.pipelines.action import create_action_pipeline
from masterclaw.pipelines.base import CompletionResult
from masterclaw.pipelines.consequence import create_consequence_pipeline
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
        self.calls = 0

    async def complete(self, **kwargs) -> CompletionResult:
        self.calls += 1
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
        proposal=PoolProposal(
            trait_names=("T0",),
            aspect_names=("A0.0", "A0.1"),
            difficulty=2,
        ),
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


def make_pending_stale(store: SQLiteStore) -> str:
    pending = store.open_pending(game_id="game", player_id="alice")
    with store.transaction() as connection:
        connection.execute(
            "UPDATE pending_interactions SET created_at = ? WHERE interaction_id = ?",
            ("2026-01-01T00:00:00+00:00", pending.interaction_id),
        )
    return pending.interaction_id


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


def test_accepted_player_narration_replay_does_not_become_a_second_action(tmp_path) -> None:
    class ReplayTrapIntent:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, **kwargs) -> CompletionResult:
            self.calls += 1
            command = "answer_pending" if self.calls == 1 else "declare_action"
            return CompletionResult(
                (
                    f'{{"command":"{command}","argument":null,"confidence":1,'
                    '"evidence":"replay trap"}'
                ),
                used_tool=True,
            )

    class CountingCompletion:
        def __init__(self, response: str) -> None:
            self.response = response
            self.calls = 0

        async def complete(self, **kwargs) -> CompletionResult:
            self.calls += 1
            return CompletionResult(self.response, used_tool=True)

    store = setup(tmp_path)
    intent = ReplayTrapIntent()
    review = Review(True)
    action = CountingCompletion(
        '{"resolution":"automatic","trait_names":[],"aspect_names":[],'
        '"flag":null,"bonus_ids":[],"difficulty":null,'
        '"evidence":["would duplicate the turn"],"clarification_question":null}'
    )
    consequence = CountingCompletion(
        '{"summary":"The accepted narration becomes canonical",'
        '"add_facts":["The door is open"],"remove_facts":[],'
        '"add_actor_conditions":[],"add_actor_plot_items":[]}'
    )
    application = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(intent),
        player_narration_pipeline=create_player_narration_pipeline(review),
        action_pipeline=create_action_pipeline(action),
        consequence_pipeline=create_consequence_pipeline(consequence),
    )
    narration = IncomingMessage(
        event_id="narration-replay",
        channel_id="game",
        author_id="alice",
        content="I throw the door open and step aside.",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    first = asyncio.run(application(narration))
    replayed = asyncio.run(application(narration))

    assert isinstance(first, HandlerResponse)
    assert isinstance(replayed, HandlerResponse)
    assert replayed.deliveries == first.deliveries
    assert review.calls == 1
    assert intent.calls == 1
    assert action.calls == 0
    assert consequence.calls == 1
    patched = [
        event
        for event in store.recent_domain_events(game_id="game", limit=100)
        if event["event_type"] == "scene_patched"
    ]
    assert len(patched) == 1
    assert patched[0]["causation_id"].startswith("player-narration:")


def test_accepted_narration_reuses_review_after_interrupted_pending_resolution(
    tmp_path,
    monkeypatch,
) -> None:
    class SequenceReview:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, **kwargs) -> CompletionResult:
            self.calls += 1
            if self.calls == 1:
                return CompletionResult(
                    '{"accepted":true,"reason":"within rights",'
                    '"scale_back_request":null,'
                    '"approved_narration":"Approved interrupted narration."}',
                    used_tool=True,
                )
            return CompletionResult(
                '{"accepted":false,"reason":"adversarial retry",'
                '"scale_back_request":"Reject it on retry.","approved_narration":null}',
                used_tool=True,
            )

    class CountingCompletion:
        def __init__(self, response: str) -> None:
            self.response = response
            self.calls = 0

        async def complete(self, **kwargs) -> CompletionResult:
            self.calls += 1
            return CompletionResult(self.response, used_tool=True)

    store = setup(tmp_path)
    review = SequenceReview()
    consequence = CountingCompletion(
        '{"summary":"The accepted narration becomes canonical",'
        '"add_facts":["The door is open"],"remove_facts":[],'
        '"add_actor_conditions":[],"add_actor_plot_items":[]}'
    )
    application = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverIntent()),
        player_narration_pipeline=create_player_narration_pipeline(review),
        consequence_pipeline=create_consequence_pipeline(consequence),
    )
    narration = IncomingMessage(
        event_id="narration-interrupted-before-resolve",
        channel_id="game",
        author_id="alice",
        content="I throw the door open and step aside.",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    narration_pending = store.open_pending(game_id="game", player_id="alice")
    assert narration_pending is not None
    original_resolve = store.resolve_pending
    resolve_attempts = 0

    def interrupt_first_resolution(**kwargs):
        nonlocal resolve_attempts
        resolve_attempts += 1
        if resolve_attempts == 1:
            raise RuntimeError("simulated interruption before pending resolution")
        return original_resolve(**kwargs)

    monkeypatch.setattr(store, "resolve_pending", interrupt_first_resolution)

    with pytest.raises(RuntimeError, match="simulated interruption"):
        asyncio.run(application(narration))

    still_open = store.pending_by_id(narration_pending.interaction_id)
    assert still_open is not None
    assert still_open.status.value == "open"
    assert store.has_scene_patch(f"player-narration:{narration_pending.interaction_id}")

    replayed = asyncio.run(application(narration))
    events = [
        event
        for event in store.recent_domain_events(game_id="game", limit=100)
        if event["event_type"] == "interaction_recorded"
        and event["causation_id"] == f"player-narration:{narration_pending.interaction_id}"
    ]
    patches = [
        event
        for event in store.recent_domain_events(game_id="game", limit=100)
        if event["event_type"] == "scene_patched"
        and event["causation_id"] == f"player-narration:{narration_pending.interaction_id}"
    ]

    assert isinstance(replayed, HandlerResponse)
    assert replayed.deliveries[0].content == "Approved interrupted narration."
    assert review.calls == 1
    assert consequence.calls == 1
    assert resolve_attempts == 2
    assert len(events) == 1
    assert len(patches) == 1
    assert store.open_pending(game_id="game", player_id="alice") is None


@pytest.mark.parametrize("mutation", ["scene", "actor", "move"])
def test_committed_narration_does_not_publish_into_changed_fiction_after_patch_crash(
    tmp_path,
    monkeypatch,
    mutation: str,
) -> None:
    class CountingCompletion:
        def __init__(self, response: str) -> None:
            self.response = response
            self.calls = 0

        async def complete(self, **kwargs) -> CompletionResult:
            self.calls += 1
            return CompletionResult(self.response, used_tool=True)

    store = setup(tmp_path)
    review = Review(True)
    consequence = CountingCompletion(
        '{"summary":"The accepted narration becomes canonical",'
        '"add_facts":["The door is open"],"remove_facts":[],'
        '"add_actor_conditions":[],"add_actor_plot_items":[]}'
    )
    application = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverIntent()),
        player_narration_pipeline=create_player_narration_pipeline(review),
        consequence_pipeline=create_consequence_pipeline(consequence),
    )
    narration = IncomingMessage(
        event_id=f"narration-patch-crash-{mutation}",
        channel_id="game",
        author_id="alice",
        content="I throw the door open and step aside.",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    pending = store.open_pending(game_id="game", player_id="alice")
    assert pending is not None
    causation_id = f"player-narration:{pending.interaction_id}"
    original_apply = store.apply_outcome_patch

    def interrupt_after_patch(**kwargs) -> None:
        original_apply(**kwargs)
        raise RuntimeError("simulated interruption immediately after narration patch")

    monkeypatch.setattr(store, "apply_outcome_patch", interrupt_after_patch)

    with pytest.raises(RuntimeError, match="immediately after narration patch"):
        asyncio.run(application(narration))

    assert store.has_scene_patch(causation_id)
    assert store.open_pending(game_id="game", player_id="alice") is not None
    if mutation == "scene":
        room = store.scene_by_id(game_id="game", scene_id="room")
        assert room is not None
        store.apply_scene_patch(
            game_id="game",
            scene_id="room",
            expected_revision=int(room["scene_revision"]),
            causation_id="external:changed-room-after-narration",
            summary="Another channel changes the room.",
            add_facts=["A warning bell is ringing"],
            remove_facts=[],
        )
    elif mutation == "actor":
        with store.transaction() as connection:
            connection.execute(
                """UPDATE characters SET revision = revision + 1
                   WHERE character_id = ?""",
                ("hero",),
            )
    else:
        store.create_scene(scene_id="hall", game_id="game", title="Hall")
        store.place_player(game_id="game", player_id="alice", scene_id="hall")

    replayed = asyncio.run(application(narration))
    replayed_again = asyncio.run(application(narration))
    interaction_events = [
        event
        for event in store.recent_domain_events(game_id="game", limit=100)
        if event["event_type"] == "interaction_recorded" and event["causation_id"] == causation_id
    ]
    patch_events = [
        event
        for event in store.recent_domain_events(game_id="game", limit=100)
        if event["event_type"] == "scene_patched" and event["causation_id"] == causation_id
    ]
    closed = store.pending_by_id(pending.interaction_id)

    assert isinstance(replayed, str)
    assert replayed == replayed_again
    assert tr("ru", "player_narration_committed_context_changed") in replayed
    assert review.calls == 1
    assert consequence.calls == 1
    assert interaction_events == []
    assert len(patch_events) == 1
    assert closed is not None
    assert closed.status.value == "resolved"
    assert "answer" not in closed.payload
    assert closed.payload["closed_by_event_id"] == narration.event_id


def test_invalid_consequence_keeps_accepted_narration_pending_without_system_failure(
    tmp_path,
) -> None:
    class InvalidConsequence:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, **kwargs) -> CompletionResult:
            self.calls += 1
            return CompletionResult("not valid JSON", used_tool=True)

    store = setup(tmp_path)
    review = Review(True)
    consequence = InvalidConsequence()
    application = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverIntent()),
        player_narration_pipeline=create_player_narration_pipeline(review),
        consequence_pipeline=create_consequence_pipeline(consequence),
    )
    pending = store.open_pending(game_id="game", player_id="alice")
    assert pending is not None

    result = asyncio.run(
        application(
            IncomingMessage(
                event_id="narration-invalid-consequence",
                channel_id="game",
                author_id="alice",
                content="I throw the door open and step aside.",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    current = store.open_pending(game_id="game", player_id="alice")
    assert isinstance(result, str)
    assert tr("ru", "player_narration_consequence_retry") in result
    assert tr("ru", "system_failure") not in result
    assert current is not None
    assert current.interaction_id == pending.interaction_id
    assert consequence.calls == 2
    assert review.calls == 1
    assert not store.has_scene_patch(f"player-narration:{pending.interaction_id}")
    assert not [
        event
        for event in store.recent_domain_events(game_id="game", limit=100)
        if event["event_type"] == "interaction_recorded"
        and event["causation_id"] == f"player-narration:{pending.interaction_id}"
    ]


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


def test_obvious_new_turn_is_not_published_as_the_old_roll_outcome(tmp_path) -> None:
    store = setup(tmp_path)

    result = asyncio.run(
        app(store, True)(
            IncomingMessage.now(
                event_id="new-turn-instead-of-narration",
                channel_id="game",
                author_id="alice",
                content="Теперь открываю сейф.",
            )
        )
    )

    assert isinstance(result, str)
    assert "незавершённый шаг" in result.lower()
    assert store.open_pending(game_id="game", player_id="alice").kind is (
        PendingKind.PLAYER_NARRATION
    )


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


def test_stale_player_narration_lookup_is_read_only_until_dispatch(tmp_path) -> None:
    store = setup(tmp_path)
    interaction_id = make_pending_stale(store)

    pending = store.open_pending(game_id="game", player_id="alice")

    assert pending is not None
    assert pending.interaction_id == interaction_id
    assert store.pending_by_id(interaction_id).status.value == "open"


def test_fresh_player_narration_can_be_cancelled_without_review(tmp_path) -> None:
    store = setup_cancellable_pending(tmp_path)

    result = asyncio.run(
        app(store, True)(
            IncomingMessage(
                event_id="cancel-narration",
                channel_id="game",
                author_id="alice",
                content="отмена",
                created_at=datetime.now(UTC),
            )
        )
    )

    assert "наррация результата отменена" in result.lower()
    assert store.open_pending(game_id="game", player_id="alice") is None
