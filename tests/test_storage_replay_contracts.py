import json
import sqlite3

import pytest

from masterclaw.domain.models import GameLifecycle, IncomingMessage
from masterclaw.domain.state import (
    GameState,
    NarratorRightsLevel,
    PendingInteraction,
    PendingKind,
    PendingStatus,
    ReserveRecoveryMode,
    WorldState,
)
from masterclaw.storage.sqlite import SQLiteStore


def _store(tmp_path, name: str = "replay.sqlite3") -> SQLiteStore:
    store = SQLiteStore(tmp_path / name)
    store.initialize()
    return store


def _workspace(store: SQLiteStore) -> None:
    store.save_world_workspace(
        channel_id="channel",
        world_id="world",
        stage="collecting",
        brief="Initial brief",
        settings={"title": "Initial"},
        sources={"title": "player"},
        world=WorldState("world", "Initial"),
    )


def _game(store: SQLiteStore) -> None:
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.PREPARING))


def _claim_event(
    store: SQLiteStore,
    *,
    event_id: str,
    channel_id: str = "channel",
) -> None:
    assert store.enqueue(
        IncomingMessage.now(
            event_id=event_id,
            channel_id=channel_id,
            author_id="alice",
            content=event_id,
        )
    )
    assert [item.event_id for item in store.claim_pending(channel_id=channel_id, limit=1)] == [
        event_id
    ]


def test_terminal_pending_records_event_and_compound_root_source(tmp_path) -> None:
    store = _store(tmp_path)
    _game(store)
    store.put_pending(
        PendingInteraction(
            interaction_id="choice",
            game_id="game",
            player_id="alice",
            scene_id=None,
            kind=PendingKind.CHOICE,
            prompt="Continue?",
            payload={
                "prompt_source_event_id": "root:part:2",
                "root_source_event_id": "root",
            },
        )
    )

    store.cancel_pending(
        interaction_id="choice",
        player_id="alice",
        expected_revision=0,
        closed_by_event_id="answer",
    )

    closed = store.closed_pending_for_event(
        game_id="game",
        player_id="alice",
        event_id="answer",
    )
    assert closed is not None
    assert closed.status is PendingStatus.CANCELLED
    assert closed.payload == {
        "prompt_source_event_id": "root:part:2",
        "root_source_event_id": "root",
        "closed_by_event_id": "answer",
    }


def test_open_pending_is_read_only_even_after_ttl(tmp_path) -> None:
    store = _store(tmp_path)
    _game(store)
    store.put_pending(
        PendingInteraction(
            interaction_id="old-question",
            game_id="game",
            player_id="alice",
            scene_id=None,
            kind=PendingKind.CLARIFICATION,
            prompt="Still waiting",
        )
    )
    with store.transaction() as connection:
        connection.execute(
            """UPDATE pending_interactions
               SET created_at = datetime('now', '-2 days')
               WHERE interaction_id = 'old-question'"""
        )

    pending = store.open_pending(game_id="game", player_id="alice")

    assert pending is not None
    assert pending.status is PendingStatus.OPEN
    assert store.pending_by_id("old-question").status is PendingStatus.OPEN


def test_resolved_and_replaced_pending_checkpoint_the_answer_event(tmp_path) -> None:
    store = _store(tmp_path)
    _game(store)
    store.put_pending(
        PendingInteraction(
            "clarification",
            "game",
            "alice",
            None,
            PendingKind.CLARIFICATION,
            "Which way?",
        )
    )
    replacement = PendingInteraction(
        "follow-up",
        "game",
        "alice",
        None,
        PendingKind.CHOICE,
        "Then continue?",
        payload={"root_source_event_id": "root"},
    )

    store.replace_pending(
        current_interaction_id="clarification",
        player_id="alice",
        expected_revision=0,
        replacement=replacement,
        answer="left",
        closed_by_event_id="answer-1",
    )
    first = store.closed_pending_for_event(
        game_id="game",
        player_id="alice",
        event_id="answer-1",
    )
    assert first is not None
    assert first.payload["answer"] == "left"

    store.resolve_pending(
        interaction_id="follow-up",
        player_id="alice",
        expected_revision=0,
        answer="yes",
        closed_by_event_id="answer-2",
    )
    second = store.closed_pending_for_event(
        game_id="game",
        player_id="alice",
        event_id="answer-2",
    )
    assert second is not None
    assert second.payload["answer"] == "yes"
    assert second.payload["root_source_event_id"] == "root"


