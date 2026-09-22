import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from masterclaw.app.action_service import ActionService
from masterclaw.app.i18n import tr
from masterclaw.app.message_handler import MessageApplication
from masterclaw.app.orchestrator import ChannelOrchestrator
from masterclaw.app.response_format import format_roll_result
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.characters import CharacterState, Condition, PlotItem
from masterclaw.domain.mechanics import (
    CharacterSheet,
    Flag,
    FlagType,
    OutcomeAuthority,
    PoolProposal,
    Trait,
)
from masterclaw.domain.models import GameLifecycle, HandlerResponse, IncomingMessage
from masterclaw.domain.outcomes import CanonicalOutcomePatch
from masterclaw.domain.state import GameState, WorldState
from masterclaw.domain.text_safety import contains_secret_fragment, secret_fact_catalog
from masterclaw.pipelines.action import create_action_pipeline
from masterclaw.pipelines.base import (
    CompletionResult,
    TransientProviderError,
)
from masterclaw.pipelines.consequence import create_consequence_pipeline
from masterclaw.pipelines.narrative import create_narrative_pipeline
from masterclaw.pipelines.reserve_recovery import create_reserve_recovery_pipeline
from masterclaw.pipelines.state_decision import StateDecisionRouter
from masterclaw.storage.sqlite import SQLiteStore


class Completion:
    def __init__(self, response):
        self.response = response
        self.contexts: list[str] = []

    async def complete(self, **kwargs) -> CompletionResult:
        self.contexts.append(kwargs["context"])
        return CompletionResult(self.response, used_tool=True)


class InvalidCompletion:
    async def complete(self, **kwargs) -> CompletionResult:
        return CompletionResult("not valid JSON", used_tool=True)


class SequenceCompletion:
    def __init__(self, *responses: str) -> None:
        self.responses = iter(responses)
        self.calls = 0

    async def complete(self, **kwargs) -> CompletionResult:
        self.calls += 1
        return CompletionResult(next(self.responses), used_tool=True)


def setup(tmp_path):
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(
        GameState("game", "world", GameLifecycle.ACTIVE, narrative_channel_id="narrative")
    )
    store.bind_channel(channel_id="game", game_id="game")
    store.create_scene(
        scene_id="room",
        game_id="game",
        title="Room",
        state={"facts": ["The door is closed"]},
    )
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
    store.create_character(
        CharacterState(
            "hero",
            "game",
            "alice",
            "Bio",
            sheet,
            conditions=(Condition("Pinned", "falling stones"),),
            plot_items=(PlotItem("Old token"),),
        )
    )
    return store


