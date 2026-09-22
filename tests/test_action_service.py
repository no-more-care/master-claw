from datetime import UTC, datetime

from masterclaw.app.action_service import ActionService
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.mechanics import (
    CharacterSheet,
    Flag,
    FlagType,
    NarratorRights,
    PoolProposal,
    TemporaryBonus,
    TemporaryBonusType,
    Trait,
)
from masterclaw.domain.models import GameLifecycle, IncomingMessage
from masterclaw.domain.state import (
    GameState,
    NarratorRightsLevel,
    PendingKind,
    PendingStatus,
    WorldState,
)
from masterclaw.storage.sqlite import SQLiteStore


def setup_store(tmp_path, *, temporary_bonuses=()) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    sheet = CharacterSheet(
        "Hero",
        tuple(Trait(f"Trait {i}", 3, tuple(f"Aspect {i}.{n}" for n in range(3))) for i in range(6)),
        (
            Flag("Friend", FlagType.RELATIONSHIP),
            Flag("Goal", FlagType.GOAL),
            Flag("Belief", FlagType.BELIEF),
        ),
        reserve_current=5,
        temporary_bonuses=tuple(temporary_bonuses),
    )
    store.create_character(CharacterState("hero", "game", "alice", "Bio", sheet))
    store.create_scene(scene_id="scene", game_id="game", title="Scene")
    store.place_player(game_id="game", player_id="alice", scene_id="scene")
    return store


def add_helper(store: SQLiteStore) -> None:
    actor = store.character_for_player(game_id="game", player_id="alice")
    helper_sheet = CharacterSheet(
        "Helper",
        actor.sheet.traits,
        actor.sheet.flags,
        reserve_current=7,
    )
    store.create_character(CharacterState("helper", "game", "bob", "Bio", helper_sheet))
    store.place_player(game_id="game", player_id="bob", scene_id="scene")


def test_confirmed_roll_is_immutable_and_idempotent(tmp_path) -> None:
    store = setup_store(tmp_path)
    service = ActionService(store)
    pending = service.propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="scene",
        proposal=PoolProposal(
            trait_names=("Trait 0",),
            aspect_names=("Aspect 0.0",),
            reserve_spent=0,
            difficulty=2,
        ),
        prompt="Confirm?",
    )
    dice = iter((4, 4, 1, 1))
    first = service.confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        reserve_spent=2,
        die=lambda: next(dice),
    )
    second = service.confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        reserve_spent=2,
        die=lambda: next(dice),
    )
    assert second == first
    assert first.hits == 2
    assert first.reserve_spent == 2
    character = store.character_for_player(game_id="game", player_id="alice")
    assert character is not None
    assert character.sheet.reserve_current == 3


def test_roll_narration_keeps_the_origin_channel(tmp_path) -> None:
    store = setup_store(tmp_path)
    service = ActionService(store)
    pending = service.propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="scene",
        proposal=PoolProposal(
            trait_names=("Trait 0",),
            aspect_names=("Aspect 0.0", "Aspect 0.1"),
            difficulty=2,
        ),
        prompt="Confirm?",
        origin_channel_id="game-channel",
    )

    service.confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        confirmation_event_id="origin-channel-roll",
        die=lambda: 4,
    )

    narration = store.open_pending(
        game_id="game",
        player_id="alice",
        channel_id="game-channel",
    )
    assert narration is not None
    assert narration.kind is PendingKind.PLAYER_NARRATION
    assert narration.origin_channel_id == "game-channel"
    assert (
        store.open_pending(
            game_id="game",
            player_id="alice",
            channel_id="sibling-channel",
        )
        is None
    )


def test_disabled_narrator_rights_keep_outcome_with_pipeline_gm(tmp_path) -> None:
    store = setup_store(tmp_path)
    game = store.game_state("game")
    store.set_narrator_rights_level(
        game_id="game",
        level=NarratorRightsLevel.DISABLED,
        expected_revision=game.revision,
    )
    service = ActionService(store)
    pending = service.propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="scene",
        proposal=PoolProposal(trait_names=("Trait 0",), aspect_names=("Aspect 0.0",), difficulty=2),
        prompt="Confirm",
    )
    result = service.confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        confirmation_event_id="disabled",
        die=lambda: 4,
    )
    assert result.narrator_rights is NarratorRights.GM_SUCCESS
    assert store.open_pending(game_id="game", player_id="alice") is None


