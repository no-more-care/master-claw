import asyncio
import json
from dataclasses import replace

import pytest
from pydantic import ValidationError
from test_action_preparation import Pipeline, assembler_for, service_for
from test_fiction_context_guards import active_store, concurrent_mutation, message

from masterclaw.app.action_capability_classifier import (
    ACTION_CAPABILITY_TAXONOMY,
    ActionCapabilityClassifier,
    action_capability_request,
)
from masterclaw.app.action_preparation import (
    ActionCapabilityReference,
    ActionCapabilitySnapshot,
    ActionPreparationService,
    ActionPreparationSnapshot,
)
from masterclaw.app.fiction_context import FictionContextChangedError, FictionContextSnapshot
from masterclaw.classifiers.base import ClassificationResponse, ClassifierRateLimitError
from masterclaw.classifiers.executor import SemanticClassifierExecutor
from masterclaw.classifiers.policy import ActionCapabilityClassifierConfig
from masterclaw.domain.characters import Condition, PlotItem
from masterclaw.domain.mechanics import Flag, FlagType, TemporaryBonus, TemporaryBonusType
from masterclaw.pipelines.action import ActionInterpretation
from masterclaw.telemetry import bind_trace, reset_trace


class Port:
    def __init__(
        self,
        choices=("possible", "supported", "own_character"),
        *,
        probability=0.99,
        confidence=0.99,
        error=None,
        mutation=None,
    ):
        self.choices = choices
        self.probability = probability
        self.confidence = confidence
        self.error = error
        self.mutation = mutation
        self.requests = []

    async def classify(self, request):
        self.requests.append(request)
        if self.mutation:
            self.mutation()
        if self.error:
            raise self.error
        return ClassificationResponse(
            request_key=request.request_key,
            taxonomy_version=request.taxonomy_version,
            provider="test",
            model="resolved-1",
            version="version-1",
            request_id="request-1",
            usage={"total_tokens": 40, "user_id": "private-id"},
            cost=0.001,
            answers={
                key: {
                    "type": "choice",
                    "choice": choice,
                    "confidence": self.confidence,
                    "probabilities": {
                        label: self.probability
                        if label == choice
                        else (1 - self.probability) / (len(question.criteria) - 1)
                        for label in question.criteria
                    },
                }
                for (key, question), choice in zip(
                    request.questions.items(), self.choices, strict=True
                )
            },
        )


def adapter(port=None, **config):
    return ActionCapabilityClassifier(
        SemanticClassifierExecutor(port or Port(), requested_model="alias-latest"),
        ActionCapabilityClassifierConfig(mode="shadow", **config),
    )


def snapshot_for(store, declaration="Я пробую открыть дверь / I try to open the door"):
    actor = store.character_for_player(game_id="game", player_id="alice")
    scene = store.scene_projection(game_id="game", player_id="alice")
    return ActionCapabilitySnapshot(
        declaration=declaration,
        fiction=ActionPreparationSnapshot(
            context=FictionContextSnapshot.capture(
                game_id="game", player_id="alice", character=actor, scene=scene
            ),
            character=actor,
            scene_json=json.dumps(scene),
        ),
        participants_json=json.dumps([{"player_id": "alice", "name": "Mara"}]),
        source_ids=("discord-event", "discord-channel"),
    )


def service(store, pipeline, observer):
    return ActionPreparationService(
        store=store,
        pipeline=pipeline,
        observer=observer,
        assemble_context=assembler_for(store)._assemble_context,
    )