def test_world_generation_commit_is_atomic_and_replayable(tmp_path) -> None:
    store = _store(tmp_path)
    _workspace(store)
    content = {"locations": [{"name": "Harbor"}], "premise": "Stormbound"}

    assert store.commit_world_generation(
        event_id="generate",
        channel_id="channel",
        world_id="world",
        expected_world_revision=0,
        expected_workspace_revision=0,
        content=content,
        brief="Initial brief",
        settings={"title": "Initial", "locale": "en"},
        sources={"title": "player", "locale": "inferred"},
    )

    assert store.world_content("world") == content
    assert store.world_state("world").revision == 1
    workspace = store.world_workspace("channel")
    assert workspace is not None
    assert workspace["stage"] == "review"
    assert workspace["revision"] == 1
    assert store.world_generation_for_event("generate") == {
        "operation": "generation",
        "channel_id": "channel",
        "world_id": "world",
        "world_revision": 1,
        "workspace_revision": 1,
    }

    assert not store.commit_world_generation(
        event_id="generate",
        channel_id="channel",
        world_id="world",
        expected_world_revision=0,
        expected_workspace_revision=0,
        content={"locations": [{"name": "Must not replace"}]},
        brief="Must not replace",
        settings={},
        sources={},
    )
    assert store.world_content("world") == content
    assert store.world_state("world").revision == 1


def test_world_generation_conflict_rolls_back_both_aggregates_and_marker(tmp_path) -> None:
    store = _store(tmp_path)
    _workspace(store)

    with pytest.raises(RuntimeError, match="workspace revision"):
        store.commit_world_generation(
            event_id="generate",
            channel_id="channel",
            world_id="world",
            expected_world_revision=0,
            expected_workspace_revision=9,
            content={"locations": [{"name": "Harbor"}]},
            brief="Changed",
            settings={"title": "Changed"},
            sources={"title": "player"},
        )

    assert store.world_content("world") == {}
    assert store.world_state("world").revision == 0
    workspace = store.world_workspace("channel")
    assert workspace is not None
    assert workspace["brief"] == "Initial brief"
    assert workspace["revision"] == 0
    assert store.world_generation_for_event("generate") is None


def test_world_revision_is_atomic_and_skips_intake_on_exact_replay(tmp_path) -> None:
    store = _store(tmp_path)
    _workspace(store)

    assert store.apply_world_revision(
        event_id="revise",
        channel_id="channel",
        world_id="world",
        expected_world_revision=0,
        expected_workspace_revision=0,
        title="Revised",
        brief="Revised brief",
        settings={"title": "Revised", "themes": ["mystery"]},
        sources={"title": "player", "themes": "player"},
    )

    assert store.world_state("world") == WorldState("world", "Revised", "draft", 1)
    workspace = store.world_workspace("channel")
    assert workspace is not None
    assert workspace["brief"] == "Revised brief"
    assert workspace["revision"] == 1
    assert store.world_revision_for_event("revise") == {
        "operation": "revision",
        "channel_id": "channel",
        "world_id": "world",
        "world_revision": 1,
        "workspace_revision": 1,
    }

    assert not store.apply_world_revision(
        event_id="revise",
        channel_id="channel",
        world_id="world",
        expected_world_revision=0,
        expected_workspace_revision=0,
        title="Must not replace",
        brief="Must not replace",
        settings={},
        sources={},
    )
    assert store.world_state("world").title == "Revised"
    assert store.world_workspace("channel")["brief"] == "Revised brief"


def test_atomic_world_approval_rolls_back_when_workspace_delete_fails(tmp_path) -> None:
    store = _store(tmp_path)
    _workspace(store)
    store.commit_world_generation(
        event_id="generate",
        channel_id="channel",
        world_id="world",
        expected_world_revision=0,
        expected_workspace_revision=0,
        content={"locations": [{"name": "Harbor"}]},
        brief="Initial brief",
        settings={"title": "Initial"},
        sources={"title": "player"},
    )
    with store.transaction() as connection:
        connection.execute(
            """CREATE TRIGGER reject_project_delete
               BEFORE DELETE ON world_projects
               BEGIN
                 SELECT RAISE(ABORT, 'delete rejected');
               END"""
        )

    with pytest.raises(sqlite3.IntegrityError, match="delete rejected"):
        store.approve_world_and_clear_workspace(
            event_id="approve",
            channel_id="channel",
            world_id="world",
            expected_world_revision=1,
            expected_workspace_revision=1,
        )

    assert store.world_state("world").status == "draft"
    assert store.world_state("world").revision == 1
    assert store.world_workspace("channel") is not None


