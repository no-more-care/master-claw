import ast
import asyncio
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

import pytest
from test_game_service import add_character
from test_player_narration import NeverIntent

from masterclaw.app.decision_checkpoints import decision_output_type_name
from masterclaw.app.game_service import GameService
from masterclaw.app.legacy_reserve_recovery import (
    LegacyReserveRecoveryDecider,
    LegacyReserveRecoverySnapshotCapture,
)
from masterclaw.app.message_handler import MessageApplication
from masterclaw.app.reserve_recovery import (
    ReserveRecoveryAssessment,
    ReserveRecoveryReason,
    ReserveRecoveryVerdict,
    RoleplayEligibility,
    SafeRestEligibility,
)
from masterclaw.app.reserve_recovery_coordinator import ReserveRecoveryCoordinator
from masterclaw.context.assembler import ContextAssembler, ContextHistory
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.state import ReserveRecoveryMode
from masterclaw.pipelines.reserve_recovery import ReserveRecoveryDecision
from masterclaw.pipelines.state_decision import StateDecisionRouter
from masterclaw.storage.sqlite import SQLiteStore

PROMPTS = Path(__file__).parents[1] / "prompts"
REST_REASON = "The party completed a safe overnight rest."
AWARD_REASON = "Alice made a specific observable costly choice."


def setup(tmp_path, mode=ReserveRecoveryMode.BOTH):
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    games = GameService(store)
    games.create_world(world_id="world", title="World")
    games.prepare_game(
        game_id="game", world_id="world", channel_id="game", reserve_recovery_mode=mode
    )
    store.create_scene(
        scene_id="room", game_id="game", title="Room", state={"facts": ["Дверь закрыта"]}
    )
    add_character(store, "game", "room", reserve=2)
    alice = store.character_for_player(game_id="game", player_id="alice")
    store.create_character(
        replace(
            alice,
            character_id="bob-hero",
            player_id="bob",
            sheet=replace(alice.sheet, name="Bob", reserve_current=6),
        )
    )
    store.place_player(game_id="game", player_id="bob", scene_id="room")
    return store


class Pipeline:
    output_type = ReserveRecoveryDecision

    def __init__(self, *, rest=False, awards=("alice",), error=None):
        self.calls = []
        self.error = error
        self.result = ReserveRecoveryDecision(
            safe_rest_completed=rest,
            safe_rest_reason=REST_REASON,
            awards=[{"player_id": player, "reason": AWARD_REASON} for player in awards],
        )

    async def run(self, *, task, context):
        self.calls.append((task, context))
        if self.error:
            raise self.error
        return self.result


def app_for(store, pipeline=None, **kwargs):
    return MessageApplication(
        store=store,
        context=ContextAssembler(PROMPTS),
        state_router=StateDecisionRouter(NeverIntent()),
        reserve_recovery_pipeline=pipeline,
        **kwargs,
    )


def arguments(store, **updates):
    return {
        "game_id": "game",
        "scene": store.scene_projection(game_id="game", player_id="alice"),
        "causation_id": "outcome:one",
        "outcome_source": {"kind": "automatic", "text": "Мы отдохнули. We rested."},
        "player_id": "alice",
        **updates,
    }