def test_exact_independent_taxonomies_and_ru_en_input_shape(tmp_path):
    request = action_capability_request(snapshot_for(active_store(tmp_path)))
    assert request.taxonomy_version == ACTION_CAPABILITY_TAXONOMY == "action_capability.v1"
    assert {key: set(question.criteria) for key, question in request.questions.items()} == {
        "feasibility": {"possible", "impossible", "underspecified"},
        "sheet_support": {"supported", "unsupported", "not_required", "uncertain"},
        "authority_scope": {
            "own_character",
            "other_pc_control",
            "narrator_world_change",
            "uncertain",
        },
    }
    assert all(question.type == "choice" for question in request.questions.values())
    assert "Я пробую" in request.state["declaration"] and "I try" in request.state["declaration"]
    assert request.state["actor"]["traits"] == [{"name": "Lore", "aspects": ["Runes"]}]
    assert set(request.state) == {"declaration", "actor", "scene"}
    assert request.state["scene"]["participants"] == [{"name": "Mara", "role": "actor"}]


@pytest.mark.parametrize(
    ("choices", "probability", "confidence", "decision", "reason"),
    [
        (
            ("possible", "supported", "own_character"),
            0.95,
            0.95,
            "capable",
            "public_capability_supported",
        ),
        (
            ("possible", "not_required", "own_character"),
            0.99,
            0.99,
            "capable",
            "public_capability_supported",
        ),
        (
            ("possible", "supported", "own_character"),
            0.949,
            0.99,
            "uncertain",
            "insufficient_signal",
        ),
        (
            ("possible", "supported", "own_character"),
            0.99,
            0.949,
            "uncertain",
            "insufficient_signal",
        ),
        (("impossible", "supported", "own_character"), 0.98, 0.98, "blocked", "fiction_impossible"),
        (
            ("impossible", "supported", "own_character"),
            0.979,
            0.99,
            "uncertain",
            "insufficient_signal",
        ),
        (("possible", "supported", "other_pc_control"), 0.98, 0.98, "blocked", "other_pc_control"),
        (
            ("possible", "supported", "narrator_world_change"),
            0.98,
            0.98,
            "blocked",
            "world_authority",
        ),
        (
            ("impossible", "supported", "other_pc_control"),
            0.99,
            0.99,
            "blocked",
            "other_pc_control",
        ),
        (
            ("possible", "unsupported", "own_character"),
            0.99,
            0.99,
            "uncertain",
            "sheet_support_advisory",
        ),
        (
            ("underspecified", "uncertain", "own_character"),
            0.99,
            0.99,
            "uncertain",
            "declaration_underspecified",
        ),
    ],
)
def test_conservative_reducer_boundaries_and_advisory_sheet_support(
    tmp_path, choices, probability, confidence, decision, reason
):
    result = asyncio.run(
        adapter(Port(choices, probability=probability, confidence=confidence)).observe(
            snapshot_for(active_store(tmp_path)), reference=ActionCapabilityReference.PROCEED
        )
    )
    assert result.observation.decision == decision
    assert result.observation.decision_reason == reason
    assert result.observation.agreement is (decision == "capable")
    assert result.observation.reference == {}  # No invented per-question baseline answers.
    assert result.observation.error_category is None


def test_own_thresholds_not_inherited_state_threshold(tmp_path):
    result = asyncio.run(
        adapter(
            Port(probability=0.8, confidence=0.8),
            threshold=1.0,
            capable_threshold=0.8,
            blocked_threshold=0.9,
        ).observe(snapshot_for(active_store(tmp_path)), reference=ActionCapabilityReference.PROCEED)
    )
    assert result.observation.decision == "capable"
    assert result.observation.decision_thresholds == {"capable": 0.8, "blocked": 0.9}


@pytest.mark.parametrize(
    "config",
    [
        {"capable_threshold": 0.49},
        {"blocked_threshold": 1.1},
        {"capable_threshold": float("nan")},
        {"mode": "active"},
    ],
)
def test_invalid_action_capability_config(config):
    with pytest.raises(ValidationError):
        ActionCapabilityClassifierConfig(**config)


