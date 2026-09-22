import asyncio
import json
from dataclasses import replace

import pytest
from test_advancement_safety_classifier import Classifier
from test_outcome_narrative_review import facade

from masterclaw.app.decision_checkpoints import run_checkpointed_decision
from masterclaw.app.outcome_narrative_classifier import (
    OutcomeNarrativeClassifier,
    outcome_narrative_request,
)
from masterclaw.app.outcome_narrative_review import OutcomeNarrativeSnapshot
from masterclaw.classifier_calibration import ClassifierSpan, classifier_calibration_report
from masterclaw.classifiers.base import ClassifierRateLimitError
from masterclaw.classifiers.executor import SemanticClassifierExecutor
from masterclaw.classifiers.policy import OutcomeNarrativeClassifierConfig
from masterclaw.context.assembler import AssembledContext
from masterclaw.pipelines.base import PipelineValidationError
from masterclaw.storage.sqlite import SQLiteStore
from masterclaw.telemetry import bind_trace, reset_trace


def snapshot(*, automatic=False, raw="Черновик. The door remains shut.", updates=None):
    outcome = (
        {"resolution": "automatic", "declaration": "I open the door."}
        if automatic
        else {
            "hits": 0,
            "difficulty": 2,
            "dice": [1, 1],
            "roll_id": "internal-roll",
            "narrator_rights": "gm_failure",
            "declaration": "Я открываю дверь. I open it.",
        }
    )
    projections = {
        "roll_result": outcome,
        "session_brief": {"game_id": "internal-game", "locale": "ru"},
        "actor_character": {
            "name": "Mara",
            "player_id": "internal-player",
            "character_id": "internal-actor",
            "biography": "Private full biography",
            "xp": 321,
        },
        "current_scene": {
            "scene_id": "internal-scene",
            "participants": ["internal-player", "other-player"],
            "participant_characters": [
                {"name": "Mara", "player_id": "internal-player"},
                {"name": "Dorn", "player_id": "other-player"},
            ],
            "state": {
                "description": "A public room",
                "facts": ["The door is shut"],
                "npcs": [{"id": "nested-npc", "name": "Innkeeper"}],
                "hidden": {"fact": "A hidden plan"},
            },
        },
        "gm_world_context": {"facts": ["A GM-only fact"]},
        "public_world_context": {"details": "Unrelated world setting"},
    }
    if updates:
        updates(projections)
    source = "\n\n".join(
        f"## STATE {key}\n{json.dumps(value, ensure_ascii=False)}"
        for key, value in projections.items()
    )
    source += (
        "\n\n## HISTORY chat_messages\n"
        '[{"content":"Private raw chat", "channel_id":"internal-channel"}]'
    )
    return OutcomeNarrativeSnapshot(
        task="Private generation task",
        context=AssembledContext("Private system prompt", source, (), 20),
        raw_narrative=raw,
        immutable_outcome_json=json.dumps(outcome, ensure_ascii=False),
    )


def adapter(port=None, **config):
    return OutcomeNarrativeClassifier(
        SemanticClassifierExecutor(port or Classifier((0.99,) * 4), requested_model="alias"),
        OutcomeNarrativeClassifierConfig(**{"mode": "shadow", **config}),
    )


@pytest.mark.parametrize("automatic", [False, True])
def test_exact_questions_true_polarity_and_normalized_outcome(automatic):
    request = outcome_narrative_request(snapshot(automatic=automatic))
    assert request.taxonomy_version == "outcome_narrative_review.v1"
    assert list(request.questions) == [
        "preserves_resolved_outcome",
        "uses_only_established_facts",
        "respects_actor_and_viewpoint",
        "uses_only_public_knowledge",
    ]
    assert all(
        q.type == "noul" and set(q.criteria) == {"true", "false"}
        for q in request.questions.values()
    )
    assert all("True means compliant" in q.instructions for q in request.questions.values())
    assert request.state["resolved_outcome"] == (
        {"kind": "automatic", "authority": "gm_automatic"}
        if automatic
        else {"kind": "failure", "authority": "gm_failure", "hits": 0, "difficulty": 2}
    )
    assert request.state["actor"] == {"name": "Mara", "role": "actor"}
    assert request.state["other_player_characters"] == [
        {"name": "Dorn", "role": "other_player_character"}
    ]
    assert "Черновик" in request.state["raw_narrative"]


