import pytest

from masterclaw.domain.characters import CharacterState, Condition, PlotItem
from masterclaw.domain.mechanics import (
    CharacterSheet,
    TemporaryBonus,
    TemporaryBonusType,
)
from masterclaw.domain.models import GameLifecycle
from masterclaw.domain.outcomes import CanonicalOutcomePatch, SceneNpcState
from masterclaw.domain.state import GameState, WorldState
from masterclaw.storage.sqlite import SQLiteStore


def setup_store(tmp_path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.create_scene(
        scene_id="room",
        game_id="game",
        title="Room",
        state={
            "facts": ["The archive door is sealed"],
            "npcs": [{"id": "guard", "name": "Guard", "state": "Hostile"}],
            "threads": ["Who sealed the archive?"],
        },
    )
    store.create_scene(scene_id="archive", game_id="game", title="Archive")
    store.create_character(
        CharacterState(
            "hero",
            "game",
            "alice",
            "Bio",
            CharacterSheet("Hero", (), ()),
            conditions=(Condition("Pinned", "falling stones"),),
            plot_items=(PlotItem("Old token"),),
        )
    )
    store.place_player(game_id="game", player_id="alice", scene_id="room")
    return store


def full_patch() -> CanonicalOutcomePatch:
    return CanonicalOutcomePatch(
        summary="The hero reaches the archive with the key.",
        add_facts=("The archive door is open",),
        remove_facts=("The archive door is sealed",),
        add_actor_conditions=(Condition("Winded", "archive chase"),),
        remove_actor_conditions=("Pinned",),
        add_actor_plot_items=(PlotItem("Brass key", "Opens the archive lift"),),
        remove_actor_plot_items=("Old token",),
        move_actor_to_scene_id="archive",
        upsert_scene_npcs=(SceneNpcState("guard", "Guard", "Alert but disarmed"),),
        open_threads=("Who hired the courier?",),
        close_threads=("Who sealed the archive?",),
        grant_temporary_bonus=TemporaryBonus(
            "courier-route",
            TemporaryBonusType.EXTRA_DIE,
            "Following the courier through the archive",
        ),
    )


def source_fiction_expectations(store: SQLiteStore) -> dict[str, object]:
    scene = store.scene_projection(game_id="game", player_id="alice")
    assert scene is not None
    return {
        "expected_actor_location_revision": int(scene["location_revision"]),
        "expected_scene_participants": tuple(scene["participants"]),
    }


def test_outcome_patch_updates_actor_scene_and_location_atomically(tmp_path) -> None:
    store = setup_store(tmp_path)
    source_fiction = source_fiction_expectations(store)

    store.apply_outcome_patch(
        game_id="game",
        scene_id="room",
        expected_scene_revision=0,
        actor_character_id="hero",
        expected_actor_revision=0,
        **source_fiction,
        causation_id="outcome:1",
        patch=full_patch(),
    )

    room = store.scene_by_id(game_id="game", scene_id="room")
    assert room["state"]["facts"] == ["The archive door is open"]
    assert room["state"]["npcs"] == [
        {"id": "guard", "name": "Guard", "state": "Alert but disarmed"}
    ]
    assert room["state"]["threads"] == ["Who hired the courier?"]
    assert room["scene_revision"] == 1
    actor = store.character_for_player(game_id="game", player_id="alice")
    assert [condition.text for condition in actor.conditions] == ["Winded"]
    assert [item.name for item in actor.plot_items] == ["Brass key"]
    assert actor.sheet.temporary_bonuses[0].bonus_id == "courier-route"
    assert actor.revision == 1
    assert store.scene_projection(game_id="game", player_id="alice")["scene_id"] == "archive"

    # A retry sees the causation event before checking stale revisions.
    store.apply_outcome_patch(
        game_id="game",
        scene_id="room",
        expected_scene_revision=0,
        actor_character_id="hero",
        expected_actor_revision=0,
        **source_fiction,
        causation_id="outcome:1",
        patch=full_patch(),
    )
    events = [
        event
        for event in store.recent_domain_events(game_id="game", limit=20)
        if event["causation_id"] == "outcome:1"
    ]
    assert len(events) == 1


def test_invalid_outcome_removal_rolls_back_every_aggregate(tmp_path) -> None:
    store = setup_store(tmp_path)
    patch = CanonicalOutcomePatch(
        summary="Invalid removal",
        add_actor_plot_items=(PlotItem("Brass key"),),
        remove_actor_conditions=("Unknown condition",),
    )

    with pytest.raises(ValueError, match="unknown actor conditions"):
        store.apply_outcome_patch(
            game_id="game",
            scene_id="room",
            expected_scene_revision=0,
            actor_character_id="hero",
            expected_actor_revision=0,
            **source_fiction_expectations(store),
            causation_id="outcome:bad",
            patch=patch,
        )

    actor = store.character_for_player(game_id="game", player_id="alice")
    assert [condition.text for condition in actor.conditions] == ["Pinned"]
    assert [item.name for item in actor.plot_items] == ["Old token"]
    assert not store.has_scene_patch("outcome:bad")


def test_outcome_movement_cannot_target_scene_from_another_game(tmp_path) -> None:
    store = setup_store(tmp_path)
    store.create_world(WorldState("other-world", "Other"))
    store.create_game(GameState("other-game", "other-world", GameLifecycle.ACTIVE))
    store.create_scene(scene_id="foreign", game_id="other-game", title="Foreign")

    with pytest.raises(ValueError, match="target scene does not exist"):
        store.apply_outcome_patch(
            game_id="game",
            scene_id="room",
            expected_scene_revision=0,
            actor_character_id="hero",
            expected_actor_revision=0,
            **source_fiction_expectations(store),
            causation_id="outcome:foreign",
            patch=CanonicalOutcomePatch(
                summary="Invalid movement",
                move_actor_to_scene_id="foreign",
            ),
        )

    assert store.scene_projection(game_id="game", player_id="alice")["scene_id"] == "room"
    assert not store.has_scene_patch("outcome:foreign")


def test_secret_reveals_commit_atomically_with_outcome_and_are_idempotent(tmp_path) -> None:
    store = setup_store(tmp_path)
    source_fiction = source_fiction_expectations(store)
    patch = CanonicalOutcomePatch(summary="The archive inscription is decoded.")
    reveals = {
        "archive-founder": "The guard founded the archive under another name.",
        "sealed-vault": "The brass key opens the sealed vault.",
    }

    store.apply_outcome_patch(
        game_id="game",
        scene_id="room",
        expected_scene_revision=0,
        actor_character_id="hero",
        expected_actor_revision=0,
        **source_fiction,
        causation_id="outcome:reveal",
        patch=patch,
        secret_reveals=reveals,
    )
    store.apply_outcome_patch(
        game_id="game",
        scene_id="room",
        expected_scene_revision=0,
        actor_character_id="hero",
        expected_actor_revision=0,
        **source_fiction,
        causation_id="outcome:reveal",
        patch=patch,
        secret_reveals=reveals,
    )

    assert store.revealed_secret_ids("game") == {"archive-founder", "sealed-vault"}
    events = [
        event
        for event in store.recent_domain_events(game_id="game", limit=20)
        if event["causation_id"] == "outcome:reveal"
    ]
    assert [event["event_type"] for event in events] == [
        "scene_patched",
        "secret_revealed",
    ]
    assert events[-1]["payload"] == [
        {"secret_id": secret_id, "text": text} for secret_id, text in reveals.items()
    ]


def test_invalid_secret_reveal_rolls_back_scene_patch_event(tmp_path) -> None:
    store = setup_store(tmp_path)

    with pytest.raises(ValueError, match="cannot be empty"):
        store.apply_outcome_patch(
            game_id="game",
            scene_id="room",
            expected_scene_revision=0,
            actor_character_id="hero",
            expected_actor_revision=0,
            **source_fiction_expectations(store),
            causation_id="outcome:blank-reveal",
            patch=CanonicalOutcomePatch(summary="Invalid reveal"),
            secret_reveals={"": "hidden"},
        )

    assert not store.has_scene_patch("outcome:blank-reveal")
    assert store.revealed_secret_ids("game") == set()


@pytest.mark.parametrize("participant_drift", ["join", "leave"])
def test_outcome_patch_rejects_scene_participant_drift_atomically(
    tmp_path,
    participant_drift: str,
) -> None:
    store = setup_store(tmp_path)
    store.create_character(
        CharacterState(
            "companion",
            "game",
            "bob",
            "Bio",
            CharacterSheet("Companion", (), ()),
        )
    )
    if participant_drift == "leave":
        store.place_player(game_id="game", player_id="bob", scene_id="room")
    source_fiction = source_fiction_expectations(store)
    store.place_player(
        game_id="game",
        player_id="bob",
        scene_id="room" if participant_drift == "join" else "archive",
    )

    with pytest.raises(RuntimeError, match="scene participants changed"):
        store.apply_outcome_patch(
            game_id="game",
            scene_id="room",
            expected_scene_revision=0,
            actor_character_id="hero",
            expected_actor_revision=0,
            **source_fiction,
            causation_id=f"outcome:participant-{participant_drift}",
            patch=CanonicalOutcomePatch(
                summary="This plan was based on a stale audience.",
                add_facts=("A stale fact must not commit",),
            ),
        )

    room = store.scene_by_id(game_id="game", scene_id="room")
    assert room is not None
    assert room["state"]["facts"] == ["The archive door is sealed"]
    assert not store.has_scene_patch(f"outcome:participant-{participant_drift}")


def test_outcome_patch_rejects_actor_location_revision_drift_atomically(tmp_path) -> None:
    store = setup_store(tmp_path)
    source_fiction = source_fiction_expectations(store)
    with store.transaction() as connection:
        connection.execute(
            """UPDATE player_locations SET revision = revision + 1
               WHERE game_id = ? AND player_id = ? AND scene_id = ?""",
            ("game", "alice", "room"),
        )

    with pytest.raises(RuntimeError, match="actor location revision conflict"):
        store.apply_outcome_patch(
            game_id="game",
            scene_id="room",
            expected_scene_revision=0,
            actor_character_id="hero",
            expected_actor_revision=0,
            **source_fiction,
            causation_id="outcome:location-revision-drift",
            patch=CanonicalOutcomePatch(
                summary="This plan was based on a stale location revision.",
                add_facts=("A stale fact must not commit",),
            ),
        )

    room = store.scene_by_id(game_id="game", scene_id="room")
    assert room is not None
    assert room["state"]["facts"] == ["The archive door is sealed"]
    assert not store.has_scene_patch("outcome:location-revision-drift")