def test_privacy_including_nested_id_echoes_and_public_names(tmp_path):
    snapshot = snapshot_for(
        active_store(tmp_path),
        "Глеб npc.secret.1 helps Mara hero near bob-internal <@123456789012345678>",
    )
    character = replace(
        snapshot.fiction.character,
        biography="private-biography",
        experience_earned=654321,
        conditions=(Condition("Tired", "private-condition-source"),),
        plot_items=(PlotItem("Rope", "A long rope offered by npc.secret.1 bonus.internal.1"),),
        sheet=replace(
            snapshot.fiction.character.sheet,
            flags=(Flag("Protect Глеб", FlagType.GOAL),),
            temporary_bonuses=(
                TemporaryBonus("bonus.internal.1", TemporaryBonusType.EXTRA_DIE, "private-trigger"),
            ),
        ),
    )
    scene = snapshot.fiction.scene
    scene["state"] = {
        "description": "Глеб npc.secret.1 guards the room with bob-internal",
        "facts": ["npc.secret.1 lends the rope", "The door is closed"],
        "npcs": [{"id": "npc.secret.1", "name": "Глеб", "gm_context": "npc-secret"}],
        "secret_plot": "private-plot",
        "hidden": "private-hidden",
    }
    snapshot = replace(
        snapshot,
        fiction=replace(snapshot.fiction, character=character, scene_json=json.dumps(scene)),
        participants_json=json.dumps(
            [
                {"player_id": "alice", "name": "Mara"},
                {"player_id": "bob-internal", "name": "Bob", "character_id": "bob-character-id"},
            ]
        ),
    )
    port = Port()
    result = asyncio.run(
        adapter(port).observe(snapshot, reference=ActionCapabilityReference.PROCEED)
    )
    wire = port.requests[0].model_dump_json()
    for private in (
        "npc.secret.1",
        "bonus.internal.1",
        "bob-internal",
        "bob-character-id",
        "hero",
        "alice",
        "123456789012345678",
        "private-",
        "npc-secret",
        "654321",
        "revision",
        "experience",
        "game_id",
        "player_id",
        "character_id",
        "scene_id",
        "level",
    ):
        assert private not in wire
        assert private not in result.observation.model_dump_json()
    assert "Глеб" in wire and "Bob" in wire and "Mara" in wire and "Rope" in wire
    assert port.requests[0].state["scene"]["participants"][1] == {
        "name": "Bob",
        "role": "other_player_character",
    }
    assert "[identifier]" in port.requests[0].state["declaration"]


def test_shadow_after_fresh_checkpoint_replay_and_prepared_reuse_skip(tmp_path):
    store = active_store(tmp_path)
    pipeline, port = Pipeline(), Port()
    prep = service(store, pipeline, adapter(port))
    first = asyncio.run(prep.prepare(message=message("fresh"), game_id="game", locale="ru"))
    replay = asyncio.run(prep.prepare(message=message("fresh"), game_id="game", locale="ru"))
    reused = asyncio.run(
        prep.prepare(message=message("reuse"), game_id="game", locale="ru", prepared_result=first)
    )
    assert first.interpretation == replay.interpretation == reused.interpretation
    assert len(port.requests) == len(pipeline.calls) == 1


def test_old_checkpoint_replay_has_zero_observer_calls(tmp_path):
    store = active_store(tmp_path)
    old = asyncio.run(
        service_for(store, Pipeline()).prepare(message=message("old"), game_id="game", locale="ru")
    )
    port, pipeline = Port(), Pipeline(error=AssertionError("no baseline replay call"))
    replay = asyncio.run(
        service(store, pipeline, adapter(port)).prepare(
            message=message("old"), game_id="game", locale="ru"
        )
    )
    assert old.interpretation == replay.interpretation
    assert not port.requests and not pipeline.calls


