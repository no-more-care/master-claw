import asyncio
import json
from dataclasses import replace

import pytest
from pydantic import ValidationError
from test_advancement_safety_classifier import Classifier
from test_player_narration import NeverIntent, setup
from test_player_narration_review import Pipeline, capture

from masterclaw.app.context_inputs import ContextInputSnapshot
from masterclaw.app.i18n import tr
from masterclaw.app.legacy_player_narration_review import LegacyPlayerNarrationReview
from masterclaw.app.message_handler import MessageApplication
from masterclaw.app.player_narration_review import (
    NarrationAssessment,
    NarrationReason,
    NarrationVerdict,
)
from masterclaw.app.player_narration_rights_classifier import (
    PlayerNarrationRightsClassifier,
    ShadowNarrationRightsDecider,
    narration_rights_request,
)
from masterclaw.classifiers.base import ClassifierRateLimitError
from masterclaw.classifiers.executor import SemanticClassifierExecutor
from masterclaw.classifiers.policy import NarrationRightsClassifierConfig
from masterclaw.pipelines.state_decision import StateDecisionRouter
from masterclaw.telemetry import bind_trace, reset_trace

QUESTIONS = [
    "preserves_resolved_outcome",
    "within_rights_scope",
    "actor_only",
    "publicly_supported",
]


def adapter(port=None, **config):
    return PlayerNarrationRightsClassifier(
        SemanticClassifierExecutor(port or Classifier((0.99,) * 4), requested_model="alias-latest"),
        NarrationRightsClassifierConfig(mode="shadow", **config),
    )


@pytest.mark.parametrize("text", ["Я отступаю, но не сдаюсь.", "I step back, still defiant."])
def test_exact_questions_and_bounded_bilingual_shape(tmp_path, text):
    _, snapshot, *_ = capture(setup(tmp_path))
    request = narration_rights_request(replace(snapshot, submitted_text=text))
    assert request.taxonomy_version == "player_narration_rights.v1"
    assert list(request.questions) == QUESTIONS
    assert all(question.type == "noul" for question in request.questions.values())
    assert all(
        set(question.criteria) == {"true", "false"} for question in request.questions.values()
    )
    assert request.state["submitted_narration"] == text
    assert request.state["original_declaration"] == "Act"
    assert request.state["authority"] == snapshot.narrator_rights
    assert request.state["rights_level"] == snapshot.narrator_rights_level
    assert request.state["resolved_outcome"] == (
        "success" if snapshot.hits >= snapshot.difficulty else "failure"
    )
    long = narration_rights_request(replace(snapshot, submitted_text="x" * 10000))
    assert len(long.state["submitted_narration"]) == 4000


@pytest.mark.parametrize(
    ("probabilities", "verdict", "reason"),
    [
        ((0.8,) * 4, "allow", "compliant"),
        ((0.799, 0.9, 0.9, 0.9), "uncertain", "insufficient_signal"),
        ((0.201, 0.9, 0.9, 0.9), "uncertain", "insufficient_signal"),
        ((0.2, 0.0, 0.0, 0.0), "deny", "outcome_contradiction"),
        ((0.9, 0.2, 0.0, 0.0), "deny", "rights_exceeded"),
        ((0.9, 0.9, 0.2, 0.0), "deny", "other_pc_control"),
        ((0.9, 0.9, 0.9, 0.2), "deny", "unsupported_or_hidden_fact"),
    ],
)
def test_independent_thresholds_boundaries_and_stable_priority(
    tmp_path, probabilities, verdict, reason
):
    _, snapshot, *_ = capture(setup(tmp_path))
    result = asyncio.run(
        adapter(
            Classifier(probabilities),
            threshold=1,
            allow_threshold=0.8,
            deny_threshold=0.2,
        ).observe(snapshot, reference=NarrationVerdict.DENY)
    )
    observation = result.observation
    assert observation.decision == verdict and observation.decision_reason == reason
    assert observation.reference == {}  # No fabricated question-level baseline answers.
    assert observation.decision_reference == "deny"
    assert observation.agreement is (verdict == "deny")
    assert observation.error_category is None
    assert observation.decision_thresholds == {"allow": 0.8, "deny": 0.2}


