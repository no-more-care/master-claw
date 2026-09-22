import asyncio
import json
from dataclasses import replace

import pytest
from pydantic import ValidationError
from test_advancement_coordinator import setup
from test_advancement_safety import RecordingPipeline, decider_for, snapshot_for

from masterclaw.app.advancement_safety import SafetyReason, SafetyVerdict
from masterclaw.app.advancement_safety_classifier import (
    AdvancementSafetyClassifier,
    ShadowAdvancementSafetyDecider,
    advancement_classification_request,
)
from masterclaw.classifiers.base import ClassificationResponse, ClassifierRateLimitError
from masterclaw.classifiers.executor import SemanticClassifierExecutor
from masterclaw.classifiers.policy import AdvancementClassifierConfig
from masterclaw.telemetry import bind_trace, reset_trace


class Classifier:
    def __init__(self, probabilities=(0.99, 0.99, 0.99), *, error=None):
        self.probabilities = probabilities
        self.error = error
        self.requests = []

    async def classify(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return ClassificationResponse(
            request_key=request.request_key,
            taxonomy_version=request.taxonomy_version,
            answers={
                key: {
                    "type": "noul",
                    "noul": probability,
                    "probabilities": {"true": probability, "false": 1 - probability},
                }
                for key, probability in zip(request.questions, self.probabilities, strict=False)
            },
            provider="test",
            model="resolved-1",
            version="1.0",
            request_id="req-123",
            usage={"input_tokens": 20, "user_id": "private-metadata"},
            cost=0.001,
        )


def adapter(port=None, **config):
    return AdvancementSafetyClassifier(
        SemanticClassifierExecutor(port or Classifier(), requested_model="alias-latest"),
        AdvancementClassifierConfig(mode="shadow", **config),
    )


@pytest.mark.parametrize("kind", ["raise", "learn"])
def test_exact_raise_learn_taxonomies_and_noul_contract(tmp_path, kind):
    store = setup(tmp_path)
    snapshot = snapshot_for(store, request={"kind": kind, "trait": "Медицина"})
    request = advancement_classification_request(snapshot)
    assert request.taxonomy_version == f"advancement_safety.{kind}.v1"
    assert list(request.questions) == ["scene_safe_enough", "downtime_available"] + (
        ["learning_opportunity_supported"] if kind == "learn" else []
    )
    assert all(question.type == "noul" for question in request.questions.values())
    assert all(
        set(question.criteria) == {"true", "false"} for question in request.questions.values()
    )
    assert request.state["advancement_request"]["trait"] == "Медицина"


@pytest.mark.parametrize(
    ("probabilities", "verdict", "reason"),
    [
        ((0.8, 0.8, 0.8), "allow", "safe_with_downtime"),
        ((0.799, 0.9, 0.9), "uncertain", "insufficient_signal"),
        ((0.201, 0.9, 0.9), "uncertain", "insufficient_signal"),
        ((0.2, 0.0, 0.0), "deny", "unsafe"),
        ((0.9, 0.2, 0.0), "deny", "no_downtime"),
        ((0.9, 0.9, 0.2), "deny", "no_learning_opportunity"),
    ],
)
def test_advancement_specific_thresholds_boundaries_and_reason_priority(
    tmp_path, probabilities, verdict, reason
):
    snapshot = snapshot_for(setup(tmp_path), request={"kind": "learn", "trait": "Medicine"})
    result = asyncio.run(
        adapter(
            Classifier(probabilities), threshold=1.0, allow_threshold=0.8, deny_threshold=0.2
        ).observe(snapshot, reference=SafetyVerdict.ALLOW)
    )
    assert result.observation.decision == verdict
    assert result.observation.decision_reason == reason
    assert result.observation.error_category is None
    assert result.observation.agreement is (verdict == "allow")
    assert result.observation.decision_reference == "allow"


@pytest.mark.parametrize(
    "config",
    [
        {"allow_threshold": 0.1, "deny_threshold": 0.2},
        {"allow_threshold": 0.5, "deny_threshold": 0.5},
        {"allow_threshold": 1.1},
        {"deny_threshold": -0.1},
        {"allow_threshold": float("nan")},
        {"deny_threshold": float("inf")},
    ],
)
def test_invalid_thresholds_rejected(config):
    with pytest.raises(ValidationError):
        AdvancementClassifierConfig(**config)


def test_provider_projection_excludes_ids_hidden_state_chat_and_legacy_prose(tmp_path):
    snapshot = snapshot_for(setup(tmp_path))
    projections = json.loads(snapshot.projections_json)
    projections["current_scene"].update(
        title="Лагерь",
        participants=["discord-participant"],
        state={
            "description": (
                "Есть время для учёбы; <@123456789012345678> и alice отдыхают. "
                "Discord 876543210987654321"
            ),
            "facts": ["Место безопасно", {"hidden": "nested-secret"}],
            "gm_context": "gm-secret",
            "hidden": "hidden-secret",
            "danger": "unvetted-state",
        },
    )
    projections["actor_character"].update(name="private-character", available_xp=987654)
    projections["advancement_request"].update(
        kind="learn",
        trait="Medicine",
        justification="A public teacher offers practice",
        aspects=["First aid", "Research"],
        private_note="private-request",
        character_id="private-character-id",
        level=654321,
    )
    snapshot = replace(
        snapshot,
        projections_json=json.dumps(projections),
        chat_messages_json=json.dumps([{"content": "private-chat", "author_id": "chat-author"}]),
        domain_events_json=json.dumps(
            [
                {
                    "event_type": "scene_patched",
                    "causation_id": "discord-event",
                    "payload": {
                        "scene_id": "camp",
                        "add_facts": ["A mentor arrives"],
                        "remove_facts": ["The camp is under attack"],
                        "summary": "raw-provider-prose",
                        "hidden": "hidden-event",
                    },
                },
                {
                    "event_type": "scene_patched",
                    "payload": {"scene_id": "elsewhere", "add_facts": ["other-scene-secret"]},
                },
                {"event_type": "secret_revealed", "payload": {"text": "raw-secret-event"}},
            ]
        ),
    )
    request = advancement_classification_request(snapshot)
    wire = request.model_dump_json()
    for excluded in (
        "game_id",
        "player_id",
        "character_id",
        "scene_id",
        "participants",
        "discord-participant",
        "123456789012345678",
        "876543210987654321",
        "alice",
        "private-character",
        "987654",
        "654321",
        "gm-secret",
        "hidden-secret",
        "unvetted-state",
        "private-request",
        "private-chat",
        "chat-author",
        "discord-event",
        "raw-provider-prose",
        "hidden-event",
        "other-scene-secret",
        "raw-secret",
        "nested-secret",
        "available_xp",
        "traits",
        "level",
    ):
        assert excluded not in wire
    assert request.state["scene"]["facts"] == ["Место безопасно"]
    assert request.state["recent_public_fiction"] == [
        {"added_facts": ["A mentor arrives"], "removed_facts": ["The [identifier] is under attack"]}
    ]
    assert "Есть время" in request.state["scene"]["description"]


@pytest.mark.parametrize("public_name", ["Наставник Мирон", "npc.internal.mentor.17"])
def test_nested_canonical_id_is_redacted_from_all_public_text_not_public_names(
    tmp_path, public_name
):
    internal_id = "npc.internal.mentor.17"
    snapshot = snapshot_for(
        setup(tmp_path),
        request={
            "kind": "learn",
            "trait": "Medicine",
            "justification": f"{public_name} ({internal_id}) offers lessons",
            "aspects": [f"First aid with {internal_id}"],
        },
    )
    projections = json.loads(snapshot.projections_json)
    projections["current_scene"].update(
        title=f"Training with {public_name}",
        state={
            "npcs": [{"id": internal_id, "name": public_name}],
            "description": f"{public_name} ({internal_id}) offers training in a safe room.",
            "facts": [f"{public_name} ({internal_id}) has time to teach."],
        },
    )
    snapshot = replace(
        snapshot,
        projections_json=json.dumps(projections),
        domain_events_json=json.dumps(
            [
                {
                    "event_type": "scene_patched",
                    "payload": {
                        "scene_id": snapshot.scene_id,
                        "add_facts": [f"{public_name} ({internal_id}) arrived."],
                        "remove_facts": [f"{public_name} ({internal_id}) was unavailable."],
                    },
                }
            ]
        ),
    )
    port = Classifier()
    result = asyncio.run(adapter(port).observe(snapshot, reference=SafetyVerdict.ALLOW))
    request = port.requests[0]
    assert internal_id not in request.model_dump_json()
    assert internal_id not in result.observation.model_dump_json()
    expected_name = "[identifier]" if public_name == internal_id else public_name
    assert request.state["scene"]["title"] == f"Training with {expected_name}"
    assert request.state["scene"]["description"] == (
        f"{expected_name} ([identifier]) offers training in a safe room."
    )
    assert request.state["scene"]["facts"] == [f"{expected_name} ([identifier]) has time to teach."]


@pytest.mark.parametrize("probabilities", [(0.99, 0.99), (0.01, 0.99), (0.5, 0.5)])
@pytest.mark.parametrize("allowed", [True, False])
def test_shadow_never_changes_authoritative_result_or_prose(tmp_path, probabilities, allowed):
    store = setup(tmp_path)
    pipeline = RecordingPipeline(allowed=allowed)
    shadow = ShadowAdvancementSafetyDecider(
        decider_for(store, pipeline), adapter(Classifier(probabilities))
    )
    result = asyncio.run(shadow.assess(snapshot_for(store)))
    assert result.verdict is (SafetyVerdict.ALLOW if allowed else SafetyVerdict.DENY)
    assert result.reason is (SafetyReason.LEGACY_ALLOWED if allowed else SafetyReason.LEGACY_DENIED)
    assert result.display_detail == "Свободный текст GM"
    assert result.evidence == ("camp",)


def test_replay_skips_classifier_and_baseline_provider(tmp_path):
    store = setup(tmp_path)
    pipeline, port = RecordingPipeline(), Classifier()
    decider = ShadowAdvancementSafetyDecider(decider_for(store, pipeline), adapter(port))
    snapshot = snapshot_for(store)
    first = asyncio.run(decider.assess(snapshot, checkpoint_event_id="request"))
    replay = asyncio.run(decider.assess(snapshot, checkpoint_event_id="request"))
    assert not first.replayed and replay.replayed
    assert first == replay
    assert len(port.requests) == len(pipeline.contexts) == 1


def test_existing_legacy_checkpoint_skips_new_shadow(tmp_path):
    store = setup(tmp_path)
    baseline, port = decider_for(store, RecordingPipeline()), Classifier()
    snapshot = snapshot_for(store)
    asyncio.run(baseline.assess(snapshot, checkpoint_event_id="before-shadow"))
    result = asyncio.run(
        ShadowAdvancementSafetyDecider(baseline, adapter(port)).assess(
            snapshot, checkpoint_event_id="before-shadow"
        )
    )
    assert result.replayed
    assert not port.requests


def test_off_and_baseline_errors_skip_classifier(tmp_path):
    store = setup(tmp_path)
    port = Classifier()
    off = AdvancementSafetyClassifier(
        SemanticClassifierExecutor(port, requested_model="alias"), AdvancementClassifierConfig()
    )
    asyncio.run(
        ShadowAdvancementSafetyDecider(decider_for(store, RecordingPipeline()), off).assess(
            snapshot_for(store)
        )
    )
    error = RuntimeError("legacy-failure")
    with pytest.raises(RuntimeError) as raised:
        asyncio.run(
            ShadowAdvancementSafetyDecider(
                decider_for(store, RecordingPipeline(error=error)), adapter(port)
            ).assess(snapshot_for(store))
        )
    assert raised.value is error
    assert not port.requests


def test_classifier_error_and_projection_error_are_isolated_without_raw_logs(tmp_path, caplog):
    store = setup(tmp_path)
    baseline = decider_for(store, RecordingPipeline())
    port = Classifier(error=ClassifierRateLimitError("sensitive-provider-body"))
    result = asyncio.run(
        ShadowAdvancementSafetyDecider(baseline, adapter(port)).assess(snapshot_for(store))
    )
    assert result.verdict is SafetyVerdict.ALLOW
    # Unknown request kind only breaks shadow construction; legacy still decides normally.
    malformed = snapshot_for(store, request={"kind": "unknown-sensitive-request"})
    result = asyncio.run(ShadowAdvancementSafetyDecider(baseline, adapter()).assess(malformed))
    assert result.verdict is SafetyVerdict.ALLOW
    assert "sensitive" not in caplog.text


def test_cancellation_is_not_swallowed(tmp_path):
    store = setup(tmp_path)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            ShadowAdvancementSafetyDecider(
                decider_for(store, RecordingPipeline()),
                adapter(Classifier(error=asyncio.CancelledError())),
            ).assess(snapshot_for(store))
        )


