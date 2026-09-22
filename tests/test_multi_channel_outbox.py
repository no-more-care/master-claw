import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from masterclaw.app.orchestrator import ChannelOrchestrator
from masterclaw.domain.models import (
    GameLifecycle,
    HandlerResponse,
    IncomingMessage,
    OutboundDelivery,
)
from masterclaw.domain.state import GameState, PendingInteraction, PendingKind, WorldState
from masterclaw.storage.sqlite import SQLiteStore


def test_game_and_narrative_deliveries_commit_with_same_inbox_batch(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(
        GameState(
            "game",
            "world",
            GameLifecycle.ACTIVE,
            locale="en",
            narrative_channel_id="narrative-channel",
        )
    )
    for channel_id in ("game-channel", "narrative-channel"):
        store.record_discord_channel(
            channel_id=channel_id,
            guild_id="guild",
            parent_channel_id=None,
            kind="text",
        )
    store.bind_channel(channel_id="game-channel", game_id="game")
    store.enqueue(
        IncomingMessage.now(
            event_id="1",
            channel_id="game-channel",
            author_id="alice",
            content="confirm",
        )
    )

    async def handler(_):
        return HandlerResponse(
            "Mechanical result",
            (OutboundDelivery("narrative-channel", "Fictional outcome", "narrative"),),
        )

    asyncio.run(ChannelOrchestrator(store, handler).process_available("game-channel"))
    game = store.pending_outbox(channel_id="game-channel")
    narrative = store.pending_outbox(channel_id="narrative-channel")
    assert len(game) == 1 and "Mechanical result" in game[0]["content"]
    assert len(narrative) == 1 and narrative[0]["content"] == "Fictional outcome"


@pytest.mark.parametrize("delivery_kind", ["narrative", "roleplay_reply", "player_narration"])
def test_journaled_prose_is_skipped_if_narrative_channel_changes_before_retry(
    tmp_path,
    monkeypatch,
    delivery_kind,
) -> None:
    store = SQLiteStore(tmp_path / "stale-prose.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(
        GameState(
            "game",
            "world",
            GameLifecycle.ACTIVE,
            locale="en",
            narrative_channel_id="narrative-old",
        )
    )
    for channel_id in ("game-channel", "narrative-old", "narrative-new"):
        store.record_discord_channel(
            channel_id=channel_id,
            guild_id="guild",
            parent_channel_id=None,
            kind="text",
        )
    store.bind_channel(channel_id="game-channel", game_id="game")
    event = IncomingMessage.now(
        event_id="automatic-result",
        channel_id="game-channel",
        author_id="alice",
        content="I open the door.",
    )
    assert store.enqueue(event)
    handler_calls = 0

    async def handler(_message):
        nonlocal handler_calls
        handler_calls += 1
        store.record_interaction_event(
            game_id="game",
            scene_id=None,
            actor_role="player_character",
            kind="automatic_outcome",
            text="The door opens.",
            causation_id="automatic-result:automatic-result",
            player_id="alice",
        )
        return HandlerResponse(
            "The action succeeds.",
            (OutboundDelivery("narrative-old", "The door swings open.", delivery_kind),),
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
    orchestrator = ChannelOrchestrator(store, handler)
    with pytest.raises(RuntimeError, match="simulated completion failure"):
        asyncio.run(orchestrator.process_available("game-channel"))

    game = store.game_state("game")
    assert game is not None
    store.set_narrative_channel(
        game_id="game",
        channel_id="narrative-new",
        expected_revision=game.revision,
    )
    replayed = asyncio.run(orchestrator.process_available("game-channel"))

    assert replayed is not None
    assert handler_calls == 1
    assert completion_attempts == 2
    assert store.pending_outbox(channel_id="narrative-old") == []
    source_outbox = store.pending_outbox(channel_id="game-channel")
    assert [row["kind"] for row in source_outbox] == ["response", "system_notice"]
    assert source_outbox[0]["content"] == "The action succeeds."
    assert "game-state changes were saved" in source_outbox[1]["content"]
    history = store.recent_chat_messages(channel_id="game-channel", limit=10)
    assert [turn["role"] for turn in history] == ["user", "assistant"]
    assert history[-1]["content"] == "The action succeeds."
    events = store.recent_domain_events(game_id="game", limit=20)
    assert [event["event_type"] for event in events].count("interaction_recorded") == 1
    skipped = [event for event in events if event["event_type"] == "prose_delivery_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["payload"]["reason"] == "target_no_longer_narrative_channel"
    assert all(row["kind"] != "system_failure" for row in source_outbox)


def test_journaled_completion_game_does_not_overwrite_a_new_binding(
    tmp_path,
    monkeypatch,
) -> None:
    store = SQLiteStore(tmp_path / "completion-rebind.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("current-game", "world", GameLifecycle.ACTIVE, locale="en"))
    for channel_id in ("selector", "narrative"):
        store.record_discord_channel(
            channel_id=channel_id,
            guild_id="guild",
            parent_channel_id=None,
            kind="text",
        )
    received_at = datetime(2026, 1, 1, tzinfo=UTC)
    source = IncomingMessage(
        event_id="select-game",
        channel_id="selector",
        author_id="alice",
        content="Choose this world.",
        created_at=received_at,
    )
    follower = IncomingMessage(
        event_id="later-message",
        channel_id="selector",
        author_id="alice",
        content="And then start.",
        created_at=received_at + timedelta(seconds=1),
    )
    assert store.enqueue(source)
    assert store.enqueue(follower)
    handler_calls = 0

    async def handler(_message):
        nonlocal handler_calls
        handler_calls += 1
        store.create_game(
            GameState(
                "selected-game",
                "world",
                GameLifecycle.PREPARING,
                locale="en",
                narrative_channel_id="narrative",
            )
        )
        store.bind_channel(channel_id="selector", game_id="selected-game")
        return HandlerResponse(
            "Selected the saved game.",
            (OutboundDelivery("narrative", "Opening prose.", "narrative"),),
            completion_game_id="selected-game",
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
    orchestrator = ChannelOrchestrator(store, handler)
    with pytest.raises(RuntimeError, match="simulated completion failure"):
        asyncio.run(orchestrator.process_available("selector", limit=1))

    store.bind_channel(channel_id="selector", game_id="current-game")
    replayed = asyncio.run(orchestrator.process_available("selector", limit=1))

    assert replayed is not None
    assert handler_calls == 1
    assert completion_attempts == 2
    assert store.channel_state("selector").game_id == "current-game"
    outbox = store.pending_outbox(channel_id="selector")
    assert len(outbox) == 1
    assert outbox[0]["kind"] == "system_notice"
    assert "Do not repeat the request" in outbox[0]["content"]
    assert store.pending_outbox(channel_id="narrative") == []
    with store.connect() as connection:
        inbox = {
            row["event_id"]: (row["game_id"], row["status"])
            for row in connection.execute(
                """SELECT event_id, game_id, status FROM inbox_messages
                   WHERE event_id IN ('select-game', 'later-message')"""
            ).fetchall()
        }
        assistant_count = connection.execute(
            """SELECT COUNT(*) AS count FROM assistant_responses
               WHERE source_event_id = 'select-game'"""
        ).fetchone()["count"]
    assert inbox == {
        "select-game": (None, "processed"),
        "later-message": (None, "pending"),
    }
    assert assistant_count == 0
    audit = [
        event
        for event in store.recent_domain_events(game_id="selected-game", limit=20)
        if event["event_type"] == "completion_context_changed"
    ]
    assert len(audit) == 1
    assert audit[0]["payload"]["current_game_id"] == "current-game"


def test_journaled_game_response_is_suppressed_after_source_channel_rebind(
    tmp_path,
    monkeypatch,
) -> None:
    store = SQLiteStore(tmp_path / "source-rebind.sqlite3")
    store.initialize()
    store.create_world(WorldState("world-a", "World A"))
    store.create_world(WorldState("world-b", "World B"))
    store.create_game(
        GameState(
            "game-a",
            "world-a",
            GameLifecycle.ACTIVE,
            locale="en",
            narrative_channel_id="narrative-a",
        )
    )
    store.create_game(GameState("game-b", "world-b", GameLifecycle.ACTIVE, locale="en"))
    for channel_id in ("game-channel", "narrative-a"):
        store.record_discord_channel(
            channel_id=channel_id,
            guild_id="guild",
            parent_channel_id=None,
            kind="text",
        )
    store.bind_channel(channel_id="game-channel", game_id="game-a")
    event = IncomingMessage.now(
        event_id="committed-automatic",
        channel_id="game-channel",
        author_id="alice",
        content="I trigger the mechanism.",
    )
    assert store.enqueue(event)
    handler_calls = 0

    async def handler(_message):
        nonlocal handler_calls
        handler_calls += 1
        store.record_interaction_event(
            game_id="game-a",
            scene_id=None,
            actor_role="player_character",
            kind="automatic_outcome",
            text="The mechanism activates.",
            causation_id="automatic:committed-automatic",
            player_id="alice",
        )
        return HandlerResponse(
            "The mechanism activates.",
            (OutboundDelivery("narrative-a", "Ancient gears turn.", "narrative"),),
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
    orchestrator = ChannelOrchestrator(store, handler)
    with pytest.raises(RuntimeError, match="simulated completion failure"):
        asyncio.run(orchestrator.process_available("game-channel"))

    store.queue_system_notice(
        channel_id="game-channel",
        key="preexisting-a",
        content="First superseded notice.",
        source_event_id=event.event_id,
    )
    store.queue_system_notice(
        channel_id="game-channel",
        key="preexisting-b",
        content="Second superseded notice.",
        source_event_id=event.event_id,
    )
    store.queue_system_notice(
        channel_id="game-channel",
        key="unrelated",
        content="Unrelated notice.",
        source_event_id="another-event",
    )
    store.bind_channel(channel_id="game-channel", game_id="game-b")
    replayed = asyncio.run(orchestrator.process_available("game-channel"))

    assert replayed is not None
    assert handler_calls == 1
    assert completion_attempts == 2
    assert store.channel_state("game-channel").game_id == "game-b"
    outbox = store.pending_outbox(channel_id="game-channel")
    source_notices = [row for row in outbox if row["source_event_id"] == event.event_id]
    assert len(source_notices) == 1
    assert source_notices[0]["kind"] == "system_notice"
    assert "Do not repeat the action" in source_notices[0]["content"]
    unrelated_notices = [row for row in outbox if row["source_event_id"] == "another-event"]
    assert len(unrelated_notices) == 1
    assert unrelated_notices[0]["content"] == "Unrelated notice."
    assert store.pending_outbox(channel_id="narrative-a") == []
    with store.connect() as connection:
        source = connection.execute(
            """SELECT game_id, status FROM inbox_messages
               WHERE event_id = 'committed-automatic'"""
        ).fetchone()
        assistant_count = connection.execute(
            """SELECT COUNT(*) AS count FROM assistant_responses
               WHERE source_event_id = 'committed-automatic'"""
        ).fetchone()["count"]
        coalesced = connection.execute(
            """SELECT content, attempts, error FROM outbox_messages
               WHERE channel_id = 'game-channel'
                 AND source_event_id = 'committed-automatic'
                 AND kind = 'system_notice'
               ORDER BY id"""
        ).fetchall()
    assert dict(source) == {"game_id": "game-a", "status": "processed"}
    assert assistant_count == 0
    assert [(row["attempts"], row["error"]) for row in coalesced] == [
        (0, None),
        (5, "SourceGameContextChanged"),
    ]
    assert coalesced[1]["content"] == "Second superseded notice."
    events = store.recent_domain_events(game_id="game-a", limit=20)
    assert [item["event_type"] for item in events].count("interaction_recorded") == 1
    assert [item["event_type"] for item in events].count("source_game_context_changed") == 1


def test_outbox_stops_automatic_retry_and_can_be_requeued(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.enqueue(
        IncomingMessage.now(event_id="1", channel_id="game", author_id="alice", content="hello")
    )

    async def handler(_):
        return "response"

    asyncio.run(ChannelOrchestrator(store, handler).process_available("game"))
    outbox_id = store.pending_outbox()[0]["id"]
    for _ in range(5):
        store.mark_delivery_failed(outbox_id, "network")
    assert store.pending_outbox() == []
    assert store.failed_outbox()[0]["id"] == outbox_id
    store.requeue_failed_outbox(outbox_id)
    assert store.pending_outbox()[0]["id"] == outbox_id


def test_outbox_delivery_is_claimed_only_once(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.enqueue(
        IncomingMessage.now(event_id="1", channel_id="game", author_id="alice", content="hello")
    )

    async def handler(_):
        return "response"

    asyncio.run(ChannelOrchestrator(store, handler).process_available("game"))
    assert len(store.claim_outbox()) == 1
    assert store.claim_outbox() == []


def test_outbox_claims_fifo_heads_per_channel_and_respects_retry_delay(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.queue_system_notice(channel_id="a", key="a-1", content="a first")
    store.queue_system_notice(channel_id="a", key="a-2", content="a second")
    store.queue_system_notice(channel_id="b", key="b-1", content="b first")

    claimed = store.claim_outbox()
    assert [row["content"] for row in claimed] == ["a first", "b first"]
    first_a = claimed[0]["id"]
    first_b = claimed[1]["id"]
    first_a_nonce = claimed[0]["discord_nonce"]
    assert first_a_nonce
    store.mark_delivery_failed(
        first_a,
        "NetworkError",
        delivery_claim=claimed[0]["delivery_claim"],
    )
    store.mark_delivered(
        first_b,
        delivery_claim=claimed[1]["delivery_claim"],
    )

    assert store.claim_outbox() == []
    assert store.pending_outbox(channel_id="a")[0]["next_attempt_at"] is not None

    store.queue_system_notice(channel_id="c", key="c-1", content="c first")
    other_channel = store.claim_outbox()
    assert [row["content"] for row in other_channel] == ["c first"]
    store.mark_delivered(
        other_channel[0]["id"],
        delivery_claim=other_channel[0]["delivery_claim"],
    )

    with store.transaction() as connection:
        connection.execute(
            "UPDATE outbox_messages SET next_attempt_at = datetime('now', '-1 second') "
            "WHERE id = ?",
            (first_a,),
        )
    retried = store.claim_outbox()
    assert [row["content"] for row in retried] == ["a first"]
    assert retried[0]["discord_nonce"] == first_a_nonce
    store.mark_delivered(
        retried[0]["id"],
        delivery_claim=retried[0]["delivery_claim"],
    )
    assert [row["content"] for row in store.claim_outbox()] == ["a second"]


def test_outbox_claims_can_all_be_released_for_immediate_retry(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.queue_system_notice(channel_id="a", key="a", content="a")
    store.queue_system_notice(channel_id="b", key="b", content="b")

    claimed = store.claim_outbox()
    claimed_ids = [row["id"] for row in claimed]
    assert (
        store.release_outbox_claims(
            claimed_ids,
            delivery_claim=claimed[0]["delivery_claim"],
        )
        == 2
    )

    assert [row["id"] for row in store.claim_outbox()] == claimed_ids


def test_terminal_failure_unblocks_next_message_in_same_channel(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.queue_system_notice(channel_id="a", key="a-1", content="head")
    store.queue_system_notice(channel_id="a", key="a-2", content="tail")

    head = store.claim_outbox()[0]
    store.mark_delivery_failed(
        head["id"],
        "NetworkError",
        delivery_claim=head["delivery_claim"],
    )
    for _ in range(4):
        store.mark_delivery_failed(head["id"], "NetworkError")

    assert [row["content"] for row in store.claim_outbox()] == ["tail"]


def test_stale_outbox_owner_cannot_mutate_a_reclaimed_delivery(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.queue_system_notice(channel_id="a", key="a-1", content="head")

    claimed = store.claim_outbox()[0]
    outbox_id = claimed["id"]
    stale_claim = claimed["delivery_claim"]
    current_claim = "replacement-worker"
    with store.transaction() as connection:
        connection.execute(
            """UPDATE outbox_messages
               SET delivery_claim = ?, claimed_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (current_claim, outbox_id),
        )

    with pytest.raises(RuntimeError, match="claim lost"):
        store.release_outbox_claims(
            [outbox_id],
            delivery_claim=stale_claim,
        )
    with pytest.raises(RuntimeError, match="claim lost"):
        store.mark_delivery_failed(
            outbox_id,
            "stale failure",
            delivery_claim=stale_claim,
        )
    with pytest.raises(RuntimeError, match="claim lost"):
        store.mark_delivery_terminal(
            outbox_id,
            "stale terminal",
            delivery_claim=stale_claim,
        )
    with pytest.raises(RuntimeError, match="claim lost"):
        store.mark_delivered(
            outbox_id,
            discord_message_id="stale-message",
            delivery_claim=stale_claim,
        )
    with pytest.raises(RuntimeError, match="claim lost"):
        store.mark_delivery_failed(outbox_id, "legacy stale failure")
    with pytest.raises(RuntimeError, match="claim lost"):
        store.mark_delivered(outbox_id, discord_message_id="legacy-stale")

    row = store.pending_outbox(channel_id="a")[0]
    assert row["delivery_claim"] == current_claim
    assert row["attempts"] == 0
    with store.connect() as connection:
        persisted = connection.execute(
            """SELECT delivered_at, discord_message_id, error
               FROM outbox_messages WHERE id = ?""",
            (outbox_id,),
        ).fetchone()
    assert persisted["delivered_at"] is None
    assert persisted["discord_message_id"] is None
    assert persisted["error"] is None

    store.mark_delivered(
        outbox_id,
        discord_message_id="current-message",
        delivery_claim=current_claim,
    )
    assert store.pending_outbox(channel_id="a") == []


def _inherited_pending_prompt(tmp_path, database_name: str):
    store = SQLiteStore(tmp_path / database_name)
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(
        GameState(
            "game",
            "world",
            GameLifecycle.ACTIVE,
            locale="en",
        )
    )
    store.record_discord_channel(
        channel_id="parent",
        guild_id="guild",
        parent_channel_id=None,
        kind="text",
    )
    store.record_discord_channel(
        channel_id="thread",
        guild_id="guild",
        parent_channel_id="parent",
        kind="thread",
    )
    store.enable_channel_monitoring("parent")
    store.bind_channel(channel_id="parent", game_id="game")
    store.inherit_thread_context(
        channel_id="thread",
        parent_channel_id="parent",
        guild_id="guild",
    )
    store.enqueue(
        IncomingMessage.now(
            event_id="prompt-source",
            channel_id="thread",
            author_id="alice",
            content="I act",
        )
    )
    store.claim_pending(channel_id="thread", limit=1)
    store.put_pending(
        PendingInteraction(
            "pending",
            "game",
            "alice",
            None,
            PendingKind.CHOICE,
            "Confirm?",
            payload={"prompt_source_event_id": "prompt-source"},
            origin_channel_id="thread",
        )
    )
    store.complete_batch(
        event_ids=["prompt-source"],
        channel_id="thread",
        contents=["Confirm?"],
        idempotency_key="prompt-response",
    )
    return store, store.pending_outbox(channel_id="thread")[0]


def test_inherited_detach_suppresses_unclaimed_prompt_and_queues_notice(tmp_path) -> None:
    store, prompt = _inherited_pending_prompt(tmp_path, "unclaimed.sqlite3")

    store.unbind_channel(channel_id="parent", expected_game_id="game")

    pending = store.pending_by_id("pending")
    assert pending.status.value == "cancelled"
    rows = store.pending_outbox(channel_id="thread")
    assert len(rows) == 1
    assert rows[0]["kind"] == "system_notice"
    assert "channel switched to another game" in rows[0]["content"]
    suppressed = {row["id"]: row for row in store.failed_outbox()}
    assert suppressed[prompt["id"]]["error"] == "PendingInteractionCancelled"


def test_inherited_detach_notifies_after_prompt_was_already_delivered(tmp_path) -> None:
    store, prompt = _inherited_pending_prompt(tmp_path, "delivered.sqlite3")
    store.mark_delivered(prompt["id"], discord_message_id="discord-prompt")

    store.unbind_channel(channel_id="parent", expected_game_id="game")

    rows = store.pending_outbox(channel_id="thread")
    assert len(rows) == 1
    assert rows[0]["kind"] == "system_notice"
    assert "interactive step it created was cancelled" in rows[0]["content"]


def test_completion_does_not_publish_prompt_cancelled_before_outbox_insert(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "cancel-before-complete.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(
        GameState(
            "game",
            "world",
            GameLifecycle.ACTIVE,
            locale="en",
        )
    )
    store.bind_channel(channel_id="channel", game_id="game")
    store.enqueue(
        IncomingMessage.now(
            event_id="source",
            channel_id="channel",
            author_id="alice",
            content="I act",
        )
    )
    store.claim_pending(channel_id="channel", limit=1)
    store.put_pending(
        PendingInteraction(
            "pending",
            "game",
            "alice",
            None,
            PendingKind.CHOICE,
            "Confirm?",
            payload={"prompt_source_event_id": "source"},
            origin_channel_id="channel",
        )
    )

    store.unbind_channel(channel_id="channel", expected_game_id="game")
    store.complete_batch(
        event_ids=["source"],
        channel_id="channel",
        contents=["Confirm this stale action?"],
        idempotency_key="response",
    )

    deliverable = store.pending_outbox(channel_id="channel")
    assert len(deliverable) == 1
    assert deliverable[0]["kind"] == "system_notice"
    assert "no longer bound to the source game" in deliverable[0]["content"]
    assert "Do not repeat the action" in deliverable[0]["content"]
    assert all("Confirm this stale action?" not in row["content"] for row in deliverable)