def test_help_die_is_spent_on_success_and_added_to_pool(tmp_path) -> None:
    store = setup_store(tmp_path)
    add_helper(store)
    service = ActionService(store)
    pending = service.propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="scene",
        proposal=PoolProposal(trait_names=("Trait 0",), difficulty=2),
        prompt="Confirm",
    )
    store.offer_help(game_id="game", helper_player_id="bob", target_player_id="alice")
    result = service.confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        confirmation_event_id="help-success",
        die=lambda: 4,
    )
    assert result.help_dice == 1
    assert result.pool_size == 2
    helper = store.character_for_player(game_id="game", player_id="bob")
    assert helper.sheet.reserve_current == 6


def test_replayed_help_is_idempotent_and_does_not_spend_twice(tmp_path) -> None:
    store = setup_store(tmp_path)
    add_helper(store)
    pending = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="scene",
        proposal=PoolProposal(trait_names=("Trait 0",), difficulty=2),
        prompt="Confirm",
    )

    first = store.offer_help(game_id="game", helper_player_id="bob", target_player_id="alice")
    replay = store.offer_help(game_id="game", helper_player_id="bob", target_player_id="alice")

    assert first.interaction_id == pending.interaction_id
    assert replay.interaction_id == pending.interaction_id
    assert store.help_count(pending.interaction_id) == 1
    assert store.character_for_player(game_id="game", player_id="bob").sheet.reserve_current == 6


def test_event_scoped_help_replays_original_pool_after_a_new_pool_opens(tmp_path) -> None:
    import pytest

    store = setup_store(tmp_path)
    add_helper(store)
    store.bind_channel(channel_id="channel", game_id="game")
    service = ActionService(store)
    original_pool = service.propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="scene",
        proposal=PoolProposal(trait_names=("Trait 0",), difficulty=2),
        prompt="Confirm the original pool.",
        origin_channel_id="channel",
    )
    event = IncomingMessage.now(
        event_id="help-original-pool",
        channel_id="channel",
        author_id="bob",
        content="/assist alice",
    )
    assert store.enqueue(event)
    assert store.claim_pending(channel_id="channel", limit=1)[0].event_id == event.event_id

    first = store.offer_help(
        game_id="game",
        helper_player_id="bob",
        target_player_id="alice",
        event_id=event.event_id,
        channel_id=event.channel_id,
    )
    assert first.interaction_id == original_pool.interaction_id
    assert store.help_count(original_pool.interaction_id) == 1
    assert store.character_for_player(game_id="game", player_id="bob").sheet.reserve_current == 6

    store.cancel_pending(
        interaction_id=original_pool.interaction_id,
        player_id="alice",
        expected_revision=original_pool.revision,
    )
    new_pool = service.propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="scene",
        proposal=PoolProposal(trait_names=("Trait 1",), difficulty=3),
        prompt="Confirm a newer pool.",
        origin_channel_id="channel",
    )

    replay = store.offer_help(
        game_id="game",
        helper_player_id="bob",
        target_player_id="alice",
        event_id=event.event_id,
        channel_id=event.channel_id,
    )

    assert replay == first
    assert replay.interaction_id == original_pool.interaction_id
    assert store.help_count(original_pool.interaction_id) == 1
    assert store.help_count(new_pool.interaction_id) == 0
    assert store.character_for_player(game_id="game", player_id="bob").sheet.reserve_current == 7
    operation = store.event_operation(event.event_id)
    assert operation is not None
    assert operation["operation_type"] == "offer_help"
    assert operation["result"]["interaction_id"] == original_pool.interaction_id
    assert operation["result"]["target_player_id"] == "alice"
    with pytest.raises(RuntimeError, match="event operation identity mismatch"):
        store.offer_help(
            game_id="game",
            helper_player_id="alice",
            target_player_id="bob",
            event_id=event.event_id,
            channel_id=event.channel_id,
        )