def test_atomic_world_approval_updates_and_clears_together(tmp_path) -> None:
    store = _store(tmp_path)
    _workspace(store)
    store.commit_world_generation(
        event_id="generate",
        channel_id="channel",
        world_id="world",
        expected_world_revision=0,
        expected_workspace_revision=0,
        content={"locations": [{"name": "Harbor"}]},
        brief="Initial brief",
        settings={"title": "Initial"},
        sources={"title": "player"},
    )

    approved = store.approve_world_and_clear_workspace(
        event_id="approve",
        channel_id="channel",
        world_id="world",
        expected_world_revision=1,
        expected_workspace_revision=1,
    )

    assert approved == WorldState("world", "Initial", "approved", 2)
    assert store.world_state("world") == approved
    assert store.world_project("world") is None
    assert store.world_operation_for_event("approve") == {
        "operation": "approval",
        "channel_id": "channel",
        "world_id": "world",
        "world_revision": 2,
    }
    assert (
        store.approve_world_and_clear_workspace(
            event_id="approve",
            channel_id="channel",
            world_id="world",
            expected_world_revision=1,
            expected_workspace_revision=1,
        )
        == approved
    )


def test_world_pause_and_resume_have_pre_routing_replay_markers(tmp_path) -> None:
    store = _store(tmp_path)
    _workspace(store)

    assert store.pause_world_workspace_for_event(
        event_id="pause",
        channel_id="channel",
        world_id="world",
        expected_workspace_revision=0,
    )
    assert store.world_workspace("channel") is None
    assert store.world_operation_for_event("pause") == {
        "operation": "pause",
        "channel_id": "channel",
        "world_id": "world",
        "workspace_revision": 1,
    }
    assert not store.pause_world_workspace_for_event(
        event_id="pause",
        channel_id="channel",
        world_id="world",
        expected_workspace_revision=0,
    )

    assert store.resume_world_workspace_for_event(
        event_id="resume",
        channel_id="channel",
        world_id="world",
        expected_workspace_revision=1,
    )
    assert store.world_workspace("channel")["revision"] == 2
    assert store.world_operation_for_event("resume") == {
        "operation": "resume",
        "channel_id": "channel",
        "world_id": "world",
        "workspace_revision": 2,
    }
    assert not store.resume_world_workspace_for_event(
        event_id="resume",
        channel_id="channel",
        world_id="world",
        expected_workspace_revision=1,
    )


def test_same_value_setters_do_not_advance_revisions(tmp_path) -> None:
    store = _store(tmp_path)
    _game(store)

    assert (
        store.set_world_status(
            world_id="world",
            status="draft",
            expected_revision=0,
        ).revision
        == 0
    )
    store.set_progression_enabled(game_id="game", enabled=False, expected_revision=0)
    store.set_narrator_rights_level(
        game_id="game",
        level=NarratorRightsLevel.MINOR,
        expected_revision=0,
    )
    store.set_reserve_recovery_mode(
        game_id="game",
        mode=ReserveRecoveryMode.BOTH,
        expected_revision=0,
    )
    assert store.game_state("game").revision == 0

    store.set_narrative_channel(
        game_id="game",
        channel_id="narrative",
        expected_revision=0,
    )
    assert store.game_state("game").revision == 1
    store.set_narrative_channel(
        game_id="game",
        channel_id="narrative",
        expected_revision=1,
    )
    assert store.game_state("game").revision == 1