@pytest.mark.parametrize(
    ("probabilities", "decision", "reason"),
    [
        ((0.9,) * 4, "publish", "compliant"),
        ((0.899, 1, 1, 1), "uncertain", "insufficient_signal"),
        ((0.201, 1, 1, 1), "uncertain", "insufficient_signal"),
        ((0.2, 0, 0, 0), "repair", "mechanics_contradiction"),
        ((1, 0.2, 0, 0), "repair", "invented_or_contradicted_fact"),
        ((1, 1, 0.2, 0), "repair", "viewpoint_or_authority_violation"),
        ((1, 1, 1, 0.2), "repair", "hidden_knowledge"),
    ],
)
def test_reducer_boundaries_priority_and_no_fabricated_legacy_reference(
    probabilities, decision, reason
):
    result = asyncio.run(
        adapter(Classifier(probabilities), allow_threshold=0.9, deny_threshold=0.2).observe(
            snapshot()
        )
    )
    assert result.observation.decision == decision
    assert result.observation.decision_reason == reason
    assert result.observation.reference == {}
    assert result.observation.decision_reference is None and result.observation.agreement is None


def test_privacy_nested_echoed_ids_hidden_context_and_edited_prose_are_excluded():
    def update(projections):
        projections["current_scene"]["state"].update(
            description="Public nested-npc waits by the door.",
            facts=["nested-npc hears internal-game"],
        )

    source = snapshot(
        raw="Mara sees nested-npc beside internal-player <@123456789012345678>.", updates=update
    )
    port = Classifier((0.99,) * 4)
    result = asyncio.run(adapter(port).observe(source))
    encoded = port.requests[0].model_dump_json()
    observation = result.observation.model_dump_json()
    for value in (
        "nested-npc",
        "internal-player",
        "internal-game",
        "internal-roll",
        "internal-actor",
        "internal-scene",
        "other-player",
        "123456789012345678",
        "Private raw chat",
        "Private system prompt",
        "Private generation task",
        "Private full biography",
        "A hidden plan",
        "A GM-only fact",
        "Unrelated world setting",
        '"dice"',
        '"xp"',
        "edited prose",
    ):
        assert value not in encoded and value not in observation
    assert "Mara" in encoded and "Dorn" in encoded
    assert "Public" not in observation


@pytest.mark.parametrize(
    "change",
    [
        lambda s: replace(s, raw_narrative="x" * 4001),
        lambda s: replace(s, context=replace(s.context, degradations=("bounded source",))),
        lambda s: replace(s, context=replace(s.context, dynamic_context="malformed source")),
        lambda s: replace(s, raw_narrative="The A GM-only fact is revealed."),
        lambda s: replace(s, raw_narrative="A hidden plan is certain."),
        lambda s: replace(s, immutable_outcome_json="invalid"),
    ],
)
def test_unsafe_or_truncated_projection_is_typed_skipped_without_provider(change):
    port = Classifier((0.99,) * 4)
    result = asyncio.run(adapter(port).observe(change(snapshot())))
    assert result.outcome == "skipped"
    assert result.reason in {"projection_unavailable", "projection_truncated"}
    assert port.requests == []