@pytest.mark.parametrize(
    "config",
    [
        {"mode": "active"},
        {"allow_threshold": 0.2, "deny_threshold": 0.2},
        {"allow_threshold": 1.1},
        {"deny_threshold": -0.1},
        {"allow_threshold": float("nan")},
    ],
)
def test_invalid_narration_config_rejected(config):
    with pytest.raises(ValidationError):
        NarrationRightsClassifierConfig(**config)


def test_privacy_recursive_ids_no_hidden_history_or_provider_prose(tmp_path):
    _, snapshot, *_ = capture(setup(tmp_path))
    projections = snapshot.inputs.projections
    npc_id = "npc.internal.mentor.17"
    echo = f"Мирон ({npc_id}) greets player.internal.22 beside <@123456789012345678>."
    projections["current_scene"].update(
        title=f"Лагерь {npc_id}",
        participant_characters=[
            {"player_id": snapshot.fiction.player_id, "name": "Actor"},
            {"player_id": "player.internal.22", "name": "Вера", "private": "participant-private"},
        ],
        state={
            "description": echo,
            "facts": [echo, {"hidden": "nested-secret"}],
            "npcs": [
                {"id": npc_id, "name": "Мирон", "state": echo, "gm_context": "npc-gm"},
                {
                    "id": "hidden-npc-id",
                    "name": "SecretNpc",
                    "state": "SecretState",
                    "hidden": True,
                },
            ],
            "hidden": "hidden-scene",
            "gm_context": "gm-secret",
            "secret_plot": "plot-secret",
        },
    )
    projections["actor_character"].update(
        name="Актёр",
        biography="private-bio",
        traits=[{"name": "private-trait"}],
        available_xp=999999,
        reason="legacy-reason",
        scale_back_request="legacy-scale",
        approved_narration="legacy-approved",
    )
    projections["roll_result"]["original_declaration"] = echo
    projections["gm_world_context"] = {"secret": "world-secret"}
    events = [
        {
            "event_type": "scene_patched",
            "payload": {
                "scene_id": snapshot.fiction.scene_id,
                "add_facts": [echo],
                "remove_facts": [echo],
                "summary": "provider-summary",
                "hidden": "hidden-event",
            },
        },
        {
            "event_type": "scene_patched",
            "payload": {"scene_id": "elsewhere", "add_facts": ["other-scene"]},
        },
        {"event_type": "secret_revealed", "payload": {"text": "raw-secret-event"}},
    ]
    snapshot = replace(
        snapshot,
        submitted_text=echo,
        inputs=ContextInputSnapshot.capture(
            projections,
            events,
            [{"author_id": "chat-author", "content": "private-chat"}],
        ),
    )
    port = Classifier((0.99,) * 4)
    result = asyncio.run(adapter(port).observe(snapshot, reference=NarrationVerdict.ALLOW))
    wire = port.requests[0].model_dump_json()
    observation = result.observation.model_dump_json()
    for excluded in (
        npc_id,
        "player.internal.22",
        "123456789012345678",
        "hidden-npc-id",
        "SecretNpc",
        "SecretState",
        "npc-gm",
        "nested-secret",
        "participant-private",
        "hidden-scene",
        "gm-secret",
        "plot-secret",
        "private-bio",
        "private-trait",
        "999999",
        "legacy-reason",
        "legacy-scale",
        "legacy-approved",
        "world-secret",
        "provider-summary",
        "hidden-event",
        "other-scene",
        "raw-secret-event",
        "chat-author",
        "private-chat",
        "game_id",
        "roll_id",
        "player_id",
        "scene_id",
        "character_id",
        "pending_revision",
        "biography",
        "traits",
    ):
        assert excluded not in wire
        assert excluded not in observation
    state = port.requests[0].state
    assert state["actor"] == {"name": "Актёр", "role": "actor"}
    assert state["other_player_characters"] == [{"name": "Вера", "role": "other_player_character"}]
    assert state["scene"]["npcs"][0]["name"] == "Мирон"
    assert len(state["scene"]["npcs"]) == 1
    assert state["scene"]["facts"] == [state["scene"]["description"]]
    assert state["recent_public_fiction"] == [
        {
            "added_facts": [state["scene"]["description"]],
            "removed_facts": [state["scene"]["description"]],
        }
    ]
    assert "Мирон" not in observation