@pytest.mark.parametrize(
    "port",
    [
        Port(("impossible", "unsupported", "other_pc_control")),
        Port(("underspecified", "uncertain", "uncertain")),
        Port(error=ClassifierRateLimitError("sensitive-provider-body")),
    ],
)
def test_shadow_block_uncertainty_and_error_have_zero_gameplay_effect(tmp_path, port, caplog):
    store = active_store(tmp_path)
    before = store.character_for_player(game_id="game", player_id="alice")
    scene = store.scene_projection(game_id="game", player_id="alice")
    pipeline = Pipeline()
    prepared = asyncio.run(
        service(store, pipeline, adapter(port)).prepare(
            message=message("fresh"), game_id="game", locale="ru"
        )
    )
    assert prepared.interpretation.resolution.value == "automatic"
    assert prepared.interpretation.evidence == ["The door is open"]
    assert before == store.character_for_player(game_id="game", player_id="alice")
    assert scene == store.scene_projection(game_id="game", player_id="alice")
    assert len(port.requests) == len(pipeline.calls) == 1
    assert "sensitive" not in caplog.text


@pytest.mark.parametrize("mutation_stage", ["baseline", "observer"])
def test_real_context_changes_trigger_existing_guard_not_classifier_authority(
    tmp_path, mutation_stage
):
    store = active_store(tmp_path)

    def mutation():
        concurrent_mutation(store, "scene", "external")

    pipeline = Pipeline(mutation=mutation if mutation_stage == "baseline" else None)
    port = Port(mutation=mutation if mutation_stage == "observer" else None)
    with pytest.raises(
        FictionContextChangedError, match="action interpretation context changed before commit"
    ):
        asyncio.run(
            service(store, pipeline, adapter(port)).prepare(
                message=message("stale"), game_id="game", locale="ru"
            )
        )
    assert len(port.requests) == (1 if mutation_stage == "observer" else 0)


def test_off_observer_does_not_read_roster_or_call_classifier(tmp_path, monkeypatch):
    store = active_store(tmp_path)
    monkeypatch.setattr(
        store,
        "character_roster",
        lambda *args: (_ for _ in ()).throw(AssertionError("no extra reads")),
    )
    # Use minimal assembly to isolate the pre-existing shared context roster reads.
    from masterclaw.context.assembler import AssembledContext

    port = Port()
    off = ActionCapabilityClassifier(
        SemanticClassifierExecutor(port, requested_model="alias"),
        ActionCapabilityClassifierConfig(),
    )
    prep = ActionPreparationService(
        store=store,
        pipeline=Pipeline(),
        observer=off,
        assemble_context=lambda *args, **kwargs: AssembledContext("", "", (), 0),
    )
    result = asyncio.run(prep.prepare(message=message("off"), game_id="game", locale="ru"))
    assert result.interpretation.resolution.value == "automatic"
    assert not port.requests


def test_generic_span_carries_aggregate_comparison_without_raw_state(tmp_path):
    snapshot = snapshot_for(active_store(tmp_path))
    records = []

    class Sink:
        def record_stage_spans(self, values):
            records.extend(values)

    binding = bind_trace(Sink(), trace_id="capability-test")
    try:
        result = asyncio.run(
            adapter().observe(snapshot, reference=ActionCapabilityReference.PROCEED)
        )
    finally:
        reset_trace(binding)
    attrs = records[0]["attributes"]
    assert records[0]["stage"] == "classifier.action_capability"
    assert attrs["decision"] == "capable" and attrs["decision_comparison"] == "proceed"
    assert attrs["decision_reference"] == "proceed" and attrs["agreement"] is True
    assert set(attrs["answers"]) == {"feasibility", "sheet_support", "authority_scope"}
    assert attrs["resolved_model"] == "resolved-1" and attrs["version"] == "version-1"
    assert attrs["cost"] == 0.001 and attrs["usage"] == {"total_tokens": 40}
    assert "Я пробую" not in json.dumps(attrs) and "alice" not in json.dumps(attrs)
    assert result.observation.agreement is True