def test_generic_span_records_aggregate_agreement_and_distributions_without_state(tmp_path):
    store = setup(tmp_path)
    records = []

    class Sink:
        def record_stage_spans(self, values):
            records.extend(values)

    binding = bind_trace(Sink(), trace_id="trace")
    try:
        result = asyncio.run(
            adapter(Classifier((0.01, 0.99))).observe(
                snapshot_for(store), reference=SafetyVerdict.DENY
            )
        )
    finally:
        reset_trace(binding)
    assert result.observation.agreement is True  # Not "all noul answers must be false".
    attrs = records[0]["attributes"]
    assert attrs["use_case"] == "advancement"
    assert attrs["taxonomy_version"] == "advancement_safety.raise.v1"
    assert attrs["requested_model"] == "alias-latest"
    assert attrs["resolved_model"] == "resolved-1"
    assert attrs["version"] == "1.0"
    assert attrs["request_id"] == "req-123"
    assert attrs["usage"] == {"input_tokens": 20}
    assert attrs["cost"] == 0.001
    assert attrs["answers"]["scene_safe_enough"]["probabilities"] == {"true": 0.01, "false": 0.99}
    assert attrs["decision"] == "deny" and attrs["agreement"] is True
    assert attrs["decision_thresholds"] == {"allow": 0.95, "deny": 0.05}
    assert "private" not in json.dumps(attrs)
    assert "alice" not in json.dumps(attrs)