def test_automatic_action_applies_typed_scene_patch_before_narrative(tmp_path) -> None:
    store = setup(tmp_path)
    narrative_completion = Completion('{"narrative":"Дверь бесшумно открывается."}')
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"declare_action","argument":null,"confidence":1,'
                '"evidence":"opens door"}'
            )
        ),
        action_pipeline=create_action_pipeline(
            Completion(
                '{"resolution":"automatic","trait_names":[],"aspect_names":[],'
                '"flag":null,"difficulty":null,"evidence":["door is unlocked"],'
                '"clarification_question":null}'
            )
        ),
        consequence_pipeline=create_consequence_pipeline(
            Completion(
                '{"summary":"The door opens","add_facts":["The door is open"],'
                '"remove_facts":["The door is closed"],'
                '"add_actor_conditions":[{"text":"Focused","source":"opened door"}],'
                '"add_actor_plot_items":[{"name":"Brass key",'
                '"description":"Opens the archive lift"}]}'
            )
        ),
        narrative_pipeline=create_narrative_pipeline(narrative_completion),
    )
    result = asyncio.run(
        app(
            IncomingMessage(
                event_id="action",
                channel_id="game",
                author_id="alice",
                content="Я открываю незапертую дверь.",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )
    assert isinstance(result, HandlerResponse)
    scene = store.scene_projection(game_id="game", player_id="alice")
    assert scene["state"]["facts"] == ["The door is open"]
    assert scene["scene_revision"] == 1
    assert '"conditions":["Pinned","Focused"]' in narrative_completion.contexts[0]
    assert '"revision":1' in narrative_completion.contexts[0]
    assert '"name":"Brass key"' in narrative_completion.contexts[0]
    assert '"description":"Opens the archive lift"' in narrative_completion.contexts[0]


def test_automatic_action_skips_stale_prose_after_scene_changes_during_recovery(
    tmp_path,
) -> None:
    store = setup(tmp_path)

    class MutatingRecovery:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, **kwargs) -> CompletionResult:
            self.calls += 1
            room = store.scene_by_id(game_id="game", scene_id="room")
            assert room is not None
            store.apply_scene_patch(
                game_id="game",
                scene_id="room",
                expected_revision=int(room["scene_revision"]),
                causation_id="external:during-automatic-recovery",
                summary="Another channel changes the scene during recovery.",
                add_facts=["A warning bell is ringing"],
                remove_facts=[],
            )
            return CompletionResult(
                '{"safe_rest_completed":false,"safe_rest_reason":null,"awards":[]}',
                used_tool=True,
            )

    recovery = MutatingRecovery()
    narrative = Completion('{"narrative":"Stale prose must never be used."}')
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"declare_action","argument":null,"confidence":1,'
                '"evidence":"opens door"}'
            )
        ),
        action_pipeline=create_action_pipeline(
            Completion(
                '{"resolution":"automatic","trait_names":[],"aspect_names":[],'
                '"flag":null,"difficulty":null,"evidence":["door is unlocked"],'
                '"clarification_question":null}'
            )
        ),
        consequence_pipeline=create_consequence_pipeline(
            Completion(
                '{"summary":"The door opens","add_facts":["The door is open"],'
                '"remove_facts":["The door is closed"]}'
            )
        ),
        reserve_recovery_pipeline=create_reserve_recovery_pipeline(recovery),
        narrative_pipeline=create_narrative_pipeline(narrative),
    )

    result = asyncio.run(
        app(
            IncomingMessage(
                event_id="automatic-recovery-race",
                channel_id="game",
                author_id="alice",
                content="I open the unlocked door.",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    assert isinstance(result, HandlerResponse)
    assert recovery.calls == 1
    assert narrative.contexts == []
    assert result.deliveries[0].content == tr("ru", "narrative_fallback")
    scene = store.scene_projection(game_id="game", player_id="alice")
    assert scene is not None
    assert scene["state"]["facts"] == [
        "The door is open",
        "A warning bell is ringing",
    ]


def test_reserve_recovery_decision_is_checkpointed_after_canonical_patch(tmp_path) -> None:
    store = setup(tmp_path)
    bob_sheet = CharacterSheet(
        "Bob",
        tuple(Trait(f"B{i}", 3, ()) for i in range(6)),
        (
            Flag("Friend", FlagType.RELATIONSHIP),
            Flag("Goal", FlagType.GOAL),
            Flag("Belief", FlagType.BELIEF),
        ),
    )
    store.create_character(CharacterState("bob", "game", "bob", "Bio", bob_sheet))
    store.place_player(game_id="game", player_id="bob", scene_id="room")
    consequence = Completion(
        '{"summary":"The group catches its breath","add_facts":["The door is open"],'
        '"remove_facts":["The door is closed"]}'
    )
    recovery = SequenceCompletion(
        '{"safe_rest_completed":false,"safe_rest_reason":null,'
        '"awards":[{"player_id":"alice","reason":"Alice made a specific costly choice."}]}',
        '{"safe_rest_completed":false,"safe_rest_reason":null,'
        '"awards":[{"player_id":"bob","reason":"A contradictory retry targets Bob instead."}]}',
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(Completion("unused")),
        consequence_pipeline=create_consequence_pipeline(consequence),
        reserve_recovery_pipeline=create_reserve_recovery_pipeline(recovery),
    )
    scene = store.scene_projection(game_id="game", player_id="alice")
    assert scene is not None

    for _ in range(2):
        asyncio.run(
            app._ensure_scene_consequence(
                game_id="game",
                scene=scene,
                causation_id="automatic:recovery-checkpoint",
                outcome_source={"kind": "automatic", "declaration": "I open the door."},
                narrator_rights=OutcomeAuthority.GM_AUTOMATIC.value,
                player_id="alice",
            )
        )

    checkpoint = store.reserve_recovery_decision("automatic:recovery-checkpoint")
    awards = [
        event
        for event in store.recent_domain_events(game_id="game", limit=20)
        if event["event_type"] == "reserve_roleplay_award"
    ]
    assert recovery.calls == 1
    assert checkpoint is not None
    assert checkpoint["awards"] == [
        {
            "player_id": "alice",
            "reason": "Alice made a specific costly choice.",
        }
    ]
    assert [event["payload"]["player_id"] for event in awards] == ["alice"]


def test_automatic_action_decisions_cannot_switch_to_roll_on_direct_retry(tmp_path) -> None:
    store = setup(tmp_path)
    states = SequenceCompletion(
        '{"command":"declare_action","argument":null,"confidence":1,"evidence":"opens the door"}',
        '{"command":"player_narration","argument":null,"confidence":1,'
        '"evidence":"adversarial retry branch"}',
    )
    actions = SequenceCompletion(
        '{"resolution":"automatic","trait_names":[],"aspect_names":[],"flag":null,'
        '"bonus_ids":[],"difficulty":null,"evidence":["door is unlocked"],'
        '"clarification_question":null,"rejection_reason":null}',
        '{"resolution":"roll","trait_names":["T0"],"aspect_names":["A0.0"],'
        '"flag":null,"bonus_ids":[],"difficulty":2,"evidence":["retry changes branch"],'
        '"clarification_question":null,"rejection_reason":null}',
    )
    consequence = SequenceCompletion(
        '{"summary":"The door opens","add_facts":["The door is open"],'
        '"remove_facts":["The door is closed"]}'
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(states),
        action_pipeline=create_action_pipeline(actions),
        consequence_pipeline=create_consequence_pipeline(consequence),
    )
    event = IncomingMessage(
        event_id="automatic-checkpoint",
        channel_id="game",
        author_id="alice",
        content="I open the unlocked door.",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    first = asyncio.run(app(event))
    replayed = asyncio.run(app(event))
    scene = store.scene_projection(game_id="game", player_id="alice")

    assert replayed == first
    assert states.calls == 1
    assert actions.calls == 1
    assert consequence.calls == 1
    assert scene is not None
    assert scene["scene_revision"] == 1
    assert scene["state"]["facts"] == ["The door is open"]
    assert store.open_pending(game_id="game", player_id="alice") is None


def test_consequence_checkpoint_fails_closed_if_bound_revisions_change(tmp_path) -> None:
    store = setup(tmp_path)
    consequence = SequenceCompletion(
        '{"summary":"The door opens","add_facts":["The door is open"],'
        '"remove_facts":["The door is closed"]}'
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(Completion("unused")),
        consequence_pipeline=create_consequence_pipeline(consequence),
    )
    scene = store.scene_projection(game_id="game", player_id="alice")
    character = store.character_for_player(game_id="game", player_id="alice")
    assert scene is not None
    assert character is not None
    prepared = asyncio.run(
        app._prepare_scene_consequence(
            game_id="game",
            scene=scene,
            causation_id="automatic:interrupted-before-apply",
            outcome_source={"kind": "automatic", "declaration": "I open the door."},
            narrator_rights=OutcomeAuthority.GM_AUTOMATIC.value,
            player_id="alice",
        )
    )
    assert prepared is not None

    store.apply_outcome_patch(
        game_id="game",
        scene_id=str(scene["scene_id"]),
        expected_scene_revision=int(scene["scene_revision"]),
        actor_character_id=character.character_id,
        expected_actor_revision=character.revision,
        expected_actor_location_revision=int(scene["location_revision"]),
        expected_scene_participants=tuple(scene["participants"]),
        causation_id="external:scene-change",
        patch=CanonicalOutcomePatch(
            summary="Another channel changes the scene first.",
            add_facts=("A warning bell is ringing",),
        ),
    )
    changed_scene = store.scene_projection(game_id="game", player_id="alice")
    assert changed_scene is not None

    with pytest.raises(RuntimeError, match="input fingerprint mismatch"):
        asyncio.run(
            app._prepare_scene_consequence(
                game_id="game",
                scene=changed_scene,
                causation_id="automatic:interrupted-before-apply",
                outcome_source={"kind": "automatic", "declaration": "I open the door."},
                narrator_rights=OutcomeAuthority.GM_AUTOMATIC.value,
                player_id="alice",
            )
        )

    assert consequence.calls == 1
    assert not store.has_scene_patch("automatic:interrupted-before-apply")


def test_automatic_action_exact_result_is_journaled_before_outbox_completion(
    tmp_path,
    monkeypatch,
) -> None:
    store = setup(tmp_path)
    states = SequenceCompletion(
        '{"command":"declare_action","argument":null,"confidence":1,"evidence":"opens the door"}',
        '{"command":"player_narration","argument":null,"confidence":1,'
        '"evidence":"adversarial retry branch"}',
    )
    actions = SequenceCompletion(
        '{"resolution":"automatic","trait_names":[],"aspect_names":[],"flag":null,'
        '"bonus_ids":[],"difficulty":null,"evidence":["door is unlocked"],'
        '"clarification_question":null,"rejection_reason":null}',
        '{"resolution":"roll","trait_names":["T0"],"aspect_names":["A0.0"],'
        '"flag":null,"bonus_ids":[],"difficulty":2,"evidence":["retry changes branch"],'
        '"clarification_question":null,"rejection_reason":null}',
    )
    consequence = SequenceCompletion(
        '{"summary":"The door opens","add_facts":["The door is open"],'
        '"remove_facts":["The door is closed"]}'
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(states),
        action_pipeline=create_action_pipeline(actions),
        consequence_pipeline=create_consequence_pipeline(consequence),
    )
    event = IncomingMessage(
        event_id="automatic-result-journal",
        channel_id="game",
        author_id="alice",
        content="I open the unlocked door.",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert store.enqueue(event)
    original_complete = store.complete_batch
    completion_attempts = 0
    handler_calls = 0

    def fail_first_completion(**kwargs):
        nonlocal completion_attempts
        completion_attempts += 1
        if completion_attempts == 1:
            raise RuntimeError("simulated completion failure")
        return original_complete(**kwargs)

    monkeypatch.setattr(store, "complete_batch", fail_first_completion)

    async def recording_handler(message: IncomingMessage):
        nonlocal handler_calls
        handler_calls += 1
        return await app(message)

    orchestrator = ChannelOrchestrator(store, recording_handler)
    with pytest.raises(RuntimeError, match="simulated completion failure"):
        asyncio.run(orchestrator.process_available("game"))
    journaled = store.handler_result(event.event_id)
    replayed = asyncio.run(orchestrator.process_available("game"))
    scene = store.scene_projection(game_id="game", player_id="alice")

    assert journaled is not None
    assert replayed is not None
    assert replayed.items[0].text == journaled["text"]
    assert handler_calls == 1
    assert completion_attempts == 2
    assert states.calls == 1
    assert actions.calls == 1
    assert consequence.calls == 1
    assert scene is not None
    assert scene["scene_revision"] == 1
    assert store.open_pending(game_id="game", player_id="alice") is None


def test_automatic_action_reuses_typed_decisions_after_handler_is_interrupted(
    tmp_path,
    monkeypatch,
) -> None:
    """A kill inside the handler must not let the retry choose another valid branch."""

    store = setup(tmp_path)
    states = SequenceCompletion(
        '{"command":"declare_action","argument":null,"confidence":1,"evidence":"opens the door"}',
        '{"command":"player_narration","argument":null,"confidence":1,'
        '"evidence":"adversarial retry branch"}',
    )
    actions = SequenceCompletion(
        '{"resolution":"automatic","trait_names":[],"aspect_names":[],"flag":null,'
        '"bonus_ids":[],"difficulty":null,"evidence":["door is unlocked"],'
        '"clarification_question":null,"rejection_reason":null}',
        '{"resolution":"roll","trait_names":["T0"],"aspect_names":["A0.0"],'
        '"flag":null,"bonus_ids":[],"difficulty":2,"evidence":["retry changes branch"],'
        '"clarification_question":null,"rejection_reason":null}',
    )
    consequence = SequenceCompletion(
        '{"summary":"The door opens","add_facts":["The door is open"],'
        '"remove_facts":["The door is closed"]}'
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(states),
        action_pipeline=create_action_pipeline(actions),
        consequence_pipeline=create_consequence_pipeline(consequence),
    )
    event = IncomingMessage(
        event_id="automatic-handler-interruption",
        channel_id="game",
        author_id="alice",
        content="I open the unlocked door.",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert store.enqueue(event)
    original_recovery = app._consider_reserve_recovery
    recovery_attempts = 0
    handler_calls = 0

    async def interrupt_after_patch(**kwargs) -> None:
        nonlocal recovery_attempts
        recovery_attempts += 1
        if recovery_attempts == 1:
            raise RuntimeError("simulated process interruption after outcome patch")
        await original_recovery(**kwargs)

    monkeypatch.setattr(app, "_consider_reserve_recovery", interrupt_after_patch)

    async def recording_handler(message: IncomingMessage):
        nonlocal handler_calls
        handler_calls += 1
        return await app(message)

    orchestrator = ChannelOrchestrator(store, recording_handler)
    with pytest.raises(RuntimeError, match="simulated process interruption"):
        asyncio.run(orchestrator.process_available("game"))

    assert store.has_scene_patch("automatic:automatic-handler-interruption")
    assert store.handler_result(event.event_id) is None

    completed = asyncio.run(orchestrator.process_available("game"))
    scene = store.scene_projection(game_id="game", player_id="alice")
    with store.connect() as connection:
        inbox = connection.execute(
            "SELECT status, attempts FROM inbox_messages WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()

    assert completed is not None
    assert handler_calls == 2
    assert recovery_attempts == 2
    assert states.calls == 1
    assert actions.calls == 1
    assert consequence.calls == 1
    assert scene is not None
    assert scene["scene_revision"] == 1
    assert scene["state"]["facts"] == ["The door is open"]
    assert store.open_pending(game_id="game", player_id="alice") is None
    assert store.handler_result(event.event_id) is not None
    assert inbox is not None
    assert inbox["status"] == "processed"
    assert inbox["attempts"] == 2


def test_secret_fragment_in_action_clarification_is_replaced(tmp_path) -> None:
    secret = "The bell keeper is the storm's forgotten name."
    store = setup(tmp_path)
    store.update_world_content(
        world_id="world",
        expected_revision=0,
        content={"secret_plot": secret},
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"declare_action","argument":null,"confidence":1,'
                '"evidence":"ambiguous action"}'
            )
        ),
        action_pipeline=create_action_pipeline(
            Completion(
                '{"resolution":"clarification","trait_names":[],"aspect_names":[],'
                '"flag":null,"bonus_ids":[],"difficulty":null,"evidence":[],'
                '"clarification_question":"THE BELL-KEEPER is the storm’s forgotten NAME"}'
            )
        ),
    )

    result = asyncio.run(
        app(
            IncomingMessage(
                event_id="secret-clarification",
                channel_id="game",
                author_id="alice",
                content="I inspect it.",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    assert not contains_secret_fragment(result, secret)


def test_secret_fragment_in_public_patch_is_rejected_without_mutation(tmp_path) -> None:
    secret = "The bell keeper is the storm's forgotten name."
    store = setup(tmp_path)
    store.update_world_content(
        world_id="world",
        expected_revision=0,
        content={"secret_plot": secret},
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"declare_action","argument":null,"confidence":1,'
                '"evidence":"opens door"}'
            )
        ),
        action_pipeline=create_action_pipeline(
            Completion(
                '{"resolution":"automatic","trait_names":[],"aspect_names":[],'
                '"flag":null,"bonus_ids":[],"difficulty":null,'
                '"evidence":["door is unlocked"],"clarification_question":null}'
            )
        ),
        consequence_pipeline=create_consequence_pipeline(
            Completion(
                '{"summary":"THE BELL-KEEPER is the storm’s forgotten NAME",'
                '"add_facts":["The door is open"],"remove_facts":["The door is closed"]}'
            )
        ),
    )

    activity_before = store.activity_state("game")
    result = asyncio.run(
        app(
            IncomingMessage(
                event_id="secret-patch",
                channel_id="game",
                author_id="alice",
                content="I open the unlocked door.",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    assert not store.has_scene_patch("automatic:secret-patch")
    assert store.scene_projection(game_id="game", player_id="alice")["state"]["facts"] == [
        "The door is closed"
    ]
    assert store.activity_state("game") == activity_before
    assert not contains_secret_fragment(result, secret)


def test_secret_reveal_requires_deterministic_allowlist_and_keeps_the_rest_hidden(
    tmp_path,
) -> None:
    secret = "The bell keeper is the storm's forgotten name. The bronze key wakes it at midnight."
    first_secret, second_secret = secret_fact_catalog(secret)
    store = setup(tmp_path)
    store.update_world_content(
        world_id="world",
        expected_revision=0,
        content={"secret_plot": secret},
    )

    def application(consequence_response: str) -> MessageApplication:
        return MessageApplication(
            store=store,
            context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
            state_router=StateDecisionRouter(
                Completion(
                    '{"command":"declare_action","argument":null,"confidence":1,'
                    '"evidence":"resolved investigation"}'
                )
            ),
            action_pipeline=create_action_pipeline(
                Completion(
                    '{"resolution":"automatic","trait_names":[],"aspect_names":[],'
                    '"flag":null,"bonus_ids":[],"difficulty":null,'
                    '"evidence":["the archive record is already decoded"],'
                    '"clarification_question":null,"rejection_reason":null}'
                )
            ),
            consequence_pipeline=create_consequence_pipeline(Completion(consequence_response)),
        )

    blocked_result = asyncio.run(
        application(
            '{"summary":"The decoded record establishes the keeper identity",'
            f'"reveal_secret_ids":["{first_secret.secret_id}"]}}'
        )(
            IncomingMessage(
                event_id="model-only-reveal",
                channel_id="game",
                author_id="alice",
                content="I read the already decoded archive record.",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    assert not store.has_scene_patch("automatic:model-only-reveal")
    assert first_secret.secret_id not in store.revealed_secret_ids("game")
    assert not contains_secret_fragment(blocked_result, secret)

    authorized = application(
        '{"summary":"The decoded record establishes the keeper identity",'
        f'"reveal_secret_ids":["{first_secret.secret_id}"]}}'
    )
    scene = store.scene_projection(game_id="game", player_id="alice")
    asyncio.run(
        authorized._ensure_scene_consequence(
            game_id="game",
            scene=scene,
            causation_id="deterministic-secret-discovery",
            outcome_source={
                "kind": "deterministic_secret_discovery",
                "allowed_secret_ids": [first_secret.secret_id],
            },
            narrator_rights=OutcomeAuthority.GM_AUTOMATIC.value,
            player_id="alice",
        )
    )

    assert first_secret.secret_id in store.revealed_secret_ids("game")
    scene = store.scene_projection(game_id="game", player_id="alice")
    assert first_secret.text in scene["state"]["facts"]
    assert second_secret.secret_id not in store.revealed_secret_ids("game")


def test_scene_patch_cannot_remove_unknown_fact(tmp_path) -> None:
    store = setup(tmp_path)
    with pytest.raises(ValueError, match="unknown scene facts"):
        store.apply_scene_patch(
            game_id="game",
            scene_id="room",
            expected_revision=0,
            causation_id="bad",
            summary="bad",
            add_facts=[],
            remove_facts=["Unknown"],
        )


def test_committed_roll_returns_mechanics_when_consequence_pipeline_is_invalid(
    tmp_path,
) -> None:
    store = setup(tmp_path)
    pending = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="room",
        proposal=PoolProposal(
            trait_names=("T0",),
            aspect_names=("A0.0",),
            difficulty=2,
        ),
        prompt="Confirm",
        declaration="Open the door",
    )
    roll = ActionService(store).confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        confirmation_event_id="confirmed-roll",
        die=iter((1, 1)).__next__,
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion('{"command":"clarify","argument":null,"confidence":0,"evidence":"unused"}')
        ),
        consequence_pipeline=create_consequence_pipeline(InvalidCompletion()),
        narrative_pipeline=create_narrative_pipeline(
            Completion('{"narrative":"This must not be reached."}')
        ),
    )

    result = asyncio.run(
        app(
            IncomingMessage(
                event_id="confirmed-roll",
                channel_id="game",
                author_id="alice",
                content="0",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    mechanical = format_roll_result(
        dice=roll.dice,
        hits=roll.hits,
        difficulty=roll.difficulty,
        rights=roll.narrator_rights.value,
        reserve=roll.reserve_after,
        locale="ru",
    )
    assert isinstance(result, str)
    assert mechanical in result
    assert tr("ru", "committed_roll_consequence_skipped") in result
    assert tr("ru", "system_failure") not in result
    assert not store.has_scene_patch(f"roll:{roll.roll_id}")
    assert "This must not be reached." not in result


def test_committed_roll_does_not_apply_consequence_to_a_new_scene(tmp_path) -> None:
    store = setup(tmp_path)
    store.create_scene(scene_id="archive", game_id="game", title="Archive")
    pending = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="room",
        proposal=PoolProposal(
            trait_names=("T0",),
            aspect_names=("A0.0",),
            difficulty=2,
        ),
        prompt="Confirm",
        declaration="Open the door",
    )
    roll = ActionService(store).confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        confirmation_event_id="moved-after-roll",
        die=iter((4, 4)).__next__,
    )
    consequence = Completion(
        '{"summary":"The door opens","add_facts":["The door is open"],'
        '"remove_facts":["The door is closed"]}'
    )
    store.place_player(
        game_id="game",
        player_id="alice",
        scene_id="archive",
        expected_revision=0,
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion('{"command":"clarify","argument":null,"confidence":0,"evidence":"unused"}')
        ),
        consequence_pipeline=create_consequence_pipeline(consequence),
    )

    result = asyncio.run(
        app(
            IncomingMessage(
                event_id="moved-after-roll",
                channel_id="game",
                author_id="alice",
                content="0",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    assert isinstance(result, str)
    assert tr("ru", "committed_roll_consequence_skipped") in result
    assert consequence.contexts == []
    assert not store.has_scene_patch(f"roll:{roll.roll_id}")
    assert store.scene_by_id(game_id="game", scene_id="room")["state"]["facts"] == [
        "The door is closed"
    ]
    assert store.scene_by_id(game_id="game", scene_id="archive")["state"].get("facts", []) == []


def test_committed_roll_does_not_narrate_from_live_scene_after_patch_crash(
    tmp_path,
    monkeypatch,
) -> None:
    store = setup(tmp_path)
    store.create_scene(scene_id="archive", game_id="game", title="Archive")
    pending = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="room",
        proposal=PoolProposal(
            trait_names=("T0",),
            aspect_names=("A0.0",),
            difficulty=2,
        ),
        prompt="Confirm",
        declaration="Open the door",
    )
    roll = ActionService(store).confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        confirmation_event_id="roll-patch-crash",
        die=iter((4, 4)).__next__,
    )
    consequence = SequenceCompletion(
        '{"summary":"The door opens","add_facts":["The door is open"],'
        '"remove_facts":["The door is closed"]}'
    )
    narrative = Completion('{"narrative":"The old room description must not be replayed."}')
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion('{"command":"clarify","argument":null,"confidence":0,"evidence":"unused"}')
        ),
        consequence_pipeline=create_consequence_pipeline(consequence),
        narrative_pipeline=create_narrative_pipeline(narrative),
    )
    original_recovery = app._consider_reserve_recovery
    recovery_calls = 0

    async def crash_after_patch(**kwargs) -> None:
        nonlocal recovery_calls
        recovery_calls += 1
        if recovery_calls == 1:
            raise RuntimeError("simulated crash after committed roll patch")
        await original_recovery(**kwargs)

    monkeypatch.setattr(app, "_consider_reserve_recovery", crash_after_patch)
    incoming = IncomingMessage(
        event_id="roll-patch-crash",
        channel_id="game",
        author_id="alice",
        content="0",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    with pytest.raises(RuntimeError, match="simulated crash"):
        asyncio.run(app(incoming))
    assert store.has_scene_patch(f"roll:{roll.roll_id}")
    store.place_player(
        game_id="game",
        player_id="alice",
        scene_id="archive",
        expected_revision=0,
    )

    replay = asyncio.run(app(incoming))

    assert isinstance(replay, str)
    assert tr("ru", "committed_roll_consequence_saved_prose_skipped") in replay
    assert consequence.calls == 1
    assert narrative.contexts == []
    assert "old room description" not in replay


def test_committed_roll_skips_stale_prose_after_scene_changes_during_recovery(
    tmp_path,
) -> None:
    store = setup(tmp_path)
    pending = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="room",
        proposal=PoolProposal(
            trait_names=("T0",),
            aspect_names=("A0.0",),
            difficulty=2,
        ),
        prompt="Confirm",
        declaration="Open the door",
    )
    roll = ActionService(store).confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        confirmation_event_id="roll-recovery-race",
        die=iter((4, 4)).__next__,
    )

    class MutatingRecovery:
        async def complete(self, **kwargs) -> CompletionResult:
            room = store.scene_by_id(game_id="game", scene_id="room")
            assert room is not None
            store.apply_scene_patch(
                game_id="game",
                scene_id="room",
                expected_revision=int(room["scene_revision"]),
                causation_id="external:during-roll-recovery",
                summary="Another channel changes the room during recovery.",
                add_facts=["A warning bell is ringing"],
                remove_facts=[],
            )
            return CompletionResult(
                '{"safe_rest_completed":false,"safe_rest_reason":null,"awards":[]}',
                used_tool=True,
            )

    narrative = Completion('{"narrative":"Stale roll prose must never be used."}')
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion('{"command":"clarify","argument":null,"confidence":0,"evidence":"unused"}')
        ),
        consequence_pipeline=create_consequence_pipeline(
            Completion(
                '{"summary":"The door opens","add_facts":["The door is open"],'
                '"remove_facts":["The door is closed"]}'
            )
        ),
        reserve_recovery_pipeline=create_reserve_recovery_pipeline(MutatingRecovery()),
        narrative_pipeline=create_narrative_pipeline(narrative),
    )

    result = asyncio.run(
        app(
            IncomingMessage(
                event_id="roll-recovery-race",
                channel_id="game",
                author_id="alice",
                content="0",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    assert isinstance(result, str)
    assert tr("ru", "committed_roll_consequence_saved_prose_skipped") in result
    assert narrative.contexts == []
    assert store.has_scene_patch(f"roll:{roll.roll_id}")


def test_committed_gm_roll_applies_consequence_without_a_narrative_channel(tmp_path) -> None:
    store = setup(tmp_path)
    with store.transaction() as connection:
        connection.execute("UPDATE games SET narrative_channel_id = NULL WHERE game_id = 'game'")
    pending = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="room",
        proposal=PoolProposal(
            trait_names=("T0",),
            aspect_names=("A0.0",),
            difficulty=2,
        ),
        prompt="Confirm",
        declaration="Open the door",
    )
    roll = ActionService(store).confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        confirmation_event_id="gm-no-narrative",
        die=iter((4, 4)).__next__,
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion('{"command":"clarify","argument":null,"confidence":0,"evidence":"unused"}')
        ),
        consequence_pipeline=create_consequence_pipeline(
            Completion(
                '{"summary":"The door opens","add_facts":["The door is open"],'
                '"remove_facts":["The door is closed"]}'
            )
        ),
    )

    result = asyncio.run(
        app(
            IncomingMessage(
                event_id="gm-no-narrative",
                channel_id="game",
                author_id="alice",
                content="0",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    assert isinstance(result, str)
    assert store.has_scene_patch(f"roll:{roll.roll_id}")
    assert store.scene_by_id(game_id="game", scene_id="room")["state"]["facts"] == [
        "The door is open"
    ]


def test_transient_consequence_failure_retries_same_committed_roll(tmp_path) -> None:
    class FlakyConsequence:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, **kwargs) -> CompletionResult:
            self.calls += 1
            if self.calls == 1:
                raise TransientProviderError("temporary provider outage")
            return CompletionResult(
                '{"summary":"The door opens","add_facts":["The door is open"],'
                '"remove_facts":["The door is closed"]}',
                used_tool=True,
            )

    store = setup(tmp_path)
    pending = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="room",
        proposal=PoolProposal(
            trait_names=("T0",),
            aspect_names=("A0.0",),
            difficulty=2,
        ),
        prompt="Confirm",
        declaration="Open the door",
    )
    roll = ActionService(store).confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        confirmation_event_id="retry-consequence",
        die=iter((4, 4)).__next__,
    )
    flaky = FlakyConsequence()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion('{"command":"clarify","argument":null,"confidence":0,"evidence":"unused"}')
        ),
        consequence_pipeline=create_consequence_pipeline(flaky),
    )
    message = IncomingMessage(
        event_id="retry-consequence",
        channel_id="game",
        author_id="alice",
        content="0",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    with pytest.raises(TransientProviderError):
        asyncio.run(app(message))
    assert not store.has_scene_patch(f"roll:{roll.roll_id}")

    result = asyncio.run(app(message))
    assert isinstance(result, str)
    assert flaky.calls == 2
    assert store.has_scene_patch(f"roll:{roll.roll_id}")


def test_final_transient_consequence_attempt_returns_committed_mechanics(
    tmp_path,
) -> None:
    class AlwaysUnavailable:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, **kwargs) -> CompletionResult:
            self.calls += 1
            raise TransientProviderError("provider remains unavailable")

    store = setup(tmp_path)
    pending = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="room",
        proposal=PoolProposal(
            trait_names=("T0",),
            aspect_names=("A0.0",),
            difficulty=2,
        ),
        prompt="Confirm",
        declaration="Open the door",
    )
    roll = ActionService(store).confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        confirmation_event_id="exhausted-consequence",
        die=iter((4, 4)).__next__,
    )
    event = IncomingMessage(
        event_id="exhausted-consequence",
        channel_id="game",
        author_id="alice",
        content="0",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert store.enqueue(event)
    unavailable = AlwaysUnavailable()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion('{"command":"clarify","argument":null,"confidence":0,"evidence":"unused"}')
        ),
        consequence_pipeline=create_consequence_pipeline(unavailable),
    )
    orchestrator = ChannelOrchestrator(store, app)

    for _ in range(4):
        with pytest.raises(TransientProviderError):
            asyncio.run(orchestrator.process_available("game"))
        with store.transaction() as connection:
            connection.execute(
                """UPDATE inbox_messages SET next_attempt_at = NULL
                   WHERE event_id = ?""",
                (event.event_id,),
            )

    result = asyncio.run(orchestrator.process_available("game"))

    assert result is not None
    assert unavailable.calls == 5
    assert tr("ru", "committed_roll_consequence_provider_unavailable") in result.items[0].text
    assert tr("ru", "committed_roll_consequence_skipped") not in result.items[0].text
    assert tr("ru", "system_failure") not in result.items[0].text
    assert not store.has_scene_patch(f"roll:{roll.roll_id}")
    assert store.failed_inbox() == []
    assert all(
        tr("ru", "system_failure") not in row["content"]
        for row in store.pending_outbox(channel_id="game")
    )


