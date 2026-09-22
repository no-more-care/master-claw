import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from masterclaw.app.i18n import tr
from masterclaw.app.message_handler import MessageApplication
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.characters import CharacterState, Condition
from masterclaw.domain.mechanics import CharacterSheet, Trait
from masterclaw.domain.models import GameLifecycle, IncomingMessage
from masterclaw.domain.outcomes import CanonicalOutcomePatch
from masterclaw.domain.state import GameState, WorldState
from masterclaw.pipelines.action import create_action_pipeline
from masterclaw.pipelines.base import CompletionResult
from masterclaw.pipelines.conversation import (
    create_roleplay_reply_pipeline,
    create_scene_question_pipeline,
)
from masterclaw.pipelines.reserve_recovery import create_reserve_recovery_pipeline
from masterclaw.pipelines.state_decision import StateDecisionRouter
from masterclaw.storage.sqlite import SQLiteStore

PROMPTS = Path(__file__).parents[1] / "prompts"


class FixedDecision:
    def __init__(self, command: str) -> None:
        self.command = command

    async def complete(self, **kwargs) -> CompletionResult:
        return CompletionResult(
            (
                f'{{"command":"{self.command}","argument":null,"confidence":1,'
                '"evidence":"test route"}'
            ),
            used_tool=True,
        )


class MutatingCompletion:
    def __init__(self, response: str, mutation) -> None:
        self.response = response
        self.mutation = mutation
        self.calls = 0

    async def complete(self, **kwargs) -> CompletionResult:
        self.calls += 1
        self.mutation()
        return CompletionResult(self.response, used_tool=True)


def active_store(tmp_path, *, progression_enabled: bool = False) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(
        GameState(
            "game",
            "world",
            GameLifecycle.ACTIVE,
            progression_enabled=progression_enabled,
        )
    )
    store.bind_channel(channel_id="game", game_id="game")
    store.create_scene(
        scene_id="room",
        game_id="game",
        title="Rune Room",
        state={"facts": ["The door is closed"]},
    )
    store.create_character(
        CharacterState(
            "hero",
            "game",
            "alice",
            "An archivist.",
            CharacterSheet(
                name="Mara",
                traits=(Trait("Lore", 2, ("Runes",)),),
                flags=(),
            ),
        )
    )
    store.place_player(game_id="game", player_id="alice", scene_id="room")
    return store


def message(event_id: str) -> IncomingMessage:
    return IncomingMessage(
        event_id=event_id,
        channel_id="game",
        author_id="alice",
        content="I open the rune door.",
        created_at=datetime(2026, 7, 23, tzinfo=UTC),
    )


def concurrent_mutation(store: SQLiteStore, kind: str, causation_id: str) -> None:
    scene = store.scene_projection(game_id="game", player_id="alice")
    character = store.character_for_player(game_id="game", player_id="alice")
    assert scene is not None and character is not None
    if kind == "scene":
        store.apply_scene_patch(
            game_id="game",
            scene_id="room",
            expected_revision=int(scene["scene_revision"]),
            causation_id=causation_id,
            summary="Another event changes the room.",
            add_facts=["The rune door is barred from outside"],
            remove_facts=[],
        )
    elif kind == "actor":
        store.apply_outcome_patch(
            game_id="game",
            scene_id="room",
            expected_scene_revision=int(scene["scene_revision"]),
            actor_character_id=character.character_id,
            expected_actor_revision=character.revision,
            expected_actor_location_revision=int(scene["location_revision"]),
            expected_scene_participants=tuple(scene["participants"]),
            causation_id=causation_id,
            patch=CanonicalOutcomePatch(
                summary="Another event changes the actor.",
                add_actor_conditions=(Condition("Distracted", "external event"),),
            ),
        )
    elif kind == "move":
        store.create_scene(scene_id="hall", game_id="game", title="Hall")
        store.place_player(game_id="game", player_id="alice", scene_id="hall")
    elif kind == "participants":
        store.place_player(game_id="game", player_id="bob", scene_id="room")
    else:
        raise AssertionError(f"unsupported concurrent mutation: {kind}")


@pytest.mark.parametrize("external_actor_change", [False, True])
def test_roleplay_replay_attributes_its_own_activity_xp_before_recovery(
    tmp_path,
    monkeypatch,
    external_actor_change: bool,
) -> None:
    store = active_store(tmp_path, progression_enabled=True)
    incoming = message("roleplay-activity-crash")
    started_at = incoming.created_at - timedelta(minutes=30)
    store.start_activity_clock(game_id="game", started_at=started_at)
    for elapsed in (5, 10, 15, 20, 25):
        store.record_activity(
            game_id="game",
            occurred_at=started_at + timedelta(minutes=elapsed),
        )
    recovery = MutatingCompletion(
        '{"safe_rest_completed":false,"safe_rest_reason":null,"awards":[]}',
        lambda: None,
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(PROMPTS),
        state_router=StateDecisionRouter(FixedDecision("player_narration")),
        roleplay_reply_pipeline=create_roleplay_reply_pipeline(
            MutatingCompletion('{"reply":"The keeper nods."}', lambda: None)
        ),
        reserve_recovery_pipeline=create_reserve_recovery_pipeline(recovery),
    )
    record_activity = store.record_activity
    interrupt_once = True

    def interrupt_after_activity(**kwargs):
        nonlocal interrupt_once
        update = record_activity(**kwargs)
        if interrupt_once and kwargs.get("causation_id") == f"activity:{incoming.event_id}":
            interrupt_once = False
            raise RuntimeError("simulated crash after causal activity")
        return update

    monkeypatch.setattr(store, "record_activity", interrupt_after_activity)

    with pytest.raises(RuntimeError, match="simulated crash"):
        asyncio.run(app(incoming))

    character = store.character_for_player(game_id="game", player_id="alice")
    assert character is not None
    assert character.experience_earned == 1
    if external_actor_change:
        concurrent_mutation(store, "actor", "external-after-roleplay-activity")

    asyncio.run(app(incoming))

    assert recovery.calls == (0 if external_actor_change else 1)
    assert (store.reserve_recovery_decision(f"roleplay:{incoming.event_id}") is not None) is (
        not external_actor_change
    )


