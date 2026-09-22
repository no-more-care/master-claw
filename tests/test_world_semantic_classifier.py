import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from test_advancement_safety_classifier import Classifier
from test_worldgen import valid_world_payload

from masterclaw.app.handlers.world_management import WorldManagementHandlers
from masterclaw.app.i18n import tr
from masterclaw.app.world_semantic_classifier import WorldSemanticClassifier, world_semantic_request
from masterclaw.app.world_semantic_observer import capture_public_world_semantics
from masterclaw.classifier_calibration import ClassifierSpan, classifier_calibration_report
from masterclaw.classifiers.base import ClassifierRateLimitError
from masterclaw.classifiers.executor import SemanticClassifierExecutor
from masterclaw.classifiers.policy import WorldSemanticClassifierConfig
from masterclaw.domain.models import IncomingMessage
from masterclaw.domain.state import WorldState
from masterclaw.pipelines.worldgen import WorldDraft
from masterclaw.storage.sqlite import SQLiteStore
from masterclaw.telemetry import bind_trace, reset_trace


def snapshot(settings=None, content=None):
    return capture_public_world_semantics(
        settings=settings
        if settings is not None
        else {
            "genre": "фэнтези / fantasy",
            "tone": "serious",
            "pregenerated_character_briefs": ["A public investigator"],
        },
        content=content if content is not None else valid_world_payload(),
    )


def adapter(port=None, **config):
    return WorldSemanticClassifier(
        SemanticClassifierExecutor(port or Classifier((0.99, 0.99)), requested_model="alias"),
        WorldSemanticClassifierConfig(**{"mode": "shadow", **config}),
    )


@pytest.mark.parametrize(
    ("settings", "questions"),
    [
        ({"genre": "fantasy"}, ["settings_realized"]),
        (
            {"pregenerated_character_briefs": ["investigator"]},
            ["concepts_realized_by_distinct_templates"],
        ),
        (
            {"tone": "serious", "pregenerated_character_briefs": ["investigator"]},
            ["settings_realized", "concepts_realized_by_distinct_templates"],
        ),
    ],
)
def test_dynamic_positive_questions_no_remote_boundaries(settings, questions):
    request = world_semantic_request(snapshot(settings=settings))
    assert request.taxonomy_version == "worldgen_semantics.v1"
    assert list(request.questions) == questions
    assert all(
        q.type == "noul"
        and set(q.criteria) == {"true", "false"}
        and "True means compliant" in q.instructions
        for q in request.questions.values()
    )
    assert request.state["confirmed_public_settings"] == {
        key: value for key, value in settings.items() if key != "pregenerated_character_briefs"
    }


@pytest.mark.parametrize(
    ("probabilities", "decision", "reason"),
    [
        ((0.9, 0.9), "pass", "public_requirements_realized"),
        ((0.899, 0.9), "uncertain", "insufficient_signal"),
        ((0.201, 0.9), "uncertain", "insufficient_signal"),
        ((0.2, 0), "review", "settings_not_realized"),
        ((1, 0.2), "review", "distinct_concepts_not_realized"),
    ],
)
def test_thresholds_priority_and_no_comparable_reference(probabilities, decision, reason):
    result = asyncio.run(
        adapter(Classifier(probabilities), allow_threshold=0.9, deny_threshold=0.2).observe(
            snapshot()
        )
    )
    assert result.observation.decision == decision
    assert result.observation.decision_reason == reason
    assert result.observation.reference == {}
    assert result.observation.decision_reference is result.observation.agreement is None