def test_distinct_help_event_cannot_claim_an_existing_helper_attachment(tmp_path) -> None:
    import pytest

    store = setup_store(tmp_path)
    add_helper(store)
    store.bind_channel(channel_id="channel", game_id="game")
    pool = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="scene",
        proposal=PoolProposal(trait_names=("Trait 0",), difficulty=2),
        prompt="Confirm.",
        origin_channel_id="channel",
    )
    for event_id in ("first-help", "second-help"):
        assert store.enqueue(
            IncomingMessage.now(
                event_id=event_id,
                channel_id="channel",
                author_id="bob",
                content="/assist alice",
            )
        )
    assert store.claim_pending(channel_id="channel", limit=1)[0].event_id == "first-help"
    store.offer_help(
        game_id="game",
        helper_player_id="bob",
        target_player_id="alice",
        event_id="first-help",
        channel_id="channel",
    )
    store.complete_batch(
        event_ids=["first-help"],
        channel_id="channel",
        contents=[],
        idempotency_key="first-help",
    )
    assert store.claim_pending(channel_id="channel", limit=1)[0].event_id == "second-help"

    with pytest.raises(ValueError, match="already helps"):
        store.offer_help(
            game_id="game",
            helper_player_id="bob",
            target_player_id="alice",
            event_id="second-help",
            channel_id="channel",
        )

    assert store.event_operation("second-help") is None
    assert store.help_count(pool.interaction_id) == 1
    assert store.character_for_player(game_id="game", player_id="bob").sheet.reserve_current == 6


def test_help_die_is_returned_on_failure_without_penalty(tmp_path) -> None:
    store = setup_store(tmp_path)
    add_helper(store)
    service = ActionService(store)
    pending = service.propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="scene",
        proposal=PoolProposal(trait_names=("Trait 0",), difficulty=3),
        prompt="Confirm",
    )
    store.offer_help(game_id="game", helper_player_id="bob", target_player_id="alice")
    service.confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        confirmation_event_id="help-failure",
        die=lambda: 1,
    )
    helper = store.character_for_player(game_id="game", player_id="bob")
    assert helper.sheet.reserve_current == 7


def test_help_die_is_returned_when_pool_is_cancelled(tmp_path) -> None:
    store = setup_store(tmp_path)
    add_helper(store)
    pending = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="scene",
        proposal=PoolProposal(trait_names=("Trait 0",), difficulty=2),
        prompt="Confirm",
    )
    store.offer_help(game_id="game", helper_player_id="bob", target_player_id="alice")
    assert store.character_for_player(game_id="game", player_id="bob").sheet.reserve_current == 6

    store.cancel_pending(
        interaction_id=pending.interaction_id,
        player_id="alice",
        expected_revision=pending.revision,
    )

    helper = store.character_for_player(game_id="game", player_id="bob")
    assert helper.sheet.reserve_current == 7
    assert store.pending_by_id(pending.interaction_id).status is PendingStatus.CANCELLED


def test_help_die_is_returned_when_pool_expires(tmp_path) -> None:
    store = setup_store(tmp_path)
    add_helper(store)
    pending = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="scene",
        proposal=PoolProposal(trait_names=("Trait 0",), difficulty=2),
        prompt="Confirm",
    )
    store.offer_help(game_id="game", helper_player_id="bob", target_player_id="alice")
    assert store.character_for_player(game_id="game", player_id="bob").sheet.reserve_current == 6

    store.expire_pending(
        interaction_id=pending.interaction_id,
        player_id="alice",
        expected_revision=pending.revision,
    )

    helper = store.character_for_player(game_id="game", player_id="bob")
    assert helper.sheet.reserve_current == 7
    assert store.pending_by_id(pending.interaction_id).status is PendingStatus.EXPIRED