def test_recorded_skips_and_success_have_zero_comparable_legacy_reference_denominator():
    records = []

    class Sink:
        def record_stage_spans(self, values):
            records.extend(values)

        def classifier_spans(self, *, since_hours):
            yield from (
                ClassifierSpan(row["stage"], json.dumps(row["attributes"])) for row in records
            )

    binding = bind_trace(Sink(), trace_id="private-trace")
    try:
        asyncio.run(adapter().observe(snapshot()))
        asyncio.run(adapter().observe(replace(snapshot(), raw_narrative="x" * 4001)))
    finally:
        reset_trace(binding)
    report = classifier_calibration_report(Sink())
    assert report["rows"]["valid"] == report["rows"]["skipped"] == 1
    assert report["rows"]["malformed"] == report["summary"]["comparable_rows"] == 0
    assert report["summary"]["agreement_rate"] is None
    assert "legacy_always_review" not in json.dumps(records)


@pytest.mark.parametrize("case", ["success", "uncertain", "error", "editor_error", "fallback"])
def test_editor_first_raw_not_edited_shadow_and_error_isolation(case):
    calls = []

    class Port(Classifier):
        async def classify(self, request):
            assert [label for label, _ in calls] == ["raw draft", "assess", "edited prose"] + (
                ["fallback prose"] if case == "fallback" else []
            )
            assert request.state["raw_narrative"] == "raw draft"
            assert "edited prose" not in request.model_dump_json()
            return await super().classify(request)

    port = Port(
        (0.5,) * 4 if case == "uncertain" else (0.99,) * 4,
        error=ClassifierRateLimitError("private provider error") if case == "error" else None,
    )
    pipeline = facade(
        calls,
        primary_error=RuntimeError("primary") if case in {"fallback", "editor_error"} else None,
        fallback_error=RuntimeError("fallback") if case == "editor_error" else None,
    )
    pipeline._observer = adapter(port)
    if case == "editor_error":
        with pytest.raises(PipelineValidationError):
            asyncio.run(pipeline.run(task="Narrate", context=snapshot().context))
        assert port.requests == []
    else:
        result = asyncio.run(pipeline.run(task="Narrate", context=snapshot().context))
        assert result.narrative == ("fallback prose" if case == "fallback" else "edited prose")
        assert len(port.requests) == 1


def test_outer_checkpoint_replay_skips_editor_decider_and_classifier(tmp_path):
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    calls, port = [], Classifier((0.99,) * 4)
    pipeline = facade(calls)
    pipeline._observer = adapter(port)
    args = dict(
        store=store,
        event_id="event",
        pipeline_key="outcome_narration:automatic",
        pipeline=pipeline,
        task="Narrate",
        context=snapshot(automatic=True).context,
        game_id=None,
    )
    first = asyncio.run(run_checkpointed_decision(**args))
    replay = asyncio.run(run_checkpointed_decision(**args))
    assert first == replay
    assert len(calls) == 3 and len(port.requests) == 1


@pytest.mark.parametrize("kind", ["automatic", "roll"])
def test_existing_stale_guard_covers_scene_change_during_awaited_shadow(
    tmp_path, monkeypatch, kind
):
    import test_outcome_narrative_review as review_tests

    original = review_tests.facade
    ports = []

    def with_observer(calls, **kwargs):
        hook = kwargs.pop("editor_hook")
        pipeline = original(calls, **kwargs)

        class Port(Classifier):
            async def classify(self, request):
                result = await super().classify(request)
                hook()
                return result

        port = Port((0.99,) * 4)
        ports.append(port)
        pipeline._observer = adapter(port)
        return pipeline

    monkeypatch.setattr(review_tests, "facade", with_observer)
    review_tests.test_handler_outer_checkpoint_replay_payload_fingerprint_and_publication_guards(
        tmp_path, kind, "stale"
    )
    assert len(ports[0].requests) == 1


def test_off_observer_never_projects_or_calls_and_cancellation_propagates():
    port = Classifier((0.99,) * 4)
    assert (
        asyncio.run(
            adapter(port, mode="off").observe(replace(snapshot(), immutable_outcome_json="invalid"))
        )
        is None
    )
    assert port.requests == []
    pipeline = facade([])
    pipeline._observer = adapter(Classifier((0.99,) * 4, error=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(pipeline.run(task="Narrate", context=snapshot().context))