def test_automatic_outcome_patch_changes_actor_scene_and_location(tmp_path) -> None:
    store = setup(tmp_path)
    store.create_scene(scene_id="archive", game_id="game", title="Archive")
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"declare_action","argument":null,"confidence":1,'
                '"evidence":"crosses into the archive"}'
            )
        ),
        action_pipeline=create_action_pipeline(
            Completion(
                '{"resolution":"automatic","trait_names":[],"aspect_names":[],'
                '"flag":null,"bonus_ids":[],"difficulty":null,'
                '"evidence":["the passage is clear"],"clarification_question":null}'
            )
        ),
        consequence_pipeline=create_consequence_pipeline(
            Completion(
                """
                {
                  "summary": "The hero enters the archive with the brass key",
                  "add_facts": ["The archive door is open"],
                  "remove_facts": ["The door is closed"],
                  "add_actor_conditions": [],
                  "remove_actor_conditions": ["Pinned"],
                  "add_actor_plot_items": [
                    {"name": "Brass key", "description": "Opens the archive lift"}
                  ],
                  "remove_actor_plot_items": ["Old token"],
                  "move_actor_to_scene_id": "archive",
                  "upsert_scene_npcs": [
                    {"npc_id": "courier", "name": "Courier", "state": "Escaped"}
                  ],
                  "remove_scene_npc_ids": [],
                  "open_threads": ["Who hired the courier?"],
                  "close_threads": [],
                  "grant_temporary_bonus": {
                    "bonus_id": "courier-route",
                    "type": "extra_die",
                    "trigger": "Following the courier through the archive"
                  }
                }
                """
            )
        ),
        narrative_pipeline=create_narrative_pipeline(
            Completion('{"narrative":"Герой скрывается в архиве."}')
        ),
    )

    result = asyncio.run(
        app(
            IncomingMessage(
                event_id="full-outcome",
                channel_id="game",
                author_id="alice",
                content="Я прохожу в открытый архив.",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    assert isinstance(result, HandlerResponse)
    room = store.scene_by_id(game_id="game", scene_id="room")
    assert room["state"]["facts"] == ["The archive door is open"]
    assert room["state"]["npcs"][0]["id"] == "courier"
    actor = store.character_for_player(game_id="game", player_id="alice")
    assert actor.conditions == ()
    assert [item.name for item in actor.plot_items] == ["Brass key"]
    assert actor.sheet.temporary_bonuses[0].bonus_id == "courier-route"
    assert store.scene_projection(game_id="game", player_id="alice")["scene_id"] == "archive"


def test_automatic_patch_survives_narrative_failure_with_safe_delivery(tmp_path) -> None:
    store = setup(tmp_path)
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            Completion(
                '{"command":"declare_action","argument":null,"confidence":1,'
                '"evidence":"opens door"}'
            )
        ),
        action_pipeline=create_action_pipeline(
            Completion(
                '{"resolution":"automatic","trait_names":[],"aspect_names":[],'
                '"flag":null,"bonus_ids":[],"difficulty":null,'
                '"evidence":["door is unlocked"],"clarification_question":null}'
            )
        ),
        consequence_pipeline=create_consequence_pipeline(
            Completion(
                '{"summary":"The door opens","add_facts":["The door is open"],'
                '"remove_facts":["The door is closed"]}'
            )
        ),
        narrative_pipeline=create_narrative_pipeline(InvalidCompletion()),
    )

    result = asyncio.run(
        app(
            IncomingMessage(
                event_id="automatic-narrative-failure",
                channel_id="game",
                author_id="alice",
                content="Я открываю незапертую дверь.",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    assert isinstance(result, HandlerResponse)
    assert "исход действия зафиксирован" in result.deliveries[0].content.lower()
    assert store.scene_by_id(game_id="game", scene_id="room")["state"]["facts"] == [
        "The door is open"
    ]