def test_last_channel_unbind_cancels_pending_and_refunds_helpers(tmp_path) -> None:
    store = setup_store(tmp_path)
    add_helper(store)
    store.bind_channel(channel_id="game-channel", game_id="game")
    store.start_activity_clock(
        game_id="game",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    pending = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="scene",
        proposal=PoolProposal(trait_names=("Trait 0",), difficulty=2),
        prompt="Confirm",
        origin_channel_id="game-channel",
    )
    store.offer_help(game_id="game", helper_player_id="bob", target_player_id="alice")
    assert (
        store.character_for_player(
            game_id="game",
            player_id="bob",
        ).sheet.reserve_current
        == 6
    )

    store.unbind_channel(channel_id="game-channel", expected_game_id="game")

    closed = store.pending_by_id(pending.interaction_id)
    assert closed.status is PendingStatus.CANCELLED
    assert closed.payload["closed_reason"] == "origin_channel_unbound"
    assert (
        store.character_for_player(
            game_id="game",
            player_id="bob",
        ).sheet.reserve_current
        == 7
    )
    assert store.activity_state("game")["last_event_at"] is None


def test_origin_channel_unbind_refunds_once_while_sibling_session_stays_active(
    tmp_path,
) -> None:
    store = setup_store(tmp_path)
    add_helper(store)
    store.bind_channel(channel_id="primary", game_id="game")
    store.bind_channel(channel_id="secondary", game_id="game")
    store.start_activity_clock(
        game_id="game",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    pending = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="scene",
        proposal=PoolProposal(trait_names=("Trait 0",), difficulty=2),
        prompt="Confirm",
        origin_channel_id="primary",
    )
    store.offer_help(game_id="game", helper_player_id="bob", target_player_id="alice")

    store.unbind_channel(channel_id="primary", expected_game_id="game")
    store.unbind_channel(channel_id="primary", expected_game_id="game")

    closed = store.pending_by_id(pending.interaction_id)
    assert closed.status is PendingStatus.CANCELLED
    assert closed.payload["closed_reason"] == "origin_channel_unbound"
    assert (
        store.character_for_player(
            game_id="game",
            player_id="bob",
        ).sheet.reserve_current
        == 7
    )
    assert store.channel_state("secondary").game_id == "game"
    assert store.activity_state("game")["last_event_at"] is not None


def test_roll_confirmation_rejects_a_scene_changed_after_proposal(tmp_path) -> None:
    import pytest

    store = setup_store(tmp_path)
    service = ActionService(store)
    pending = service.propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="scene",
        proposal=PoolProposal(trait_names=("Trait 0",), difficulty=2),
        prompt="Confirm",
    )
    scene = store.scene_projection(game_id="game", player_id="alice")
    assert scene is not None
    store.apply_scene_patch(
        game_id="game",
        scene_id="scene",
        expected_revision=int(scene["scene_revision"]),
        add_facts=["The bridge has collapsed"],
        remove_facts=[],
        causation_id="other-player-turn",
        summary="Another player changed the scene",
    )

    with pytest.raises(ValueError, match="scene changed"):
        service.confirm_roll(
            interaction_id=pending.interaction_id,
            player_id="alice",
            confirmation_event_id="stale-scene",
            die=lambda: 4,
        )


def test_temporary_bonus_is_consumed_atomically_and_retry_is_idempotent(tmp_path) -> None:
    bonus = TemporaryBonus(
        "archive-route",
        TemporaryBonusType.EXTRA_DIE,
        "Following the courier through the archive",
    )
    store = setup_store(tmp_path, temporary_bonuses=(bonus,))
    service = ActionService(store)
    pending = service.propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="scene",
        proposal=PoolProposal(
            trait_names=("Trait 0",),
            difficulty=2,
            bonus_ids=("archive-route",),
        ),
        prompt="Confirm",
    )

    first = service.confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        confirmation_event_id="bonus-roll",
        die=lambda: 4,
    )
    second = service.confirm_roll(
        interaction_id=pending.interaction_id,
        player_id="alice",
        confirmation_event_id="bonus-roll",
        die=lambda: 1,
    )

    assert first == second
    assert first.pool_size == 2
    character = store.character_for_player(game_id="game", player_id="alice")
    assert character.sheet.temporary_bonuses == ()
    event = next(
        item
        for item in store.recent_domain_events(game_id="game", limit=10)
        if item["event_type"] == "roll_committed"
    )
    assert event["payload"]["consumed_bonus_ids"] == ["archive-route"]
