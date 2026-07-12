import sqlite3

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
