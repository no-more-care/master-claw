import asyncio
import json
from dataclasses import replace

import pytest
from test_advancement_safety_classifier import Classifier
from test_reserve_recovery_seam import (
    AWARD_REASON,
    REST_REASON,
    Pipeline,
    app_for,
    arguments,
    setup,
)

from masterclaw.app.context_inputs import ContextInputSnapshot
from masterclaw.app.reserve_recovery import (
    ReserveRecoveryAssessment,
    ReserveRecoveryReason,
    ReserveRecoveryVerdict,
    RoleplayEligibility,
    SafeRestEligibility,
)
from masterclaw.app.reserve_recovery_classifier import (
    ReserveRecoveryClassifier,
    recovery_eligibility,
    reserve_recovery_projection,
)
from masterclaw.classifier_calibration import ClassifierSpan, classifier_calibration_report
from masterclaw.classifiers.base import ClassifierRateLimitError
from masterclaw.classifiers.executor import SemanticClassifierExecutor
from masterclaw.classifiers.observations import SkippedClassifierObservation
from masterclaw.classifiers.policy import ReserveRecoveryClassifierConfig
from masterclaw.domain.state import ReserveRecoveryMode
from masterclaw.telemetry import bind_trace, reset_trace


def snapshot_for(store):
    args = arguments(store)
    return app_for(store)._reserve_recovery._capture_snapshot(
        game=store.game_state("game"),
        scene=args["scene"],
        outcome_source=args["outcome_source"],
        player_id="alice",
    )


def assessment(rest=False, players=("alice",)):
    return ReserveRecoveryAssessment(
        safe_rest=SafeRestEligibility(
            ReserveRecoveryVerdict.ALLOW if rest else ReserveRecoveryVerdict.DENY,
            ReserveRecoveryReason.SAFE_REST_COMPLETED
            if rest
            else ReserveRecoveryReason.NO_COMPLETED_SAFE_REST,
            REST_REASON,
        ),
        roleplay=tuple(
            RoleplayEligibility(
                player,
                ReserveRecoveryVerdict.ALLOW,
                ReserveRecoveryReason.OBSERVABLE_ROLEPLAY,
                AWARD_REASON,
            )
            for player in players
        ),
    )


def adapter(port, **config):
    return ReserveRecoveryClassifier(
        SemanticClassifierExecutor(port, requested_model="alias-latest"),
        ReserveRecoveryClassifierConfig(**{"mode": "shadow", **config}),
    )


@pytest.mark.parametrize("mode", list(ReserveRecoveryMode))
def test_dynamic_questions_and_private_candidate_roundtrip(tmp_path, mode):
    snapshot = snapshot_for(setup(tmp_path, mode))
    projection = reserve_recovery_projection(snapshot, ReserveRecoveryClassifierConfig())
    request = projection.request
    assert request.taxonomy_version == "reserve_recovery.v1"
    assert list(request.questions) == (["safe_rest_completed"] if mode.allows_safe_rest else []) + (
        ["strong_roleplay.candidate_0", "strong_roleplay.candidate_1"]
        if mode.allows_roleplay_award
        else []
    )
    assert all(
        q.type == "noul" and set(q.criteria) == {"true", "false"}
        for q in request.questions.values()
    )
    if mode.allows_roleplay_award:
        mapping = dict(projection.candidate_players)
        assert set(mapping.values()) == {"alice", "bob"}
        for row in request.state["candidates"]:
            assert row["actor"] is (mapping[row["candidate"]] == "alice")
            assert row["participant"] is True
    assert request.state["outcome"]["text"] == "Мы отдохнули. We rested."
    assert "alice" not in request.model_dump_json()
    assert "bob-hero" not in request.model_dump_json()


@pytest.mark.parametrize(
    ("probability", "verdict"),
    [
        (0, "deny"),
        (0.2, "deny"),
        (0.201, "uncertain"),
        (0.899, "uncertain"),
        (0.9, "allow"),
        (1, "allow"),
    ],
)
def test_per_question_asymmetric_threshold_boundaries(probability, verdict):
    assert (
        recovery_eligibility(
            probability, ReserveRecoveryClassifierConfig(allow_threshold=0.9, deny_threshold=0.2)
        )
        == verdict
    )


@pytest.mark.parametrize(
    ("probabilities", "decision"),
    [((0.9, 0.2, 0.2), "recovery"), ((0.2, 0.2, 0.2), "none"), ((0.9, 0.201, 0.2), "uncertain")],
)
def test_reducer_references_and_full_distributions(tmp_path, probabilities, decision):
    snapshot = snapshot_for(setup(tmp_path))
    projection = reserve_recovery_projection(snapshot, ReserveRecoveryClassifierConfig())
    # Explicit gold labels refer to the canonical checkpoint, not the unfiltered legacy result.
    awarded = tuple(
        player
        for index, (_, player) in enumerate(projection.candidate_players)
        if probabilities[index + 1] >= 0.5
    )
    result = asyncio.run(
        adapter(
            Classifier(probabilities),
            allow_threshold=0.9,
            deny_threshold=0.2,
        ).observe(snapshot, assessment(rest=probabilities[0] >= 0.5, players=awarded))
    )
    observation = result.observation
    assert observation.decision == decision
    assert observation.agreement is True
    assert observation.decision_reference is None
    assert observation.reference["safe_rest_completed"] is (probabilities[0] >= 0.5)
    for index, key in enumerate(projection.request.questions):
        answer = observation.answers[key]
        assert answer.noul == probabilities[index]
        assert answer.probabilities == {
            "true": probabilities[index],
            "false": 1 - probabilities[index],
        }
        assert answer.outcome == (
            "uncertain"
            if recovery_eligibility(
                probabilities[index],
                ReserveRecoveryClassifierConfig(allow_threshold=0.9, deny_threshold=0.2),
            )
            == "uncertain"
            else "eligible"
        )


