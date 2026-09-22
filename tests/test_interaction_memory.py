import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from masterclaw.app.action_service import ActionService
from masterclaw.app.message_handler import MessageApplication
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.mechanics import CharacterSheet, PoolProposal, Trait
from masterclaw.domain.models import GameLifecycle, HandlerResponse, IncomingMessage
from masterclaw.domain.state import GameState, PendingKind, WorldState
from masterclaw.domain.text_safety import contains_secret_fragment
from masterclaw.pipelines.base import CompletionResult, TransientProviderError
from masterclaw.pipelines.conversation import create_roleplay_reply_pipeline
from masterclaw.pipelines.player_narration import create_player_narration_pipeline
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


class SequencedRoleplay:
    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.contexts: list[str] = []

    async def complete(self, **kwargs) -> CompletionResult:
        self.contexts.append(kwargs["context"])
        reply = self.replies[len(self.contexts) - 1]
        return CompletionResult(f'{{"reply":"{reply}"}}', used_tool=True)


class InvalidRoleplay:
    async def complete(self, **kwargs) -> CompletionResult:
        return CompletionResult("{}", used_tool=True)


class FailedRoleplayProvider:
    async def complete(self, **kwargs) -> CompletionResult:
        raise TransientProviderError("provider unavailable")


class AcceptedNarration:
    async def complete(self, **kwargs) -> CompletionResult:
        return CompletionResult(
            '{"accepted":true,"reason":"within rights","scale_back_request":null,'
            '"approved_narration":"Approved narration."}',
            used_tool=True,
        )


def active_store(tmp_path) -> SQLiteStore:
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
    store.create_scene(scene_id="room", game_id="game", title="Guard Room")
    store.create_character(
        CharacterState(
            "hero",
            "game",
            "alice",
            "An archivist.",
            CharacterSheet(
                name="Mara",
                traits=(Trait("Lore", 2, ("Runes", "Archives")),),
                flags=(),
            ),
        )
    )
    store.place_player(game_id="game", player_id="alice", scene_id="room")
    return store


def message(event_id: str, content: str) -> IncomingMessage:
    return IncomingMessage(
        event_id=event_id,
        channel_id="game",
        author_id="alice",
        content=content,
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )


def interaction_events(store: SQLiteStore) -> list[dict[str, object]]:
    return [
        event
        for event in store.recent_domain_events(game_id="game", limit=30)
        if event["event_type"] == "interaction_recorded"
    ]


def test_interaction_event_is_compact_canonical_and_idempotent(tmp_path) -> None:
    store = active_store(tmp_path)

    assert store.record_interaction_event(
        game_id="game",
        scene_id="room",
        actor_role="gm_or_npc",
        kind="gm_roleplay_reply",
        text="  The guard   lowers\n\nher spear.  ",
        causation_id="roleplay:event-1",
        player_id="alice",
        summary="  A guarded welcome. ",
    )
    assert not store.record_interaction_event(
        game_id="game",
        scene_id="room",
        actor_role="gm_or_npc",
        kind="gm_roleplay_reply",
        text="A retry must not overwrite the canonical reply.",
        causation_id="roleplay:event-1",
        player_id="alice",
    )

    events = interaction_events(store)
    assert len(events) == 1
    assert events[0]["payload"] == {
        "kind": "gm_roleplay_reply",
        "scene_id": "room",
        "actor": {"role": "gm_or_npc"},
        "text": "The guard lowers her spear.",
        "player": {
            "player_id": "alice",
            "character_id": "hero",
            "character_name": "Mara",
        },
        "summary": "A guarded welcome.",
    }


def test_gm_remembers_its_previous_npc_reply_from_domain_history(tmp_path) -> None:
    store = active_store(tmp_path)
    roleplay = SequencedRoleplay(
        [
            "The guard says the eastern archive burned last winter.",
            "The guard points toward the sealed eastern stair.",
        ]
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(PROMPTS),
        state_router=StateDecisionRouter(FixedDecision("player_narration")),
        roleplay_reply_pipeline=create_roleplay_reply_pipeline(roleplay),
    )

    first = asyncio.run(app(message("speech-1", "I ask what happened to the archive.")))
    second = asyncio.run(app(message("speech-2", "I ask where its entrance was.")))

    assert isinstance(first, HandlerResponse)
    assert isinstance(second, HandlerResponse)
    assert len(roleplay.contexts) == 2
    assert "The guard says the eastern archive burned last winter." in roleplay.contexts[1]
    assert '"event_type":"interaction_recorded"' in roleplay.contexts[1]
    with store.connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) AS count FROM outbox_messages").fetchone()["count"]
            == 0
        )