def test_lifecycle_is_frozen_on_first_claim_and_reused_only_for_that_event(tmp_path) -> None:
    store = _store(tmp_path)
    _game(store)
    store.bind_channel(channel_id="channel", game_id="game")
    start = IncomingMessage.now(
        event_id="01-start",
        channel_id="channel",
        author_id="alice",
        content="/game start",
    )
    play = IncomingMessage.now(
        event_id="02-play",
        channel_id="channel",
        author_id="alice",
        content="Open the door",
    )
    store.enqueue(start)
    store.enqueue(play)

    first = store.claim_pending(channel_id="channel", limit=1)[0]
    assert first.routing_lifecycle is GameLifecycle.PREPARING
    store.update_game_lifecycle(
        game_id="game",
        expected_revision=0,
        lifecycle=GameLifecycle.ACTIVE,
    )
    assert first.event_id == "01-start"
    assert not store.fail_batch(event_ids=["01-start"], error="crash after commit")

    replay = store.claim_pending(channel_id="channel", limit=1)[0]
    assert replay.event_id == "01-start"
    assert replay.routing_lifecycle is GameLifecycle.PREPARING
    store.complete_batch(
        event_ids=["01-start"],
        channel_id="channel",
        contents=[],
        idempotency_key="start-complete",
    )

    next_message = store.claim_pending(channel_id="channel", limit=1)[0]
    assert next_message.event_id == "02-play"
    assert next_message.routing_lifecycle is GameLifecycle.ACTIVE