def test_privacy_nested_echoed_ids_and_provider_prose_never_enter_payload_or_span(tmp_path):
    snapshot = snapshot_for(setup(tmp_path))
    projections = snapshot.inputs.projections
    scene = projections["current_scene"]["state"]
    for index, row in enumerate(projections["characters"]):
        row["name"] = ("Rurik", "Boris")[index]
    scene.update(
        description="Трактир npc-canonical-id <@123456789012345678> открыт.",
        facts=["npc-canonical-id keeps the public door open"],
        npcs=[{"id": "npc-canonical-id", "name": "Innkeeper", "secret": "GM secret"}],
        hidden={"fact": "hidden fact"},
    )
    projections["outcome_source"].update(
        evidence=["npc-canonical-id saw the choice"],
        reason="provider reason",
        approved_narration="provider prose",
    )
    snapshot = replace(
        snapshot,
        inputs=ContextInputSnapshot.capture(
            projections=projections,
            domain_events=[{"event_type": "private", "text": "raw domain history"}],
            chat_messages=[{"content": "raw chat history"}],
        ),
    )
    port = Classifier()
    records = []

    class Sink:
        def record_stage_spans(self, values):
            records.extend(values)

    binding = bind_trace(Sink(), trace_id="private-trace")
    try:
        result = asyncio.run(adapter(port).observe(snapshot, assessment()))
    finally:
        reset_trace(binding)
    payload = port.requests[0].model_dump_json()
    attrs = json.dumps(records[0]["attributes"])
    for forbidden in (
        "npc-canonical-id",
        "123456789012345678",
        "bob-hero",
        "alice",
        "reserve_current",
        "reserve_maximum",
        "player_id",
        "character_id",
        "GM secret",
        "hidden fact",
        "raw domain history",
        "raw chat history",
        "provider reason",
        "provider prose",
        REST_REASON,
        AWARD_REASON,
    ):
        assert forbidden not in payload
        assert forbidden not in attrs
    assert "Rurik" in payload and "Boris" in payload
    assert "Трактир" in payload and "public door" in payload
    assert "Трактир" not in attrs and "public door" not in attrs
    assert result.observation.usage == {"input_tokens": 20}
    assert result.observation.cost == 0.001
    assert records[0]["attributes"]["resolved_model"] == "resolved-1"


def test_aggregate_recovery_does_not_hide_question_reference_disagreement(tmp_path):
    result = asyncio.run(
        adapter(Classifier((0.99, 0.99, 0.99))).observe(
            snapshot_for(setup(tmp_path)),
            assessment(rest=True, players=()),
        )
    )
    assert result.observation.decision == "recovery"
    assert result.observation.agreement is False
    assert result.observation.reference["safe_rest_completed"] is True
    assert result.observation.reference["strong_roleplay.candidate_0"] is False


@pytest.mark.parametrize("failure", [None, "uncertain", "provider", "observer"])
def test_fresh_observation_is_after_checkpoint_errors_do_not_change_effects_and_replay_skips(
    tmp_path,
    monkeypatch,
    failure,
):
    store = setup(tmp_path, ReserveRecoveryMode.ROLEPLAY_AWARD)
    port = Classifier(
        (0.5, 0.5) if failure == "uncertain" else (0.99, 0.01),
        error=ClassifierRateLimitError("private secret") if failure == "provider" else None,
    )
    observer = adapter(port)
    observed = []

    class RecordingObserver:
        async def observe(self, snapshot, canonical):
            assert store.reserve_recovery_decision("outcome:one") is not None
            assert (
                store.character_for_player(game_id="game", player_id="alice").sheet.reserve_current
                == 2
            )
            observed.append(canonical)
            assert canonical.safe_rest.verdict is ReserveRecoveryVerdict.DENY
            assert canonical.safe_rest.display_detail == REST_REASON
            if failure == "observer":
                raise RuntimeError("untrusted secret")
            return await observer.observe(snapshot, canonical)

    pipeline = Pipeline(rest=True, awards=("alice", "bob"))
    application = app_for(store, pipeline, reserve_recovery_observer=RecordingObserver())
    args = arguments(store)
    asyncio.run(application._consider_reserve_recovery(**args))
    assert store.character_for_player(game_id="game", player_id="alice").sheet.reserve_current == 3
    assert store.character_for_player(game_id="game", player_id="bob").sheet.reserve_current == 7
    assert len(observed) == 1

    def forbidden(**kwargs):
        raise AssertionError("replay must not read context")

    monkeypatch.setattr(store, "recent_domain_events", forbidden)
    asyncio.run(application._consider_reserve_recovery(**args))
    assert len(observed) == 1 and len(pipeline.calls) == 1
    assert store.character_for_player(game_id="game", player_id="alice").sheet.reserve_current == 3