@pytest.mark.parametrize("player_id", ["alice", None])
def test_fresh_context_task_and_output_identity_match_legacy(tmp_path, monkeypatch, player_id):
    store = setup(tmp_path)
    pipeline = Pipeline()
    application = app_for(store, pipeline)
    events = [{"event_type": "scene_patched", "payload": {"facts": ["Свеча погасла"]}}]
    chats = [{"content": "Реплика игрока"}]
    history_calls = []

    def history(*, game_id, limit):
        assert (game_id, limit) == ("game", 4)
        history_calls.append("events")
        return events

    def chat(*, game_id, channel_id, player_id: str | None, limit):
        assert (game_id, channel_id, limit) == ("game", None, 2)
        history_calls.append(("chat", player_id))
        return chats

    monkeypatch.setattr(store, "recent_domain_events", history)
    monkeypatch.setattr(store, "recent_chat_messages", chat)
    args = arguments(store, player_id=player_id)
    game = store.game_state("game")
    # Literal old projection and enrichment, independent of snapshot factory/adapter.
    scene = args["scene"]
    expected = application._context.assemble(
        manifest_for(PipelineName.RESERVE_RECOVERY),
        {
            "current_scene": {
                **scene,
                "participant_characters": [
                    {"player_id": "alice", "name": "Hero"},
                    {"player_id": "bob", "name": "Bob"},
                ],
            },
            "outcome_source": args["outcome_source"],
            "reserve_policy": game.reserve_recovery_mode.value,
            "actor_character": None
            if player_id is None
            else application._actor_character_projection(game_id="game", player_id=player_id),
            "characters": store.reserve_projection("game"),
        },
        history=ContextHistory(events, chats),
    )
    asyncio.run(application._consider_reserve_recovery(**args))
    assert len(pipeline.calls) == 1
    task, context = pipeline.calls[0]
    assert task.encode() == b"Decide whether this resolved outcome earns reserve recovery."
    assert (
        context == expected
        and context.dynamic_context.encode() == expected.dynamic_context.encode()
    )
    assert history_calls == ["events", ("chat", player_id)]
    assert decision_output_type_name(ReserveRecoveryDecision) == (
        "masterclaw.pipelines.reserve_recovery.ReserveRecoveryDecision:v1:17dfdac2f5118909"
    )


def test_replay_checks_custom_checkpoint_before_context_even_without_pipeline(
    tmp_path, monkeypatch
):
    store = setup(tmp_path)
    args = arguments(store)
    store.checkpoint_reserve_recovery_decision(
        game_id="game",
        causation_id=args["causation_id"],
        safe_rest_completed=False,
        safe_rest_reason=None,
        awards=(("alice", AWARD_REASON),),
    )
    forbidden_reads = []

    def forbidden(*args, **kwargs):
        forbidden_reads.append("read")
        raise AssertionError("replay must not capture context or run the model")

    for method in (
        "scene_projection",
        "character_for_player",
        "reserve_projection",
        "recent_domain_events",
        "recent_chat_messages",
    ):
        monkeypatch.setattr(store, method, forbidden)
    coordinator = ReserveRecoveryCoordinator(store=store, capture_snapshot=forbidden)
    asyncio.run(coordinator.consider(**args))
    asyncio.run(coordinator.consider(**args))
    assert not forbidden_reads
    event = store.domain_event_for_causation(
        event_type="reserve_roleplay_award", causation_id="reserve-award:outcome:one:alice"
    )
    assert event["payload"]["before"] == 2 and event["payload"]["after"] == 3


@pytest.mark.parametrize("mode", list(ReserveRecoveryMode))
def test_exact_custom_payload_mode_filter_keeps_reason_and_mechanics(tmp_path, mode):
    store = setup(tmp_path, mode)
    pipeline = Pipeline(rest=True, awards=("alice", "bob"))
    application = app_for(store, pipeline)
    args = arguments(store)
    asyncio.run(application._consider_reserve_recovery(**args))
    payload = store.reserve_recovery_decision(args["causation_id"])
    assert payload == {
        "game_id": "game",
        "safe_rest_completed": mode.allows_safe_rest,
        "safe_rest_reason": REST_REASON,  # Retained even when safe_rest is filtered false.
        "awards": [{"player_id": player, "reason": AWARD_REASON} for player in ("alice", "bob")]
        if mode.allows_roleplay_award
        else [],
    }
    checkpoint = store.domain_event_for_causation(
        event_type="reserve_recovery_decision", causation_id="reserve-decision:outcome:one"
    )
    assert checkpoint["payload"] == payload
    for player, expected in (("alice", 7 if mode.allows_safe_rest else 3), ("bob", 7)):
        assert (
            store.character_for_player(game_id="game", player_id=player).sheet.reserve_current
            == expected
        )
    if mode.allows_safe_rest:
        event = store.domain_event_for_causation(
            event_type="reserve_safe_rest", causation_id="reserve-rest:outcome:one"
        )
        assert event["payload"]["reason"] == REST_REASON
    if mode.allows_roleplay_award:
        event = store.domain_event_for_causation(
            event_type="reserve_roleplay_award", causation_id="reserve-award:outcome:one:bob"
        )
        assert event["payload"]["reason"] == AWARD_REASON
        assert event["payload"]["after"] == 7