def test_v7_migration_dead_letters_attempted_rows_without_lifecycle_snapshot(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    _game(store)
    store.bind_channel(channel_id="channel", game_id="game")
    store.enqueue(
        IncomingMessage.now(
            event_id="attempted",
            channel_id="channel",
            author_id="alice",
            content="/game start",
        )
    )
    store.enqueue(
        IncomingMessage.now(
            event_id="untouched",
            channel_id="channel",
            author_id="alice",
            content="Later",
        )
    )
    store.claim_pending(channel_id="channel", limit=1)
    with store.connect() as connection:
        connection.execute("ALTER TABLE inbox_messages DROP COLUMN routing_lifecycle")
        connection.execute("DELETE FROM schema_version")
        connection.execute("INSERT INTO schema_version(version) VALUES (6)")

    store.initialize()

    failed = {row["event_id"]: row for row in store.failed_inbox()}
    assert "attempted" in failed
    assert "inspect canonical game state" in str(failed["attempted"]["error"])
    with store.connect() as connection:
        untouched = connection.execute(
            """SELECT status, routing_lifecycle FROM inbox_messages
               WHERE event_id = 'untouched'"""
        ).fetchone()
    assert dict(untouched) == {"status": "pending", "routing_lifecycle": None}

    # Explicit operator requeue acknowledges the ambiguity; the next claim snapshots live state.
    store.requeue_failed_inbox("attempted")
    assert (
        store.claim_pending(channel_id="channel", limit=1)[0].routing_lifecycle
        is GameLifecycle.PREPARING
    )


def test_finish_is_atomic_refunds_helpers_and_notifies_once(tmp_path) -> None:
    store = _store(tmp_path)
    _game(store)
    store.update_game_lifecycle(
        game_id="game",
        expected_revision=0,
        lifecycle=GameLifecycle.ACTIVE,
    )
    store.bind_channel(channel_id="channel", game_id="game")
    store.start_activity_clock(
        game_id="game",
        started_at=IncomingMessage.now(
            event_id="clock",
            channel_id="channel",
            author_id="alice",
            content="clock",
        ).created_at,
    )
    with store.transaction() as connection:
        connection.execute(
            """INSERT INTO characters
               (character_id, game_id, player_id, biography, sheet_json)
               VALUES ('helper', 'game', 'bob', 'Helper', ?)""",
            (json.dumps({"reserve_current": 6, "reserve_maximum": 7}),),
        )
    store.put_pending(
        PendingInteraction(
            interaction_id="pool",
            game_id="game",
            player_id="alice",
            scene_id=None,
            kind=PendingKind.POOL_CONFIRMATION,
            prompt="Confirm",
            payload={
                "prompt_source_event_id": "pool-prompt:part:1",
                "root_source_event_id": "pool-prompt",
            },
            origin_channel_id="channel",
        )
    )
    with store.transaction() as connection:
        connection.execute(
            """INSERT INTO roll_helpers
               (interaction_id, helper_character_id, helper_player_id)
               VALUES ('pool', 'helper', 'bob')"""
        )
        connection.executemany(
            """INSERT INTO outbox_messages
               (idempotency_key, channel_id, content, kind, source_event_id)
               VALUES (?, 'channel', 'stale prompt', 'response', ?)""",
            (
                ("root-prompt", "pool-prompt"),
                ("synthetic-prompt", "pool-prompt:part:1"),
            ),
        )

    finished = store.finish_game_and_cleanup(game_id="game", expected_revision=1)

    assert finished.lifecycle is GameLifecycle.FINISHED
    assert store.activity_state("game")["last_event_at"] is None
    pending = store.pending_by_id("pool")
    assert pending.status is PendingStatus.CANCELLED
    assert pending.payload["closed_reason"] == "game_finished"
    with store.connect() as connection:
        helper = connection.execute(
            """SELECT sheet_json, revision FROM characters
               WHERE character_id = 'helper'"""
        ).fetchone()
    assert json.loads(helper["sheet_json"])["reserve_current"] == 7
    assert helper["revision"] == 1
    notices = [
        row for row in store.pending_outbox(channel_id="channel") if row["kind"] == "system_notice"
    ]
    assert len(notices) == 1
    assert notices[0]["idempotency_key"] == "game-finished-pending-cancelled:pool"
    assert "Игра завершена" in notices[0]["content"]
    assert "канал переключился" not in notices[0]["content"]
    with store.connect() as connection:
        fenced = connection.execute(
            """SELECT source_event_id, attempts FROM outbox_messages
               WHERE idempotency_key IN ('root-prompt', 'synthetic-prompt')
               ORDER BY source_event_id"""
        ).fetchall()
    assert [(row["source_event_id"], row["attempts"]) for row in fenced] == [
        ("pool-prompt", 5),
        ("pool-prompt:part:1", 5),
    ]

    replayed = store.finish_game_and_cleanup(
        game_id="game",
        expected_revision=finished.revision,
    )
    assert replayed.lifecycle is GameLifecycle.FINISHED
    with store.connect() as connection:
        helper_after = connection.execute(
            """SELECT sheet_json, revision FROM characters
               WHERE character_id = 'helper'"""
        ).fetchone()
    assert json.loads(helper_after["sheet_json"])["reserve_current"] == 7
    assert helper_after["revision"] == 1
    notices_after = [
        row for row in store.pending_outbox(channel_id="channel") if row["kind"] == "system_notice"
    ]
    assert len(notices_after) == 1


def test_pool_expiry_checkpoints_event_and_refunds_helper_once(tmp_path) -> None:
    store = _store(tmp_path)
    _game(store)
    with store.transaction() as connection:
        connection.execute(
            """INSERT INTO characters
               (character_id, game_id, player_id, biography, sheet_json)
               VALUES ('helper', 'game', 'bob', 'Helper', ?)""",
            (json.dumps({"reserve_current": 6, "reserve_maximum": 7}),),
        )
    store.put_pending(
        PendingInteraction(
            interaction_id="expired-pool",
            game_id="game",
            player_id="alice",
            scene_id=None,
            kind=PendingKind.POOL_CONFIRMATION,
            prompt="Confirm",
        )
    )
    with store.transaction() as connection:
        connection.execute(
            """INSERT INTO roll_helpers
               (interaction_id, helper_character_id, helper_player_id)
               VALUES ('expired-pool', 'helper', 'bob')"""
        )

    store.expire_pending(
        interaction_id="expired-pool",
        player_id="alice",
        expected_revision=0,
        closed_by_event_id="late-answer",
    )

    expired = store.closed_pending_for_event(
        game_id="game",
        player_id="alice",
        event_id="late-answer",
    )
    assert expired is not None
    assert expired.status is PendingStatus.EXPIRED
    with store.connect() as connection:
        helper = connection.execute(
            "SELECT sheet_json, revision FROM characters WHERE character_id = 'helper'"
        ).fetchone()
    assert json.loads(helper["sheet_json"])["reserve_current"] == 7
    assert helper["revision"] == 1
    with pytest.raises(RuntimeError, match="revision conflict"):
        store.expire_pending(
            interaction_id="expired-pool",
            player_id="alice",
            expected_revision=0,
            closed_by_event_id="late-answer",
        )
    with store.connect() as connection:
        helper_after = connection.execute(
            "SELECT sheet_json, revision FROM characters WHERE character_id = 'helper'"
        ).fetchone()
    assert json.loads(helper_after["sheet_json"])["reserve_current"] == 7
    assert helper_after["revision"] == 1


def test_reserve_recovery_checkpoint_freezes_first_canonical_targets(tmp_path) -> None:
    store = _store(tmp_path)
    _game(store)
    with store.transaction() as connection:
        connection.executemany(
            """INSERT INTO characters
               (character_id, game_id, player_id, biography, sheet_json)
               VALUES (?, 'game', ?, 'Character', ?)""",
            (
                (
                    "alice-character",
                    "alice",
                    json.dumps({"reserve_current": 5, "reserve_maximum": 7}),
                ),
                (
                    "bob-character",
                    "bob",
                    json.dumps({"reserve_current": 5, "reserve_maximum": 7}),
                ),
            ),
        )

    canonical = store.checkpoint_reserve_recovery_decision(
        game_id="game",
        causation_id="roll:one",
        safe_rest_completed=False,
        safe_rest_reason=None,
        awards=(("alice", "Specific strong roleplay in the resolved outcome."),),
    )
    replay = store.checkpoint_reserve_recovery_decision(
        game_id="game",
        causation_id="roll:one",
        safe_rest_completed=True,
        safe_rest_reason="A different retry result must not replace the first.",
        awards=(("bob", "A different target from a nondeterministic retry."),),
    )

    assert replay == canonical
    assert store.reserve_recovery_decision("roll:one") == canonical
    assert canonical == {
        "game_id": "game",
        "safe_rest_completed": False,
        "safe_rest_reason": None,
        "awards": [
            {
                "player_id": "alice",
                "reason": "Specific strong roleplay in the resolved outcome.",
            }
        ],
    }
    with store.connect() as connection:
        count = connection.execute(
            """SELECT COUNT(*) AS count FROM domain_events
               WHERE event_type = 'reserve_recovery_decision'
                 AND causation_id = 'reserve-decision:roll:one'"""
        ).fetchone()["count"]
    assert count == 1


def test_reserve_recovery_checkpoint_rejects_ambiguous_targets(tmp_path) -> None:
    store = _store(tmp_path)
    _game(store)

    with pytest.raises(ValueError, match="no character"):
        store.checkpoint_reserve_recovery_decision(
            game_id="game",
            causation_id="roll:missing",
            safe_rest_completed=False,
            safe_rest_reason=None,
            awards=(("ghost", "No canonical character exists for this player."),),
        )
    assert store.reserve_recovery_decision("roll:missing") is None


def test_typed_decision_checkpoint_is_first_writer_and_scope_checked(tmp_path) -> None:
    store = _store(tmp_path)
    _game(store)

    canonical = store.checkpoint_decision(
        event_id="root:part:2",
        pipeline_key="action.interpretation",
        output_type="masterclaw.pipelines.action.ActionInterpretation:v1",
        game_id="game",
        input_fingerprint="scene:scene-a:revision:3|actor:revision:5",
        payload={"kind": "automatic", "difficulty": None},
    )
    replay = store.checkpoint_decision(
        event_id="root:part:2",
        pipeline_key="action.interpretation",
        output_type="masterclaw.pipelines.action.ActionInterpretation:v1",
        game_id="game",
        input_fingerprint="scene:scene-a:revision:3|actor:revision:5",
        payload={"kind": "roll", "difficulty": 4},
    )

    assert canonical == {"kind": "automatic", "difficulty": None}
    assert replay == canonical
    assert (
        store.decision_checkpoint(
            event_id="root:part:2",
            pipeline_key="action.interpretation",
            output_type="masterclaw.pipelines.action.ActionInterpretation:v1",
            game_id="game",
            input_fingerprint="scene:scene-a:revision:3|actor:revision:5",
        )
        == canonical
    )
    with pytest.raises(RuntimeError, match="output type mismatch"):
        store.decision_checkpoint(
            event_id="root:part:2",
            pipeline_key="action.interpretation",
            output_type="masterclaw.pipelines.action.ActionInterpretation:v2",
            game_id="game",
            input_fingerprint="scene:scene-a:revision:3|actor:revision:5",
        )
    with pytest.raises(RuntimeError, match="game scope mismatch"):
        store.decision_checkpoint(
            event_id="root:part:2",
            pipeline_key="action.interpretation",
            output_type="masterclaw.pipelines.action.ActionInterpretation:v1",
            game_id="other-game",
            input_fingerprint="scene:scene-a:revision:3|actor:revision:5",
        )
    with pytest.raises(RuntimeError, match="input fingerprint mismatch"):
        store.decision_checkpoint(
            event_id="root:part:2",
            pipeline_key="action.interpretation",
            output_type="masterclaw.pipelines.action.ActionInterpretation:v1",
            game_id="game",
            input_fingerprint="scene:scene-a:revision:4|actor:revision:5",
        )


def test_handler_result_checkpoint_survives_retry_and_cannot_be_replaced(tmp_path) -> None:
    store = _store(tmp_path)
    message = IncomingMessage.now(
        event_id="event",
        channel_id="channel",
        author_id="alice",
        content="Do it",
    )
    assert store.enqueue(message)
    assert store.claim_pending(channel_id="channel", limit=1)

    canonical = store.checkpoint_handler_result(
        event_id="event",
        result={
            "text": "First canonical response",
            "deliveries": [
                {
                    "channel_id": "narrative",
                    "content": "Canonical delivery",
                    "kind": "narrative",
                }
            ],
            "completion_game_id": None,
            "render_live_status": False,
        },
    )
    assert store.fail_batch(event_ids=["event"], error="complete failed", retry=True) is False

    assert store.handler_result("event") == canonical
    assert store.claim_pending(channel_id="channel", limit=1)
    replay = store.checkpoint_handler_result(
        event_id="event",
        result={
            "text": "A nondeterministic retry must not win",
            "deliveries": [],
            "completion_game_id": None,
            "render_live_status": True,
        },
    )
    assert replay == canonical


def test_handler_result_checkpoint_survives_startup_recovery(tmp_path) -> None:
    store = _store(tmp_path)
    assert store.enqueue(
        IncomingMessage.now(
            event_id="interrupted",
            channel_id="channel",
            author_id="alice",
            content="Do it",
        )
    )
    assert store.claim_pending(channel_id="channel", limit=1)
    canonical = store.checkpoint_handler_result(
        event_id="interrupted",
        result={
            "text": "Already handled",
            "deliveries": [],
            "completion_game_id": None,
            "render_live_status": False,
        },
    )

    assert store.recover_interrupted_work() == 1

    assert store.handler_result("interrupted") == canonical
    assert store.claim_pending(channel_id="channel", limit=1)


def test_lifecycle_event_operation_replays_original_result_without_overwriting_newer_state(
    tmp_path,
) -> None:
    store = _store(tmp_path, "lifecycle-operation.sqlite3")
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE, locale="en"))
    store.bind_channel(channel_id="channel", game_id="game")
    _claim_event(store, event_id="pause")

    committed = store.transition_game_for_event(
        event_id="pause",
        operation_type="pause_game",
        game_id="game",
        channel_id="channel",
        expected_revision=0,
    )
    assert committed["lifecycle"] == "paused"
    assert committed["revision"] == 1
    store.update_game_lifecycle(
        game_id="game",
        expected_revision=1,
        lifecycle=GameLifecycle.ACTIVE,
    )
    store.fail_batch(event_ids=["pause"], error="completion crash")
    store.claim_pending(channel_id="channel", limit=1)

    replay = store.transition_game_for_event(
        event_id="pause",
        operation_type="pause_game",
        game_id="game",
        channel_id="channel",
        expected_revision=2,
    )

    assert replay == committed
    live = store.game_state("game")
    assert live is not None
    assert live.lifecycle is GameLifecycle.ACTIVE
    assert live.revision == 2
    assert store.event_operation("pause")["operation_type"] == "pause_game"


