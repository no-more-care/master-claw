from masterclaw.domain.models import GameLifecycle
from masterclaw.domain.state import GameState, WorldState
from masterclaw.storage.sqlite import SQLiteStore


def test_players_in_different_scenes_receive_different_projections(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.create_scene(scene_id="north", game_id="game", title="North", state={"weather": "snow"})
    store.create_scene(scene_id="south", game_id="game", title="South", state={"weather": "rain"})
    store.place_player(game_id="game", player_id="alice", scene_id="north")
    store.place_player(game_id="game", player_id="bob", scene_id="south")
    assert store.scene_projection(game_id="game", player_id="alice")["state"] == {"weather": "snow"}
    assert store.scene_projection(game_id="game", player_id="bob")["participants"] == ["bob"]


def test_players_in_same_scene_are_visible_to_each_other(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.create_scene(scene_id="room", game_id="game", title="Room")
    store.place_player(game_id="game", player_id="alice", scene_id="room")
    store.place_player(game_id="game", player_id="bob", scene_id="room")
    assert store.scene_projection(game_id="game", player_id="alice")["participants"] == [
        "alice",
        "bob",
    ]


def test_independent_scene_revisions_do_not_conflict_but_stale_same_scene_does(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.create_scene(scene_id="north", game_id="game", title="North", state={"facts": []})
    store.create_scene(scene_id="south", game_id="game", title="South", state={"facts": []})
    store.apply_scene_patch(
        game_id="game",
        scene_id="north",
        expected_revision=0,
        causation_id="north-action",
        summary="north changes",
        add_facts=["North gate opens"],
        remove_facts=[],
    )
    store.apply_scene_patch(
        game_id="game",
        scene_id="south",
        expected_revision=0,
        causation_id="south-action",
        summary="south changes",
        add_facts=["South bell rings"],
        remove_facts=[],
    )
    import pytest

    with pytest.raises(RuntimeError, match="scene revision conflict"):
        store.apply_scene_patch(
            game_id="game",
            scene_id="north",
            expected_revision=0,
            causation_id="stale-north",
            summary="stale",
            add_facts=["Impossible stale fact"],
            remove_facts=[],
        )