@pytest.mark.parametrize("mutation_kind", ["scene", "actor"])
def test_roleplay_skips_recovery_if_fiction_changes_after_interaction_commit(
    tmp_path,
    monkeypatch,
    mutation_kind: str,
) -> None:
    store = active_store(tmp_path)
    incoming = message(f"roleplay-post-commit-{mutation_kind}")
    recovery = MutatingCompletion(
        '{"safe_rest_completed":false,"safe_rest_reason":null,"awards":[]}',
        lambda: None,
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(PROMPTS),
        state_router=StateDecisionRouter(FixedDecision("player_narration")),
        roleplay_reply_pipeline=create_roleplay_reply_pipeline(
            MutatingCompletion('{"reply":"The keeper nods."}', lambda: None)
        ),
        reserve_recovery_pipeline=create_reserve_recovery_pipeline(recovery),
    )
    record_interaction = store.record_interaction_event
    mutated = False

    def commit_then_mutate(**kwargs):
        nonlocal mutated
        committed = record_interaction(**kwargs)
        if not mutated and kwargs.get("causation_id") == f"roleplay:{incoming.event_id}":
            mutated = True
            concurrent_mutation(
                store,
                mutation_kind,
                f"external-after-roleplay-commit-{mutation_kind}",
            )
        return committed

    monkeypatch.setattr(store, "record_interaction_event", commit_then_mutate)

    asyncio.run(app(incoming))

    assert mutated
    assert recovery.calls == 0
    assert store.reserve_recovery_decision(f"roleplay:{incoming.event_id}") is None
    assert (
        store.domain_event_for_causation(
            event_type="interaction_recorded",
            causation_id=f"roleplay:{incoming.event_id}",
        )
        is not None
    )


@pytest.mark.parametrize("mutation_kind", ["scene", "actor"])
def test_roleplay_drops_model_reply_if_fiction_context_changes_during_llm(
    tmp_path,
    mutation_kind: str,
) -> None:
    store = active_store(tmp_path)
    completion = MutatingCompletion(
        '{"reply":"The keeper opens the rune door."}',
        lambda: concurrent_mutation(
            store,
            mutation_kind,
            f"external-roleplay-{mutation_kind}",
        ),
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(PROMPTS),
        state_router=StateDecisionRouter(FixedDecision("player_narration")),
        roleplay_reply_pipeline=create_roleplay_reply_pipeline(completion),
    )
    incoming = message(f"stale-roleplay-{mutation_kind}")

    first = asyncio.run(app(incoming))
    replay = asyncio.run(app(incoming))

    assert "Устаревший результат не применён" in first
    assert "Устаревший результат не применён" in replay
    assert completion.calls == 1
    assert (
        store.domain_event_for_causation(
            event_type="interaction_recorded",
            causation_id=f"roleplay:{incoming.event_id}",
        )
        is None
    )


@pytest.mark.parametrize("mutation_kind", ["scene", "actor", "move", "participants"])
def test_scene_question_drops_answer_if_fiction_context_changes_during_llm(
    tmp_path,
    mutation_kind: str,
) -> None:
    store = active_store(tmp_path)
    completion = MutatingCompletion(
        '{"reply":"The rune door is visibly unlocked."}',
        lambda: concurrent_mutation(
            store,
            mutation_kind,
            f"external-scene-question-{mutation_kind}",
        ),
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(PROMPTS),
        state_router=StateDecisionRouter(FixedDecision("ask_scene_question")),
        scene_question_pipeline=create_scene_question_pipeline(completion),
    )
    incoming = message(f"stale-scene-question-{mutation_kind}")

    first = asyncio.run(app(incoming))
    replay = asyncio.run(app(incoming))

    assert tr("ru", "fiction_context_changed_retry") in first
    assert tr("ru", "fiction_context_changed_retry") in replay
    assert "visibly unlocked" not in first
    assert "visibly unlocked" not in replay
    assert completion.calls == 1


@pytest.mark.parametrize("mutation_kind", ["scene", "actor"])
def test_action_drops_roll_proposal_if_fiction_context_changes_during_llm(
    tmp_path,
    mutation_kind: str,
) -> None:
    store = active_store(tmp_path)
    completion = MutatingCompletion(
        (
            '{"resolution":"roll","trait_names":["Lore"],'
            '"aspect_names":["Runes"],"flag":null,"bonus_ids":[],"difficulty":2,'
            '"evidence":["the rune is understood"],"clarification_question":null}'
        ),
        lambda: concurrent_mutation(
            store,
            mutation_kind,
            f"external-action-{mutation_kind}",
        ),
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(PROMPTS),
        state_router=StateDecisionRouter(FixedDecision("declare_action")),
        action_pipeline=create_action_pipeline(completion),
    )
    incoming = message(f"stale-action-{mutation_kind}")

    first = asyncio.run(app(incoming))
    replay = asyncio.run(app(incoming))

    assert "Устаревший результат не применён" in first
    assert "Устаревший результат не применён" in replay
    assert completion.calls == 1
    assert store.open_pending(game_id="game", player_id="alice") is None
