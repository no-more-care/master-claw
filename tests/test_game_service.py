from datetime import UTC, datetime

import pytest

from masterclaw.app.game_service import GameService
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.mechanics import CharacterSheet, Flag, FlagType, Trait
from masterclaw.domain.models import GameLifecycle
from masterclaw.domain.state import GameState, NarratorRightsLevel, ReserveRecoveryMode
from masterclaw.storage.sqlite import SQLiteStore


def add_character(store, game_id, scene_id, reserve=7):
    sheet = CharacterSheet(
        "Hero",
        tuple(Trait(f"T{i}", 3, tuple(f"A{i}.{n}" for n in range(3))) for i in range(6)),
        (
            Flag("Friend", FlagType.RELATIONSHIP),
            Flag("Goal", FlagType.GOAL),
            Flag("Belief", FlagType.BELIEF),
        ),
        reserve_current=reserve,
    )
    store.create_character(CharacterState("hero", game_id, "alice", "Bio", sheet))
    if scene_id is not None:
        store.place_player(game_id=game_id, player_id="alice", scene_id=scene_id)


def test_game_cannot_start_until_narrative_channel_and_character_exist(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    service = GameService(store)
    service.create_world(world_id="my_world", title="My World")
    service.prepare_game(game_id="my_game", world_id="my_world", channel_id="game-channel")
    assert service.readiness("my_game").missing == (
        "narrative_channel",
        "characters",
    )
    with pytest.raises(ValueError, match="not ready"):
        service.start_game("my_game")


def test_ready_game_transitions_to_active(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    service = GameService(store)
    service.create_world(world_id="my_world", title="My World")
    service.prepare_game(game_id="my_game", world_id="my_world", channel_id="game-channel")
    game = store.game_state("my_game")
    store.set_narrative_channel(
        game_id="my_game", channel_id="narrative", expected_revision=game.revision
    )
    store.create_scene(scene_id="opening", game_id="my_game", title="Opening Scene")
    add_character(store, "my_game", "opening")
    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    active = service.start_game("my_game", started_at=started_at)
    assert active.lifecycle is GameLifecycle.ACTIVE
    assert store.activity_state("my_game")["last_event_at"] == started_at.isoformat()
    # Replaying the same start event after a crash before inbox completion is harmless.
    replayed = service.start_game("my_game", started_at=started_at)
    assert replayed.lifecycle is GameLifecycle.ACTIVE
    assert store.activity_state("my_game")["last_event_at"] == started_at.isoformat()


def test_all_participants_share_world_mutation_capability_at_service_boundary(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    service = GameService(store)
    # No operator/role parameter exists by design: Discord participants use the same gate.
    assert service.create_world(world_id="shared", title="Shared").world_id == "shared"


def test_progression_is_configured_only_before_game_start(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    service = GameService(store)
    service.create_world(world_id="world", title="World")
    service.prepare_game(game_id="game", world_id="world", channel_id="game-channel")
    configured = service.configure_progression(game_id="game", enabled=True)
    assert configured.progression_enabled is True
    store.set_narrative_channel(
        game_id="game", channel_id="narrative", expected_revision=configured.revision
    )
    store.create_scene(scene_id="opening", game_id="game", title="Opening")
    add_character(store, "game", "opening")
    service.start_game("game")
    with pytest.raises(RuntimeError, match="only during preparation"):
        service.configure_progression(game_id="game", enabled=False)


def test_reserve_recovery_mode_can_be_selected_during_preparation(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    service = GameService(store)
    service.create_world(world_id="world", title="World")
    service.prepare_game(game_id="game", world_id="world", channel_id="game-channel")

    configured = service.configure_reserve_recovery(
        game_id="game", mode=ReserveRecoveryMode.SAFE_REST
    )

    assert configured.reserve_recovery_mode is ReserveRecoveryMode.SAFE_REST


def test_game_preparation_accepts_world_policy_defaults(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    service = GameService(store)
    service.create_world(world_id="world", title="World")

    game = service.prepare_game(
        game_id="game",
        world_id="world",
        channel_id="game-channel",
        progression_enabled=True,
        narrator_rights_level=NarratorRightsLevel.SIGNIFICANT,
        reserve_recovery_mode=ReserveRecoveryMode.SAFE_REST,
    )

    assert game.progression_enabled is True
    assert game.narrator_rights_level is NarratorRightsLevel.SIGNIFICANT
    assert game.reserve_recovery_mode is ReserveRecoveryMode.SAFE_REST


def test_prepare_game_completes_channel_binding_after_partial_retry(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    service = GameService(store)
    service.create_world(world_id="world", title="World")
    # Simulate a crash after the game row was committed but before bind_channel().
    from masterclaw.domain.state import GameState

    store.create_game(GameState("game", "world", GameLifecycle.PREPARING))
    assert store.channel_state("game-channel").game_id is None

    recovered = service.prepare_game(game_id="game", world_id="world", channel_id="game-channel")

    assert recovered.game_id == "game"
    assert store.channel_state("game-channel").game_id == "game"


def test_prepare_game_cas_does_not_replace_unrelated_binding_or_leave_orphan(
    tmp_path,
) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    service = GameService(store)
    service.create_world(world_id="selected-world", title="Selected")
    service.create_world(world_id="other-world", title="Other")
    store.create_game(GameState("other-game", "other-world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="game-channel", game_id="other-game")

    with pytest.raises(RuntimeError, match="binding changed"):
        service.prepare_game(
            game_id="game_event",
            world_id="selected-world",
            channel_id="game-channel",
        )

    assert store.channel_state("game-channel").game_id == "other-game"
    assert store.game_state("game_event") is None


def test_prepare_game_same_game_replay_is_idempotent(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    service = GameService(store)
    service.create_world(world_id="world", title="World")

    first = service.prepare_game(
        game_id="game",
        world_id="world",
        channel_id="game-channel",
    )
    replay = service.prepare_game(
        game_id="game",
        world_id="world",
        channel_id="game-channel",
    )

    assert replay == first
    assert store.channel_state("game-channel").game_id == "game"


def test_start_materializes_every_world_location_as_a_travel_scene(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    service = GameService(store)
    world = service.create_world(world_id="world", title="World")
    store.update_world_content(
        world_id="world",
        expected_revision=world.revision,
        status="approved",
        content={
            "locations": [
                {"id": "harbor", "name": "Harbor", "description": "A stormy harbor."},
                {"id": "tower", "name": "Tower", "description": "A sealed tower."},
            ]
        },
    )
    service.prepare_game(game_id="game", world_id="world", channel_id="game-channel")
    game = store.game_state("game")
    assert game is not None
    store.set_narrative_channel(
        game_id="game", channel_id="narrative", expected_revision=game.revision
    )
    add_character(store, "game", None)

    service.start_game("game", started_at=datetime(2026, 1, 1, tzinfo=UTC))

    catalog = store.scene_catalog("game")
    assert {item["title"] for item in catalog} == {"Harbor", "Tower"}
    assert store.scene_projection(game_id="game", player_id="alice")["title"] == "Harbor"


def test_pause_resume_finish_and_unbind_preserve_session_state(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    service = GameService(store)
    service.create_world(world_id="world", title="World")
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="game-channel", game_id="game")
    started = datetime(2026, 1, 1, tzinfo=UTC)
    resumed = datetime(2026, 1, 2, tzinfo=UTC)
    store.start_activity_clock(game_id="game", started_at=started)

    assert service.pause_game("game").lifecycle is GameLifecycle.PAUSED
    assert store.activity_state("game")["last_event_at"] is None
    assert service.resume_game("game", resumed_at=resumed).lifecycle is GameLifecycle.ACTIVE
    assert store.activity_state("game")["last_event_at"] == resumed.isoformat()
    assert service.finish_game("game").lifecycle is GameLifecycle.FINISHED
    assert store.activity_state("game")["last_event_at"] is None

    service.unbind_channel(channel_id="game-channel", game_id="game")
    assert store.channel_state("game-channel").game_id is None
    assert store.game_state("game").lifecycle is GameLifecycle.FINISHED


def test_safe_rest_recovery_restores_everything_when_system_adjudicates_it(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    service = GameService(store)
    service.create_world(world_id="world", title="World")
    service.prepare_game(
        game_id="game",
        world_id="world",
        channel_id="game-channel",
        reserve_recovery_mode=ReserveRecoveryMode.SAFE_REST,
    )
    store.create_scene(scene_id="base", game_id="game", title="Safe Base")
    add_character(store, "game", "base", reserve=2)
    assert (
        service.restore_reserve_for_safe_rest(
            game_id="game",
            reason="overnight at the base",
            causation_id="rest-system",
        )
        == 1
    )
    assert store.character_for_player(game_id="game", player_id="alice").sheet.reserve_current == 7


def test_system_roleplay_award_recovers_exactly_one_and_respects_game_mode(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    service = GameService(store)
    service.create_world(world_id="world", title="World")
    service.prepare_game(
        game_id="game",
        world_id="world",
        channel_id="game-channel",
        reserve_recovery_mode=ReserveRecoveryMode.ROLEPLAY_AWARD,
    )
    store.create_scene(scene_id="road", game_id="game", title="Road")
    add_character(store, "game", "road", reserve=2)
    assert service.award_reserve_die(
        game_id="game",
        player_id="alice",
        reason="strong roleplay",
        causation_id="award-1",
    ) == (2, 3)
    with pytest.raises(ValueError, match="safe-rest reserve recovery is disabled"):
        service.restore_reserve_for_safe_rest(
            game_id="game",
            reason="overnight camp",
            causation_id="rest-disabled",
        )