def test_configuration_event_operation_does_not_reapply_over_admin_change(tmp_path) -> None:
    store = _store(tmp_path, "configuration-operation.sqlite3")
    _game(store)
    store.bind_channel(channel_id="channel", game_id="game")
    _claim_event(store, event_id="configure")

    committed = store.configure_game_for_event(
        event_id="configure",
        game_id="game",
        channel_id="channel",
        setting="progression",
        value=True,
        expected_revision=0,
    )
    assert committed["progression_enabled"] is True
    store.set_progression_enabled(game_id="game", enabled=False, expected_revision=1)
    store.fail_batch(event_ids=["configure"], error="completion crash")
    store.claim_pending(channel_id="channel", limit=1)

    replay = store.configure_game_for_event(
        event_id="configure",
        game_id="game",
        channel_id="channel",
        setting="progression",
        value=True,
        expected_revision=2,
    )

    assert replay == committed
    live = store.game_state("game")
    assert live is not None
    assert live.progression_enabled is False
    assert live.revision == 2


def test_unbind_event_operation_preserves_external_rebind_and_fences_completion(
    tmp_path,
) -> None:
    store = _store(tmp_path, "unbind-operation.sqlite3")
    store.create_world(WorldState("world-a", "World A"))
    store.create_world(WorldState("world-b", "World B"))
    store.create_game(GameState("game-a", "world-a", GameLifecycle.ACTIVE, locale="en"))
    store.create_game(GameState("game-b", "world-b", GameLifecycle.ACTIVE, locale="en"))
    store.bind_channel(channel_id="channel", game_id="game-a")
    _claim_event(store, event_id="unbind")

    committed = store.unbind_channel_for_event(
        event_id="unbind",
        operation_type="new_session",
        channel_id="channel",
        game_id="game-a",
    )
    store.bind_channel(channel_id="channel", game_id="game-b")
    store.fail_batch(event_ids=["unbind"], error="completion crash")
    store.claim_pending(channel_id="channel", limit=1)

    replay = store.unbind_channel_for_event(
        event_id="unbind",
        operation_type="new_session",
        channel_id="channel",
        game_id="game-a",
    )
    assert replay == committed
    assert store.channel_state("channel").game_id == "game-b"
    store.complete_batch(
        event_ids=["unbind"],
        channel_id="channel",
        contents=["stale new-session success"],
        idempotency_key="unbind",
        assistant_turns=[("unbind", "alice", "stale new-session success")],
    )
    outbox = store.pending_outbox(channel_id="channel")
    assert len(outbox) == 1
    assert outbox[0]["kind"] == "system_notice"
    assert "Do not repeat the action" in outbox[0]["content"]
    assert store.channel_state("channel").game_id == "game-b"