@pytest.mark.parametrize(
    ("payload", "reference"),
    [
        ({"resolution": "automatic", "evidence": ["provider prose"]}, "proceed"),
        (
            {
                "resolution": "roll",
                "evidence": ["provider prose"],
                "trait_names": ["Lore"],
                "difficulty": 2,
            },
            "proceed",
        ),
        (
            {
                "resolution": "rejected",
                "evidence": ["provider prose"],
                "rejection_reason": "provider reason",
            },
            "blocked",
        ),
        (
            {
                "resolution": "clarification",
                "evidence": [],
                "clarification_question": "provider question",
            },
            "uncertain",
        ),
    ],
)
def test_service_exposes_only_coarse_baseline_reference_not_interpretation(
    tmp_path, payload, reference
):
    expected = ActionInterpretation.model_validate(payload)

    class Baseline(Pipeline):
        async def run(self, *, task, context):
            return expected

    class Observer:
        enabled = True
        calls = []

        async def observe(self, snapshot, *, reference):
            self.calls.append((snapshot, reference))

    store = active_store(tmp_path)
    observer = Observer()
    prepared = asyncio.run(
        service(store, Baseline(), observer).prepare(
            message=message("baseline"), game_id="game", locale="ru"
        )
    )
    assert prepared.interpretation == expected
    assert observer.calls[0][1].value == reference
    assert "provider prose" not in repr(observer.calls[0][0])
    assert "provider reason" not in repr(observer.calls[0][0])
    assert "provider question" not in repr(observer.calls[0][0])


def test_cancellation_propagates_without_replacing_baseline_checkpoint(tmp_path):
    store = active_store(tmp_path)
    pipeline = Pipeline()
    port = Port(error=asyncio.CancelledError())
    prep = service(store, pipeline, adapter(port))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(prep.prepare(message=message("cancel"), game_id="game", locale="ru"))
    replay = asyncio.run(prep.prepare(message=message("cancel"), game_id="game", locale="ru"))
    assert replay.interpretation.resolution.value == "automatic"
    assert len(port.requests) == len(pipeline.calls) == 1


def test_unexpected_observer_error_is_isolated_and_sanitized(tmp_path, caplog):
    class BrokenObserver:
        enabled = True

        async def observe(self, snapshot, *, reference):
            raise ValueError("private declaration from adapter")

    store = active_store(tmp_path)
    prepared = asyncio.run(
        service(store, Pipeline(), BrokenObserver()).prepare(
            message=message("broken"), game_id="game", locale="ru"
        )
    )
    assert prepared.interpretation.resolution.value == "automatic"
    assert "private declaration" not in caplog.text


def test_bounded_unicode_projection_stays_below_contract_size_limit(tmp_path):
    from masterclaw.domain.mechanics import Trait

    snapshot = snapshot_for(active_store(tmp_path), "🛡" * 12000)
    character = replace(
        snapshot.fiction.character,
        sheet=replace(
            snapshot.fiction.character.sheet,
            name="🛡" * 2000,
            traits=tuple(
                Trait(str(i) + "🛡" * 2000, 2, tuple(str(j) + "🛡" * 2000 for j in range(6)))
                for i in range(12)
            ),
            flags=tuple(Flag("🛡" * 2000, FlagType.GOAL) for _ in range(10)),
        ),
        plot_items=tuple(PlotItem("🛡" * 2000, "🛡" * 2000) for _ in range(12)),
        conditions=tuple(Condition("🛡" * 2000, "private") for _ in range(10)),
    )
    scene = snapshot.fiction.scene
    scene.update(title="🛡" * 2000, state={"description": "🛡" * 2000, "facts": ["🛡" * 2000] * 20})
    snapshot = replace(
        snapshot,
        fiction=replace(snapshot.fiction, character=character, scene_json=json.dumps(scene)),
        participants_json=json.dumps([{"name": "🛡" * 2000}] * 20),
    )
    assert len(action_capability_request(snapshot).canonical_bytes()) < 128_000