def test_first_writer_checkpoint_wins_over_new_model_result(tmp_path):
    store = setup(tmp_path)

    class RacingPipeline(Pipeline):
        async def run(self, **kwargs):
            result = await super().run(**kwargs)
            store.checkpoint_reserve_recovery_decision(
                game_id="game",
                causation_id="outcome:one",
                safe_rest_completed=False,
                safe_rest_reason=None,
                awards=(("bob", "The other worker chose Bob's roleplay."),),
            )
            return result

    pipeline = RacingPipeline(rest=True)
    asyncio.run(app_for(store, pipeline)._consider_reserve_recovery(**arguments(store)))
    assert store.character_for_player(game_id="game", player_id="alice").sheet.reserve_current == 2
    assert store.character_for_player(game_id="game", player_id="bob").sheet.reserve_current == 7
    assert store.reserve_recovery_decision("outcome:one")["awards"][0]["player_id"] == "bob"


def test_partial_application_retry_keeps_targets_and_does_not_double_award(tmp_path, monkeypatch):
    store = setup(tmp_path)
    pipeline = Pipeline(awards=("alice", "bob"))
    application = app_for(store, pipeline)
    args = arguments(store)
    original_award = application._games.award_reserve_die
    calls = []

    def interrupted(**kwargs):
        calls.append(kwargs["player_id"])
        if calls == ["alice", "bob"]:
            raise RuntimeError("crash after first award")
        return original_award(**kwargs)

    monkeypatch.setattr(application._games, "award_reserve_die", interrupted)
    asyncio.run(application._consider_reserve_recovery(**args))
    assert store.character_for_player(game_id="game", player_id="alice").sheet.reserve_current == 3
    assert store.character_for_player(game_id="game", player_id="bob").sheet.reserve_current == 6
    asyncio.run(application._consider_reserve_recovery(**args))
    assert store.character_for_player(game_id="game", player_id="alice").sheet.reserve_current == 3
    assert store.character_for_player(game_id="game", player_id="bob").sheet.reserve_current == 7
    assert len(pipeline.calls) == 1


def test_application_rechecks_mode_after_legacy_decision(tmp_path):
    store = setup(tmp_path)

    class ChangingMode(Pipeline):
        async def run(self, **kwargs):
            result = await super().run(**kwargs)
            store.set_reserve_recovery_mode(
                game_id="game",
                mode=ReserveRecoveryMode.ROLEPLAY_AWARD,
                expected_revision=store.game_state("game").revision,
            )
            return result

    asyncio.run(
        app_for(store, ChangingMode(rest=True))._consider_reserve_recovery(**arguments(store))
    )
    # Old mode filtered the checkpoint; current GameService mode blocks rest and ends best effort.
    assert store.reserve_recovery_decision("outcome:one")["safe_rest_completed"] is True
    assert store.character_for_player(game_id="game", player_id="alice").sheet.reserve_current == 2


@pytest.mark.parametrize(
    "case", ["missing_pipeline", "missing_game", "provider_failure", "unknown_target"]
)
def test_best_effort_missing_dependencies_and_invalid_targets_are_nonfatal(tmp_path, case):
    store = setup(tmp_path)
    pipeline = (
        None
        if case == "missing_pipeline"
        else Pipeline(
            error=RuntimeError("provider failed") if case == "provider_failure" else None,
            awards=("unknown",) if case == "unknown_target" else ("alice",),
        )
    )
    application = app_for(store, pipeline)
    args = arguments(store, game_id="missing" if case == "missing_game" else "game")
    assert asyncio.run(application._consider_reserve_recovery(**args)) is None
    assert store.reserve_recovery_decision("outcome:one") is None
    assert store.character_for_player(game_id="game", player_id="alice").sheet.reserve_current == 2