def test_own_unbind_marker_allows_its_success_response_while_channel_is_unbound(
    tmp_path,
) -> None:
    store = _store(tmp_path, "own-unbind-operation.sqlite3")
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE, locale="en"))
    store.bind_channel(channel_id="channel", game_id="game")
    _claim_event(store, event_id="unbind-own")
    store.unbind_channel_for_event(
        event_id="unbind-own",
        operation_type="unbind_game",
        channel_id="channel",
        game_id="game",
    )

    store.complete_batch(
        event_ids=["unbind-own"],
        channel_id="channel",
        contents=["Game detached."],
        idempotency_key="unbind-own",
        assistant_turns=[("unbind-own", "alice", "Game detached.")],
    )

    assert store.channel_state("channel").game_id is None
    outbox = store.pending_outbox(channel_id="channel")
    assert [row["content"] for row in outbox] == ["Game detached."]


def test_schema_v10_migrates_replay_journals_operations_provider_budget_and_quality(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    _game(store)
    store.bind_channel(channel_id="channel", game_id="game")
    assert store.enqueue(
        IncomingMessage.now(
            event_id="v7-attempted",
            channel_id="channel",
            author_id="alice",
            content="Queued on v7",
        )
    )
    with store.transaction() as connection:
        connection.execute("ALTER TABLE inbox_messages DROP COLUMN handler_result_json")
        connection.execute("DROP TABLE decision_checkpoints")
        connection.execute(
            """UPDATE inbox_messages
               SET attempts = 1, routing_lifecycle = NULL
               WHERE event_id = 'v7-attempted'"""
        )
        connection.execute("DELETE FROM schema_version")
        connection.execute("INSERT INTO schema_version(version) VALUES (7)")

    store.initialize()

    with store.connect() as connection:
        inbox_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(inbox_messages)")
        }
        decision_table = connection.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type = 'table' AND name = 'decision_checkpoints'"""
        ).fetchone()
        decision_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(decision_checkpoints)")
        }
        operation_table = connection.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type = 'table' AND name = 'event_operations'"""
        ).fetchone()
        lexicon_table = connection.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type = 'table' AND name = 'lexicon_candidates'"""
        ).fetchone()
        lexicon_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(lexicon_candidates)")
        }
        attempted = connection.execute(
            """SELECT status, attempts, routing_lifecycle, error
               FROM inbox_messages WHERE event_id = 'v7-attempted'"""
        ).fetchone()
    assert "handler_result_json" in inbox_columns
    assert decision_table is not None
    assert "input_fingerprint" in decision_columns
    assert "provider_attempts" in inbox_columns
    assert operation_table is not None
    assert lexicon_table is not None
    assert {
        "event_id",
        "scenario",
        "command",
        "normalized_phrase",
        "confidence",
        "created_at",
    } <= lexicon_columns
    assert dict(attempted) == {
        "status": "pending",
        "attempts": 1,
        "routing_lifecycle": None,
        "error": None,
    }
    assert store.schema_version() == 10