@pytest.mark.parametrize("accepted", [True, False])
@pytest.mark.parametrize("probabilities", [(0.99,) * 4, (0.01,) * 4, (0.5,) * 4])
def test_fresh_allow_and_deny_observed_replay_skips_and_prose_unchanged(
    tmp_path, accepted, probabilities
):
    store = setup(tmp_path)
    app, snapshot, *_ = capture(store)
    pipeline = Pipeline(accepted)
    legacy = LegacyPlayerNarrationReview(store=store, context=app._context, pipeline=pipeline)
    port = Classifier(probabilities)
    wrapper = ShadowNarrationRightsDecider(legacy, adapter(port))
    assessment = asyncio.run(wrapper.assess(snapshot, "review"))
    text = asyncio.run(legacy.materialize(snapshot, assessment, "review"))
    assert assessment.verdict.value == ("allow" if accepted else "deny")
    assert text.publication_text == pipeline.result.approved_narration
    assert text.feedback_text == pipeline.result.scale_back_request
    assert len(port.requests) == len(pipeline.calls) == 1
    assert asyncio.run(wrapper.assess(snapshot, "review")).replayed
    assert len(port.requests) == len(pipeline.calls) == 1


def test_exact_assessment_identity_and_error_isolation(tmp_path, caplog):
    _, snapshot, *_ = capture(setup(tmp_path))
    expected = NarrationAssessment(NarrationVerdict.ALLOW, NarrationReason.LEGACY_ACCEPTED)

    class Baseline:
        async def assess(self, snapshot, checkpoint_event_id):
            return expected

    port = Classifier(error=ClassifierRateLimitError("sensitive-provider-body"))
    result = asyncio.run(
        ShadowNarrationRightsDecider(Baseline(), adapter(port)).assess(snapshot, "review")
    )
    assert result is expected
    assert len(port.requests) == 1
    assert "sensitive" not in caplog.text
    malformed = replace(snapshot, inputs=ContextInputSnapshot.capture({}, [], []))
    assert (
        asyncio.run(ShadowNarrationRightsDecider(Baseline(), adapter()).assess(malformed, "review"))
        is expected
    )


@pytest.mark.parametrize("kind", ["replayed", "uncertain", "off", "exception"])
def test_nonfresh_off_or_failed_baseline_never_calls_classifier(tmp_path, kind):
    _, snapshot, *_ = capture(setup(tmp_path))
    port = Classifier((0.99,) * 4)

    class Baseline:
        async def assess(self, snapshot, checkpoint_event_id):
            if kind == "exception":
                raise RuntimeError("legacy-failure")
            return NarrationAssessment(
                NarrationVerdict.UNCERTAIN if kind == "uncertain" else NarrationVerdict.ALLOW,
                NarrationReason.UNDETERMINED,
                replayed=kind == "replayed",
            )

    classifier = (
        adapter(port)
        if kind != "off"
        else PlayerNarrationRightsClassifier(
            SemanticClassifierExecutor(port, requested_model="alias"),
            NarrationRightsClassifierConfig(),
        )
    )
    if kind == "exception":
        with pytest.raises(RuntimeError, match="legacy-failure"):
            asyncio.run(
                ShadowNarrationRightsDecider(Baseline(), classifier).assess(snapshot, "review")
            )
    else:
        asyncio.run(ShadowNarrationRightsDecider(Baseline(), classifier).assess(snapshot, "review"))
    assert port.requests == []


