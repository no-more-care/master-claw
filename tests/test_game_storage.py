import sqlite3
from datetime import UTC, datetime

import pytest

from masterclaw.domain.models import GameLifecycle
from masterclaw.domain.state import GameState, PendingInteraction, PendingKind, WorldState
from masterclaw.storage.sqlite import SQLiteStore


def test_game_binding_drives_channel_state(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.PREPARING))
    store.bind_channel(channel_id="channel", game_id="game")
    state = store.channel_state("channel")
    assert state.game_id == "game"
    assert state.lifecycle is GameLifecycle.PREPARING


def test_channel_unbind_is_compare_and_delete_and_keeps_monitoring(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.PREPARING))
    store.create_game(GameState("other", "world", GameLifecycle.PREPARING))
    store.bind_channel(channel_id="channel", game_id="game")
    store.enable_channel_monitoring("channel")

    with pytest.raises(RuntimeError, match="binding changed"):
        store.unbind_channel(channel_id="channel", expected_game_id="other")
    assert store.channel_state("channel").game_id == "game"

    store.unbind_channel(channel_id="channel", expected_game_id="game")
    assert store.channel_state("channel").game_id is None
    assert store.channel_monitoring_enabled("channel")
    store.unbind_channel(channel_id="channel", expected_game_id="game")


def test_channel_unbind_releases_only_the_matching_narrative_channel(tmp_path) -> None:
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
    store.bind_channel(channel_id="narrative", game_id="game")
    store.bind_channel(channel_id="secondary", game_id="game")

    store.unbind_channel(channel_id="secondary", expected_game_id="game")

    assert store.channel_state("secondary").game_id is None
    assert store.channel_state("narrative").game_id == "game"
    assert store.game_state("game").narrative_channel_id == "narrative"

    store.unbind_channel(channel_id="narrative", expected_game_id="game")

    assert store.channel_state("narrative").game_id is None
    assert store.game_state("game").narrative_channel_id is None


def test_unbinding_one_of_multiple_channels_keeps_session_but_closes_origin_work(
    tmp_path,
) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="primary", game_id="game")
    store.bind_channel(channel_id="secondary", game_id="game")
    store.start_activity_clock(
        game_id="game",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    store.put_pending(
        PendingInteraction(
            "choice",
            "game",
            "alice",
            None,
            PendingKind.CHOICE,
            "Choose",
            origin_channel_id="primary",
        )
    )

    store.unbind_channel(channel_id="primary", expected_game_id="game")

    assert store.activity_state("game")["last_event_at"] is not None
    closed = store.pending_by_id("choice")
    assert closed.status.value == "cancelled"
    assert closed.payload["closed_reason"] == "origin_channel_unbound"


def test_late_frozen_replay_closes_new_exact_pending_after_prior_was_cancelled(
    tmp_path,
) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world-a", "World A"))
    store.create_world(WorldState("world-b", "World B"))
    store.create_game(GameState("game-a", "world-a", GameLifecycle.ACTIVE))
    store.create_game(GameState("game-b", "world-b", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="game-a")
    store.put_pending(
        PendingInteraction(
            "first",
            "game-a",
            "alice",
            None,
            PendingKind.CHOICE,
            "Choose",
            payload={"prompt_source_event_id": "frozen-event"},
            origin_channel_id="channel",
        )
    )

    store.bind_channel(channel_id="channel", game_id="game-b")
    assert store.pending_by_id("first").status.value == "cancelled"

    # A failed completion can replay the same frozen A turn after its first decision was closed.
    store.start_activity_clock(
        game_id="game-a",
        started_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    store.put_pending(
        PendingInteraction(
            "second",
            "game-a",
            "alice",
            None,
            PendingKind.CHOICE,
            "Choose again",
            payload={"prompt_source_event_id": "frozen-event"},
            origin_channel_id="channel",
        )
    )

    assert store.cancel_source_pending_if_origin_unbound(
        game_id="game-a",
        player_id="alice",
        origin_channel_id="channel",
        source_event_id="frozen-event",
    )

    second = store.pending_by_id("second")
    assert second.status.value == "cancelled"
    assert second.payload["closed_reason"] == "origin_channel_detached_before_completion"
    assert store.activity_state("game-a")["last_event_at"] is None


def test_channel_unbind_rolls_back_if_narrative_release_fails(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(
        GameState(
            "game",
            "world",
            GameLifecycle.ACTIVE,
            narrative_channel_id="channel",
        )
    )
    store.bind_channel(channel_id="channel", game_id="game")
    with store.transaction() as connection:
        connection.execute(
            """CREATE TRIGGER reject_narrative_release
               BEFORE UPDATE OF narrative_channel_id ON games
               BEGIN
                 SELECT RAISE(ABORT, 'narrative release rejected');
               END"""
        )

    with pytest.raises(sqlite3.IntegrityError, match="narrative release rejected"):
        store.unbind_channel(channel_id="channel", expected_game_id="game")

    assert store.channel_state("channel").game_id == "game"
    assert store.game_state("game").narrative_channel_id == "channel"


def test_paused_activity_clock_keeps_totals_and_resumes_from_new_timestamp(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    started = datetime(2026, 1, 1, tzinfo=UTC)
    store.start_activity_clock(game_id="game", started_at=started)
    store.record_activity(
        game_id="game",
        occurred_at=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
    )
    before = store.activity_state("game")

    store.pause_activity_clock(game_id="game")
    paused = store.activity_state("game")
    store.pause_activity_clock(game_id="game")
    paused_again = store.activity_state("game")

    assert paused["last_event_at"] is None
    assert paused["active_seconds"] == before["active_seconds"]
    assert paused["awarded_intervals"] == before["awarded_intervals"]
    assert paused_again == paused

    resumed_at = datetime(2026, 1, 2, tzinfo=UTC)
    store.start_activity_clock(game_id="game", started_at=resumed_at)
    assert store.activity_state("game")["last_event_at"] == resumed_at.isoformat()


def test_game_update_uses_optimistic_revision(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.PREPARING))
    store.update_game_lifecycle(game_id="game", expected_revision=0, lifecycle=GameLifecycle.ACTIVE)
    assert store.game_state("game").revision == 1
    with pytest.raises(RuntimeError, match="revision conflict"):
        store.update_game_lifecycle(
            game_id="game", expected_revision=0, lifecycle=GameLifecycle.PAUSED
        )


def test_only_one_open_pending_interaction_per_player(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    first = PendingInteraction(
        "pending-1", "game", "alice", "scene", PendingKind.CLARIFICATION, "Which door?"
    )
    store.put_pending(first)
    assert store.open_pending(game_id="game", player_id="alice") == first
    with pytest.raises(sqlite3.IntegrityError):
        store.put_pending(
            PendingInteraction("pending-2", "game", "alice", "scene", PendingKind.CHOICE, "Choose")
        )