def test_public_projection_privacy_nested_identifiers_and_detachment():
    content = valid_world_payload()
    content.update(
        secret_plot="TOP SECRET PHRASE",
        setting_adherence={"genre": "SELF CLAIM"},
        raw_creative_draft="RAW CREATIVE PRIVATE",
        history=["RAW CHAT"],
        provider_metadata={"prose": "PROVIDER FEEDBACK"},
    )
    content["locations"][0].update(
        id="internal-location",
        npcs=[{"id": "nested-npc"}],
        description="Sky City contains nested-npc at internal-location.",
    )
    content["tensions"] = ["nested-npc meets <@123456789012345678> in Sky City"]
    content["character_templates"][0].update(
        player_id="internal-player", xp=9876, private_notes="PRIVATE NOTES"
    )
    settings = {"genre": "фэнтези / fantasy", "content_constraints": ["BOUNDARY PRIVATE"]}
    captured = snapshot(settings=settings, content=content)
    content["premise"] = "MUTATED"
    port = Classifier((0.99,))
    result = asyncio.run(adapter(port).observe(captured))
    request = port.requests[0].model_dump_json()
    observation = result.observation.model_dump_json()
    for forbidden in (
        "TOP SECRET PHRASE",
        "SELF CLAIM",
        "RAW CREATIVE PRIVATE",
        "RAW CHAT",
        "PROVIDER FEEDBACK",
        "internal-location",
        "nested-npc",
        "internal-player",
        "PRIVATE NOTES",
        "123456789012345678",
        "BOUNDARY PRIVATE",
        "9876",
        '"secret_plot"',
        '"setting_adherence"',
        '"traits"',
        '"flags"',
        "MUTATED",
    ):
        assert forbidden not in request and forbidden not in observation
        assert forbidden not in (captured.public_json or "")
    assert "Sky City" in request and "Mira" in request and "фэнтези" in request
    assert "Sky City" not in observation


@pytest.mark.parametrize(
    "kind", ["large", "degraded", "secret_echo", "boundary_echo", "hidden", "empty"]
)
def test_unsafe_projection_skips_without_provider(kind):
    content = valid_world_payload()
    settings = {"genre": "fantasy", "content_constraints": ["PRIVATE BOUNDARY"]}
    if kind == "large":
        content["premise"] = "x" * 4001
    elif kind == "degraded":
        content["degradations"] = ["private reason"]
    elif kind == "secret_echo":
        content["premise"] += content["secret_plot"]
    elif kind == "boundary_echo":
        content["premise"] += "PRIVATE BOUNDARY"
    elif kind == "hidden":
        content["locations"][0]["visibility"] = "private"
    else:
        settings = {}
    captured = snapshot(settings=settings, content=content)
    assert captured.public_json is None
    port = Classifier()
    result = asyncio.run(adapter(port).observe(captured))
    assert result.outcome == "skipped"
    assert result.reason in {"projection_truncated", "projection_unavailable"}
    assert port.requests == []


def test_durable_spans_have_no_positive_branch_gold_and_skips_are_not_calibrated():
    records = []

    class Sink:
        def record_stage_spans(self, rows):
            records.extend(rows)

        def classifier_spans(self, *, since_hours):
            yield from (
                ClassifierSpan(row["stage"], json.dumps(row["attributes"])) for row in records
            )

    binding = bind_trace(Sink(), trace_id="private-trace")
    try:
        asyncio.run(adapter().observe(snapshot()))
        asyncio.run(adapter().observe(snapshot(settings={})))
    finally:
        reset_trace(binding)
    report = classifier_calibration_report(Sink())
    assert report["rows"]["valid"] == report["rows"]["skipped"] == 1
    assert report["rows"]["malformed"] == report["summary"]["comparable_rows"] == 0
    assert report["summary"]["agreement_rate"] is None