@pytest.mark.parametrize("mutate", [False, True])
def test_provider_error_alone_preserves_ux_but_shadow_latency_staleness_retries(tmp_path, mutate):
    store = setup(tmp_path)
    app, snapshot, pending, _, _, message = capture(store)
    pipeline = Pipeline(True)
    legacy = LegacyPlayerNarrationReview(store=store, context=app._context, pipeline=pipeline)

    class Port:
        async def classify(self, request):
            if mutate:
                store.apply_scene_patch(
                    game_id="game",
                    scene_id="room",
                    expected_revision=snapshot.fiction.scene_revision,
                    causation_id="concurrent",
                    summary="The door closes.",
                    add_facts=["The door is barred"],
                    remove_facts=[],
                )
            raise ClassifierRateLimitError("transient")

    application = MessageApplication(
        store=store,
        context=app._context,
        state_router=StateDecisionRouter(NeverIntent()),
        narration_rights_decider=ShadowNarrationRightsDecider(legacy, adapter(Port())),
        narration_text_port=legacy,
    )
    result = asyncio.run(application._handle_player_narration(message=message, pending=pending))
    if mutate:
        assert result == tr("ru", "fiction_context_changed_retry")
        assert store.open_pending(game_id="game", player_id="alice") is not None
    else:
        assert result.deliveries[0].content == pipeline.result.approved_narration
        assert store.open_pending(game_id="game", player_id="alice") is None


def test_generic_span_contains_calibration_not_state(tmp_path):
    _, snapshot, *_ = capture(setup(tmp_path))
    records = []

    class Sink:
        def record_stage_spans(self, values):
            records.extend(values)

    token = bind_trace(Sink(), trace_id="trace")
    try:
        asyncio.run(
            adapter(Classifier((0.01, 0.99, 0.99, 0.99))).observe(
                snapshot, reference=NarrationVerdict.DENY
            )
        )
    finally:
        reset_trace(token)
    attrs = records[0]["attributes"]
    assert attrs["use_case"] == "player_narration_rights"
    assert attrs["taxonomy_version"] == "player_narration_rights.v1"
    assert attrs["requested_model"] == "alias-latest" and attrs["resolved_model"] == "resolved-1"
    assert attrs["version"] == "1.0" and attrs["request_id"] == "req-123"
    assert attrs["usage"] == {"input_tokens": 20} and attrs["cost"] == 0.001
    assert attrs["answers"][QUESTIONS[0]]["probabilities"] == {"true": 0.01, "false": 0.99}
    assert attrs["decision"] == "deny" and attrs["agreement"] is True
    assert attrs["decision_reason"] == "outcome_contradiction"
    assert attrs["decision_thresholds"] == {"allow": 0.95, "deny": 0.05}
    assert attrs["reference"] == {} and attrs["decision_reference"] == "deny"
    assert "submitted_narration" not in json.dumps(attrs) and "private" not in json.dumps(attrs)


def test_missing_answer_and_timeout_are_sanitized_and_cancellation_propagates(tmp_path):
    _, snapshot, *_ = capture(setup(tmp_path))
    result = asyncio.run(
        adapter(Classifier((0.99,) * 3)).observe(snapshot, reference=NarrationVerdict.ALLOW)
    )
    assert result.observation.error_category == "response"
    assert result.observation.decision is None

    class Slow:
        async def classify(self, request):
            await asyncio.sleep(1)

    result = asyncio.run(
        adapter(Slow(), timeout_seconds=0.001).observe(snapshot, reference=NarrationVerdict.DENY)
    )
    assert result.observation.error_category == "timeout"
    assert result.observation.decision is None
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            adapter(Classifier(error=asyncio.CancelledError())).observe(
                snapshot, reference=NarrationVerdict.ALLOW
            )
        )