def test_snapshot_is_detached_assessment_separates_prose_and_injection_is_exclusive(tmp_path):
    store = setup(tmp_path)
    application = app_for(store)
    args = arguments(store)
    capture = LegacyReserveRecoverySnapshotCapture(store, application._capture_context_inputs)
    snapshot = capture(
        game=store.game_state("game"),
        scene=args["scene"],
        outcome_source=args["outcome_source"],
        player_id=None,
    )
    args["outcome_source"]["text"] = "mutated"
    assert snapshot.inputs.projections["outcome_source"]["text"] != "mutated"
    with pytest.raises(FrozenInstanceError):
        snapshot.player_id = "bob"
    decider = LegacyReserveRecoveryDecider(
        context=application._context, pipeline=Pipeline(rest=True)
    )
    assessment = asyncio.run(decider.assess(snapshot))
    assert assessment.safe_rest.reason is ReserveRecoveryReason.SAFE_REST_COMPLETED
    assert assessment.safe_rest.display_detail == REST_REASON
    assert assessment.roleplay[0].reason is ReserveRecoveryReason.OBSERVABLE_ROLEPLAY
    assert assessment.roleplay[0].display_detail == AWARD_REASON
    for contract in (SafeRestEligibility, RoleplayEligibility, ReserveRecoveryAssessment):
        assert not {"amount", "delta", "maximum"} & {field.name for field in fields(contract)}
    with pytest.raises(ValueError, match="not multiple"):
        app_for(store, Pipeline(), reserve_recovery_decider=decider)
    assert app_for(store, reserve_recovery_decider=decider)._reserve_recovery._decider is decider
    coordinator = application._reserve_recovery
    assert app_for(store, reserve_recovery=coordinator)._reserve_recovery is coordinator
    with pytest.raises(ValueError, match="not multiple"):
        app_for(store, Pipeline(), reserve_recovery=coordinator)


def test_duplicate_semantic_targets_are_rejected_by_unchanged_store(tmp_path, caplog):
    store = setup(tmp_path)

    class DuplicateDecider:
        async def assess(self, snapshot):
            award = RoleplayEligibility(
                "alice",
                ReserveRecoveryVerdict.ALLOW,
                ReserveRecoveryReason.OBSERVABLE_ROLEPLAY,
                AWARD_REASON,
            )
            return ReserveRecoveryAssessment(
                SafeRestEligibility(
                    ReserveRecoveryVerdict.DENY, ReserveRecoveryReason.NO_COMPLETED_SAFE_REST
                ),
                (award, award),
            )

    application = app_for(store, reserve_recovery_decider=DuplicateDecider())
    asyncio.run(application._consider_reserve_recovery(**arguments(store)))
    assert store.reserve_recovery_decision("outcome:one") is None
    assert store.character_for_player(game_id="game", player_id="alice").sheet.reserve_current == 2
    assert "cannot award one player twice" in caplog.text


def test_legacy_cancellation_still_propagates(tmp_path):
    store = setup(tmp_path)
    pipeline = Pipeline(error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(app_for(store, pipeline)._consider_reserve_recovery(**arguments(store)))
    assert store.reserve_recovery_decision("outcome:one") is None


def test_contract_and_coordinator_have_no_direct_context_pipeline_or_provider_imports():
    root = Path(__file__).parents[1] / "src/masterclaw/app"
    for filename in ("reserve_recovery.py", "reserve_recovery_coordinator.py"):
        tree = ast.parse((root / filename).read_text(encoding="utf-8"))
        imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any(
            module.startswith(("masterclaw.context", "masterclaw.pipelines", "masterclaw.adapters"))
            for module in imports
        )