def test_first_writer_race_loser_never_observes(tmp_path):
    store = setup(tmp_path)
    port = Classifier()

    class RacingPipeline(Pipeline):
        async def run(self, **kwargs):
            result = await super().run(**kwargs)
            store.checkpoint_reserve_recovery_decision(
                game_id="game",
                causation_id="outcome:one",
                safe_rest_completed=False,
                safe_rest_reason=None,
                awards=(("bob", "canonical winner"),),
            )
            return result

    application = app_for(store, RacingPipeline(rest=True), reserve_recovery_observer=adapter(port))
    asyncio.run(application._consider_reserve_recovery(**arguments(store)))
    assert port.requests == []
    assert store.character_for_player(game_id="game", player_id="alice").sheet.reserve_current == 2
    assert store.character_for_player(game_id="game", player_id="bob").sheet.reserve_current == 7


def test_truncated_projection_has_typed_skip_no_provider_no_calibrated_denominator(tmp_path):
    store = setup(tmp_path)
    port = Classifier()
    records = []

    class Sink:
        def record_stage_spans(self, values):
            records.extend(values)

        def classifier_spans(self, *, since_hours):
            for row in records:
                yield ClassifierSpan(row["stage"], json.dumps(row["attributes"]))

    binding = bind_trace(Sink(), trace_id="private-trace")
    try:
        result = asyncio.run(
            adapter(port, max_candidates=1).observe(snapshot_for(store), assessment())
        )
    finally:
        reset_trace(binding)
    assert isinstance(result, SkippedClassifierObservation)
    assert result.outcome == "skipped" and result.reason == "projection_truncated"
    assert port.requests == []
    report = classifier_calibration_report(Sink())
    assert report["rows"]["skipped"] == 1
    assert report["rows"]["valid"] == report["rows"]["malformed"] == 0
    assert report["summary"]["total"] == report["summary"]["comparable_rows"] == 0
    assert report["skipped"][0]["reason"] == "projection_truncated"
    assert "private-trace" not in json.dumps(report)
    application = app_for(
        store, Pipeline(rest=True), reserve_recovery_observer=adapter(port, max_candidates=1)
    )
    asyncio.run(application._consider_reserve_recovery(**arguments(store)))
    assert port.requests == []
    assert store.character_for_player(game_id="game", player_id="alice").sheet.reserve_current == 7


def test_off_observer_and_empty_candidate_projection_make_no_provider_calls(tmp_path):
    snapshot = snapshot_for(setup(tmp_path, ReserveRecoveryMode.ROLEPLAY_AWARD))
    port = Classifier()
    assert asyncio.run(adapter(port, mode="off").observe(snapshot, assessment())) is None
    projections = snapshot.inputs.projections
    projections["characters"] = []
    snapshot = replace(
        snapshot,
        inputs=ContextInputSnapshot.capture(
            projections=projections,
            domain_events=[],
            chat_messages=[],
        ),
    )
    result = asyncio.run(adapter(port).observe(snapshot, assessment()))
    assert result.reason == "no_candidates"
    assert port.requests == []


def test_cancelled_observer_propagates_checkpoint_survives_for_effect_replay(tmp_path):
    store = setup(tmp_path)

    class Cancelled:
        async def observe(self, *args):
            raise asyncio.CancelledError()

    application = app_for(store, Pipeline(), reserve_recovery_observer=Cancelled())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(application._consider_reserve_recovery(**arguments(store)))
    assert store.reserve_recovery_decision("outcome:one") is not None
    asyncio.run(application._consider_reserve_recovery(**arguments(store)))
    assert store.character_for_player(game_id="game", player_id="alice").sheet.reserve_current == 3


def test_partial_award_retry_does_not_repeat_shadow_or_first_award(tmp_path, monkeypatch):
    store = setup(tmp_path)
    port = Classifier()
    application = app_for(
        store, Pipeline(awards=("alice", "bob")), reserve_recovery_observer=adapter(port)
    )
    original = application._games.award_reserve_die
    calls = []

    def fail_second(**kwargs):
        calls.append(kwargs["player_id"])
        if calls == ["alice", "bob"]:
            raise RuntimeError("partial application")
        return original(**kwargs)

    monkeypatch.setattr(application._games, "award_reserve_die", fail_second)
    asyncio.run(application._consider_reserve_recovery(**arguments(store)))
    asyncio.run(application._consider_reserve_recovery(**arguments(store)))
    assert len(port.requests) == 1
    assert store.character_for_player(game_id="game", player_id="alice").sheet.reserve_current == 3
    assert store.character_for_player(game_id="game", player_id="bob").sheet.reserve_current == 7