def test_missing_required_answer_is_a_sanitized_error_not_an_allow(tmp_path):
    snapshot = snapshot_for(setup(tmp_path), request={"kind": "learn", "trait": "Medicine"})
    result = asyncio.run(
        adapter(Classifier((0.99, 0.99))).observe(snapshot, reference=SafetyVerdict.ALLOW)
    )
    assert result.response is None
    assert result.observation.outcome == "error"
    assert result.observation.error_category == "response"
    assert result.observation.decision is None


def test_advancement_timeout_is_observed_without_changing_legacy_assessment(tmp_path):
    class Slow:
        async def classify(self, request):
            await asyncio.sleep(60)

    store = setup(tmp_path)
    classifier = adapter(Slow(), timeout_seconds=0.001)
    result = asyncio.run(classifier.observe(snapshot_for(store), reference=SafetyVerdict.ALLOW))
    assert result.observation.error_category == "timeout"
    assert result.observation.error_transient is True
    authoritative = asyncio.run(
        ShadowAdvancementSafetyDecider(decider_for(store, RecordingPipeline()), classifier).assess(
            snapshot_for(store)
        )
    )
    assert authoritative.verdict is SafetyVerdict.ALLOW


def test_maximum_projected_unicode_input_stays_within_request_limit(tmp_path):
    snapshot = snapshot_for(
        setup(tmp_path),
        request={
            "kind": "learn",
            "trait": "🛡" * 2000,
            "justification": "🛡" * 2000,
            "aspects": ["🛡" * 2000] * 20,
        },
    )
    projections = json.loads(snapshot.projections_json)
    projections["current_scene"].update(
        title="🛡" * 2000,
        state={"description": "🛡" * 2000, "facts": ["🛡" * 2000] * 20},
    )
    snapshot = replace(
        snapshot,
        projections_json=json.dumps(projections),
        domain_events_json=json.dumps(
            [
                {
                    "event_type": "scene_patched",
                    "payload": {
                        "scene_id": "camp",
                        "add_facts": ["🛡" * 2000] * 20,
                        "remove_facts": ["🛡" * 2000] * 20,
                    },
                }
                for _ in range(4)
            ]
        ),
    )
    request = advancement_classification_request(snapshot)
    assert len(request.canonical_bytes()) < 128_000