@pytest.mark.parametrize(
    "case", ["owner", "race_loser", "off", "error", "observer_error", "uncertain", "cancel"]
)
def test_only_durable_owner_observes_replay_never_recreates(tmp_path, monkeypatch, caplog, case):
    store = SQLiteStore(tmp_path / "world.sqlite")
    store.initialize()
    store.create_world(WorldState("storm", "Storm World"))
    store.save_world_workspace(
        channel_id="channel",
        world_id="storm",
        stage="review",
        brief="A city above a storm",
        settings={},
        sources={},
    )
    workspace = store.world_workspace("channel")
    message = IncomingMessage(
        event_id="generate",
        channel_id="channel",
        author_id="player",
        content="Generate",
        created_at=datetime.now(UTC),
    )
    calls = []
    draft = WorldDraft.model_validate(valid_world_payload())

    class Generator:
        async def generate(self, **kwargs):
            calls.append("generate")
            return draft

    port = Classifier(
        (0.5, 0.5) if case == "uncertain" else (0.99, 0.99),
        error=ClassifierRateLimitError("PRIVATE PROVIDER ERROR") if case == "error" else None,
    )

    class Observer:
        async def observe(self, value):
            assert store.world_generation_for_event(message.event_id) is not None
            assert store.world_content("storm")["premise"] == draft.premise
            calls.append("observe")
            if case == "cancel":
                raise asyncio.CancelledError()
            if case == "observer_error":
                raise RuntimeError("PRIVATE OBSERVER ERROR")
            return await adapter(port).observe(value)

    if case == "race_loser":
        commit = store.commit_world_generation

        def lose_race(**kwargs):
            assert commit(**kwargs) is True  # The competing worker won before this commit.
            return commit(**kwargs)

        monkeypatch.setattr(store, "commit_world_generation", lose_race)
    if case in {"off", "race_loser"}:

        def forbidden_capture(**kwargs):
            raise AssertionError("off or losing commit must not project")

        monkeypatch.setattr(
            "masterclaw.app.handlers.world_management.capture_public_world_semantics",
            forbidden_capture,
        )
    handler = SimpleNamespace(
        _store=store,
        _worldgen=Generator(),
        _locale=lambda _: "en",
        _world_semantic_observer=None if case == "off" else Observer(),
    )

    def run():
        return WorldManagementHandlers._handle_world_generation(
            handler, message=message, workspace=workspace
        )

    if case == "cancel":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run())
    else:
        result = asyncio.run(run())
        assert result == tr("en", "world_draft_generated")
    assert calls == ["generate"] + ([] if case in {"race_loser", "off"} else ["observe"])
    assert store.world_state("storm").revision == 1
    before = list(calls)
    assert asyncio.run(run()) == tr("en", "world_draft_generated")
    assert calls == before
    assert len(port.requests) == (
        0 if case in {"race_loser", "off", "cancel", "observer_error"} else 1
    )
    assert "PRIVATE PROVIDER ERROR" not in caplog.text
    assert "PRIVATE OBSERVER ERROR" not in caplog.text


def test_commit_failure_never_observes_or_projects(monkeypatch):
    world = WorldState("storm", "Storm World")
    calls = []

    def fail_commit(**kwargs):
        calls.append("commit")
        raise RuntimeError("world revision conflict")

    class Generator:
        async def generate(self, **kwargs):
            return WorldDraft.model_validate(valid_world_payload())

    class Observer:
        async def observe(self, value):
            raise AssertionError("failed commit must not observe")

    def fail_capture(**kwargs):
        raise AssertionError("failed commit must not project")

    monkeypatch.setattr(
        "masterclaw.app.handlers.world_management.capture_public_world_semantics", fail_capture
    )
    handler = SimpleNamespace(
        _store=SimpleNamespace(
            world_generation_for_event=lambda _: None,
            world_state=lambda _: world,
            commit_world_generation=fail_commit,
        ),
        _worldgen=Generator(),
        _locale=lambda _: "en",
        _world_semantic_observer=Observer(),
    )
    with pytest.raises(RuntimeError, match="world revision conflict"):
        asyncio.run(
            WorldManagementHandlers._handle_world_generation(
                handler,
                message=IncomingMessage(
                    event_id="failed",
                    channel_id="channel",
                    author_id="player",
                    content="generate",
                    created_at=datetime.now(UTC),
                ),
                workspace={
                    "world_id": "storm",
                    "revision": 1,
                    "settings": {},
                    "sources": {},
                    "brief": "A city",
                },
            )
        )
    assert calls == ["commit"]


def test_off_adapter_does_not_parse_or_call():
    port = Classifier()
    assert asyncio.run(adapter(port, mode="off").observe(None)) is None
    assert port.requests == []