def test_normalized_secret_fragment_in_roleplay_reply_is_replaced_before_recording(
    tmp_path,
) -> None:
    secret = "The bell keeper is the storm's forgotten name."
    store = active_store(tmp_path)
    store.update_world_content(
        world_id="world",
        expected_revision=0,
        content={"secret_plot": secret},
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(PROMPTS),
        state_router=StateDecisionRouter(FixedDecision("player_narration")),
        roleplay_reply_pipeline=create_roleplay_reply_pipeline(
            SequencedRoleplay(["THE BELL-KEEPER is the storm’s forgotten NAME"])
        ),
    )

    result = asyncio.run(app(message("secret-reply", "I ask what the keeper knows.")))

    assert isinstance(result, HandlerResponse)
    assert not contains_secret_fragment(result, secret)
    assert not contains_secret_fragment(interaction_events(store), secret)


def test_invalid_gm_reply_is_not_recorded(tmp_path) -> None:
    store = active_store(tmp_path)
    app = MessageApplication(
        store=store,
        context=ContextAssembler(PROMPTS),
        state_router=StateDecisionRouter(FixedDecision("player_narration")),
        roleplay_reply_pipeline=create_roleplay_reply_pipeline(InvalidRoleplay()),
    )

    result = asyncio.run(app(message("invalid-reply", "I greet the guard.")))

    assert isinstance(result, str)
    assert interaction_events(store) == []


def test_provider_failure_does_not_record_a_gm_reply(tmp_path) -> None:
    store = active_store(tmp_path)
    app = MessageApplication(
        store=store,
        context=ContextAssembler(PROMPTS),
        state_router=StateDecisionRouter(FixedDecision("player_narration")),
        roleplay_reply_pipeline=create_roleplay_reply_pipeline(FailedRoleplayProvider()),
    )

    with pytest.raises(TransientProviderError):
        asyncio.run(app(message("provider-failure", "I greet the guard.")))

    assert interaction_events(store) == []


def test_accepted_player_narration_is_recorded_after_review(tmp_path) -> None:
    store = active_store(tmp_path)
    pending = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="room",
        proposal=PoolProposal(
            trait_names=("Lore",),
            aspect_names=("Runes", "Archives"),
            difficulty=2,
        ),
        prompt="Confirm.",
        declaration="Open the rune lock.",
    )
    roll = ActionService(store).confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        confirmation_event_id="confirm-roll",
        die=lambda: 4,
    )
    narration_pending = store.open_pending(game_id="game", player_id="alice")
    assert narration_pending is not None
    assert narration_pending.kind is PendingKind.PLAYER_NARRATION
    app = MessageApplication(
        store=store,
        context=ContextAssembler(PROMPTS),
        state_router=StateDecisionRouter(FixedDecision("answer_pending")),
        player_narration_pipeline=create_player_narration_pipeline(AcceptedNarration()),
    )

    result = asyncio.run(
        app(message("narration", "Mara catches the falling key before it reaches the grate."))
    )

    assert isinstance(result, HandlerResponse)
    assert store.open_pending(game_id="game", player_id="alice") is None
    events = interaction_events(store)
    assert len(events) == 1
    assert events[0]["causation_id"] == f"player-narration:{narration_pending.interaction_id}"
    assert events[0]["payload"]["kind"] == "accepted_player_narration"
    assert events[0]["payload"]["text"] == "Approved narration."
    assert events[0]["payload"]["metadata"] == {
        "roll_id": roll.roll_id,
        "source_event_id": "narration",
        "fiction_context": {
            "game_id": "game",
            "player_id": "alice",
            "character_id": "hero",
            "character_revision": 1,
            "scene_id": "room",
            "scene_revision": 0,
            "location_revision": 0,
            "participants": ["alice"],
        },
        "response_mode": "delivery",
        "original_target_channel_id": "narrative",
    }


def test_existing_scene_patch_summary_is_outcome_memory(tmp_path) -> None:
    store = active_store(tmp_path)
    store.apply_scene_patch(
        game_id="game",
        scene_id="room",
        expected_revision=0,
        causation_id="automatic:door",
        summary="The barred eastern door is now open.",
        add_facts=["The eastern door is open."],
        remove_facts=[],
    )

    event = store.recent_domain_events(game_id="game", limit=1)[0]
    assert event["event_type"] == "scene_patched"
    assert event["payload"]["summary"] == "The barred eastern door is now open."
