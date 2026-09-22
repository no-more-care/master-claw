import copy
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from masterclaw.classifier_calibration import (
    ClassifierSpan,
    SQLiteClassifierSpanSource,
    classifier_calibration_report,
    render_classifier_calibration_report,
)
from masterclaw.classifiers.observations import ClassifierObservation
from masterclaw.cli import main
from masterclaw.storage.sqlite import SQLiteStore


def choice(label="show_status", probability=0.96):
    return {
        "type": "choice",
        "outcome": "eligible",
        "choice": label,
        "confidence": 0.98,
        "probabilities": {label: probability, "answer_pending": 1 - probability},
    }


def noul(probability):
    return {
        "type": "noul",
        "outcome": "eligible",
        "noul": probability,
        "probabilities": {"true": probability, "false": 1 - probability},
    }


def attrs(use_case="state_dispatch", **updates):
    return ClassifierObservation.model_validate(
        {
            "use_case": use_case,
            "mode": "shadow",
            "scope": "active_scene",
            "taxonomy_version": f"{use_case}.v1",
            "requested_model": "alias-latest",
            "resolved_model": "model-2026",
            "provider": "test-provider",
            "version": "1.0",
            "request_id": "private-request",
            "request_key": "private-fingerprint",
            "outcome": "eligible",
            "answers": {"command": choice()},
            "latency_ms": 1,
            **updates,
        }
    ).model_dump(mode="json")


class Source:
    def __init__(self, records):
        self.records = records

    def classifier_spans(self, *, since_hours):
        assert since_hours > 0
        yield from self.records


def span(attributes, stage=None):
    return ClassifierSpan(
        stage or f"classifier.{attributes.get('use_case', 'legacy')}", json.dumps(attributes)
    )


def report(*records, **filters):
    return classifier_calibration_report(Source([span(row) for row in records]), **filters)


def mixed():
    score = {
        "type": "score",
        "outcome": "eligible",
        "score": 0.75,
        "confidence": 0.8,
        "probabilities": {"0": 0.25, "1": 0.75},
    }
    return [
        attrs(
            reference={"command": "show_status"},
            agreement=False,
            cost=0.1,
            usage={
                "input_tokens": 100,
                "prompt_tokens": 999,
                "output_tokens": 20,
                "completion_tokens": 888,
                "total_tokens": 120,
                "cost": 9,
                "input_tokens_details": {"cached_tokens": 40},
            },
        ),
        attrs(
            agreement=False,
            latency_ms=2,
            usage={"prompt_tokens": 4, "completion_tokens": 6, "cost": 0.2},
        ),
        attrs(
            outcome="blocked", reference={"command": "answer_pending"}, agreement=True, latency_ms=3
        ),
        attrs(
            "advancement",
            taxonomy_version="advancement_safety.raise.v1",
            scope=None,
            latency_ms=4,
            answers={"scene_safe_enough": noul(0.99), "downtime_available": noul(0.99)},
            decision="allow",
            decision_reference="deny",
        ),
        attrs(
            "action_capability",
            scope=None,
            latency_ms=5,
            answers={"feasibility": choice("possible")},
            decision="capable",
            decision_comparison="proceed",
            decision_reference="proceed",
        ),
        attrs(
            "player_narration_rights",
            scope=None,
            latency_ms=6,
            answers={"actor_only": noul(0.01), "publicly_supported": noul(0.99)},
            decision="deny",
            decision_reference="deny",
        ),
        attrs(
            "rubric",
            scope=None,
            latency_ms=7,
            taxonomy_version="custom_rubric.v7",
            answers={"quality": score},
            reference={"quality": 0.75},
        ),
        attrs(
            "rubric",
            scope=None,
            latency_ms=8,
            taxonomy_version="custom_rubric.v7",
            answers={"quality": score},
            outcome="uncertain",
            agreement=True,
        ),
        attrs(
            outcome="error",
            answers={},
            error_category="rate_limit",
            error_transient=True,
            latency_ms=9,
        ),
        attrs(
            outcome="error",
            answers={},
            error_category="configuration",
            error_transient=False,
            latency_ms=10,
            cost=0.3,
        ),
    ]


def test_mixed_primitives_explicit_reference_denominators_cost_and_tokens_once():
    result = report(*mixed())
    summary = result["summary"]
    assert result["rows"] == {
        "scanned": 10,
        "valid": 10,
        "legacy": 0,
        "malformed": 0,
        "unknown_version": 0,
        "filtered_out": 0,
    }
    assert summary["total"] == 10
    assert summary["outcomes"] == {
        "eligible": 6,
        "uncertain": 1,
        "blocked": 1,
        "error": 2,
        "off": 0,
    }
    assert summary["outcome_rates"]["error"] == 0.2
    assert summary["comparable_rows"] == 6 and summary["agreements"] == 4
    assert (
        summary["agreement_rate"] == 0.666667
    )  # Missing refs not disagreements; stored bool ignored.
    assert summary["latency_ms"] == {"p50": 5, "p95": 10}
    assert summary["cost"] == {"sum": 0.6, "observed_rows": 3, "missing_rows": 7}
    assert summary["tokens"] == {
        "input": {"sum": 104, "observed_rows": 2},
        "output": {"sum": 26, "observed_rows": 2},
        "total": {"sum": 130, "observed_rows": 2},
    }
    assert summary["errors"] == [
        {"category": "configuration", "transient": False, "count": 1},
        {"category": "rate_limit", "transient": True, "count": 1},
    ]
    assert "questions" not in summary  # No mixed taxonomy scales at the top level.
    groups = {group["use_case"]: group for group in result["groups"]}
    command = groups["state_dispatch"]["questions"][0]
    assert command["comparable"] == 2 and command["agreements"] == 1
    assert command["selected_probability_buckets"][-1]["count"] == 3
    assert command["confidence_buckets"][-1]["comparable"] == 2
    assert groups["action_capability"]["decision_confusion"] == [
        {"predicted": "proceed", "reference": "proceed", "count": 1}
    ]
    question = groups["player_narration_rights"]["questions"][0]
    assert question["comparable"] == 0 and question["agreement_rate"] is None
    assert "p_true_buckets" in question and "confidence_buckets" not in question
    score = groups["rubric"]["questions"][0]
    assert score["comparable"] == 1 and score["agreement_rate"] == 1
    assert score["score"] == {"min": 0.75, "max": 0.75, "mean": 0.75}
    assert "confidence_buckets" in score and "selected_probability_buckets" not in score


def test_noul_probability_is_not_confidence_and_question_refs_are_type_checked():
    rows = [
        attrs("signal", answers={"safe": noul(0.1)}, reference={"safe": False}),
        attrs("signal", answers={"safe": noul(0.9)}, reference={"safe": "false"}),
        attrs("signal", answers={"safe": noul(0.5)}, reference={"safe": True}),
        attrs("signal", answers={"safe": noul(0.8)}, reference={"missing": True}),
    ]
    result = report(*rows)
    assert result["summary"]["comparable_rows"] == 2
    assert result["summary"]["agreements"] == 2
    question = result["groups"][0]["questions"][0]
    assert question["p_true_buckets"][0]["count"] == 1
    assert "confidence_buckets" not in question
    assert question["confusion"] == [
        {"predicted": False, "reference": False, "count": 1},
        {"predicted": True, "reference": True, "count": 1},
    ]


def test_legacy_unknown_and_malformed_are_excluded_even_if_shape_looks_current():
    inferred = attrs()
    inferred.pop("observation_schema_version")
    malformed = attrs()
    malformed["answers"]["command"]["probabilities"]["show_status"] = 0.1
    rows = [
        span(attrs()),
        span(inferred),
        span({"baseline_command": "show_status"}),
        span({**attrs(), "observation_schema_version": "v2"}),
        span(malformed),
        ClassifierSpan("classifier.state_dispatch", "invalid-json"),
        span({**attrs(), "raw_state": "private-message"}),
        span(attrs(), "classifier.wrong_stage"),
        span(attrs(taxonomy_version="unversioned")),
    ]
    result = classifier_calibration_report(Source(rows))
    assert result["rows"] == {
        "scanned": 9,
        "valid": 1,
        "legacy": 2,
        "unknown_version": 1,
        "malformed": 5,
        "filtered_out": 0,
    }
    assert result["summary"]["total"] == 1
    assert result["summary"]["agreement_rate"] is None
    assert "private-message" not in render_classifier_calibration_report(result, "json")


@pytest.mark.parametrize(
    "update",
    [
        {"latency_ms": float("nan")},
        {"cost": float("inf")},
        {"cost": -1},
        {"requested_model": "provider error contains secret prose"},
        {"provider": "https://user:secret@example.com"},
        {"scope": "123456789012345678"},
        {"decision": "private-request"},
    ],
)
def test_malformed_numeric_and_private_metadata_never_leaks(update):
    row = {**attrs(), **update}
    result = report(row)
    assert result["rows"]["malformed"] == 1
    assert result["groups"] == []
    wire = render_classifier_calibration_report(result, "json")
    assert "secret" not in wire and "123456789012345678" not in wire
    assert "private-request" not in wire


def test_usage_extra_fields_ids_and_raw_span_columns_never_reach_output(tmp_path):
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    row = attrs(
        usage={
            "user_id": "usage-private",
            "prompt": "raw-context",
            "cost_details": {"secret": "nested-private"},
        }
    )
    store.record_stage_span(
        span_id="private-span",
        trace_id="private-trace",
        parent_span_id="private-parent",
        game_id="private-game",
        channel_id="private-channel",
        event_id="private-event",
        stage="classifier.state_dispatch",
        component="classifier",
        operation="secret-operation",
        status="ok",
        duration_ms=1,
        attributes=row,
        error="raw-provider-error",
        started_at=datetime.now(UTC).isoformat(),
    )
    result = classifier_calibration_report(SQLiteClassifierSpanSource(store.path))
    assert result["rows"]["valid"] == 1
    for format_name in ("json", "table"):
        rendered = render_classifier_calibration_report(result, format_name)
        for excluded in (
            "private",
            "raw-context",
            "raw-provider-error",
            "secret-operation",
            "request_id",
            "request_key",
            "trace_id",
            "event_id",
            "channel_id",
            "player_id",
            "game_id",
            "user_id",
        ):
            assert excluded not in rendered


def test_bad_answer_shapes_are_malformed_not_report_crashes():
    answers = [
        {
            "type": "choice",
            "choice": "missing",
            "confidence": 0.9,
            "outcome": "eligible",
            "probabilities": {"yes": 0.8, "no": 0.2},
        },
        {
            "type": "noul",
            "noul": 0.9,
            "outcome": "eligible",
            "probabilities": {"true": 0.1, "false": 0.9},
        },
        {
            "type": "score",
            "score": 0.5,
            "confidence": 0.9,
            "outcome": "eligible",
            "probabilities": {"secret-label": 0.5, "1": 0.5},
        },
        {
            "type": "score",
            "score": 0.9,
            "confidence": 0.9,
            "outcome": "eligible",
            "probabilities": {"0": 0.5, "1": 0.5},
        },
    ]
    result = report(*[{**attrs(), "answers": {"question": answer}} for answer in answers])
    assert result["rows"]["malformed"] == 4
    assert "secret-label" not in render_classifier_calibration_report(result, "json")


def test_limit_and_filters_keep_totals_and_stable_order():
    rows = mixed()
    result = report(*rows, limit=1)
    assert result["summary"]["total"] == 10
    assert result["group_count"] == 5 and result["omitted_groups"] == 4
    assert result["groups"][0]["use_case"] == "state_dispatch"
    assert result == report(*reversed(rows), limit=1)
    scoped = report(*rows, use_case="state_dispatch", scope="active_scene")
    assert scoped["summary"]["total"] == 5 and scoped["rows"]["filtered_out"] == 5
    assert render_classifier_calibration_report(
        result, "json"
    ) == render_classifier_calibration_report(result, "json")
    assert render_classifier_calibration_report(
        result, "table"
    ) == render_classifier_calibration_report(report(*reversed(rows), limit=1), "table")


@pytest.mark.parametrize(
    ("probability", "distribution", "valid"),
    [
        (0.0, {"false": 1.0}, False),
        (1.0, {"true": 1.0}, False),
        (0.5, {"true": 0.5, "false": 0.4, "extra": 0.1}, False),
        (0.5, {"true": 0.5, "false": 0.505}, True),
        (0.5, {"true": 0.5, "false": 0.495}, True),
        (0.5, {"true": 0.5, "false": 0.511}, False),
        (0.5, {"true": 0.5, "false": 0.489}, False),
        (0.0, {"true": 0.0, "false": 1.0}, True),
        (1.0, {"true": 1.0, "false": 0.0}, True),
        (0.0, {"true": -0.001, "false": 1.0}, False),
        (1.0, {"true": 1.001, "false": 0.0}, False),
        (0.5, {"true": float("nan"), "false": 0.5}, False),
        (0.5, {"true": 0.5, "false": float("inf")}, False),
    ],
)
def test_noul_exact_keys_probability_bounds_and_distribution_tolerance(
    probability, distribution, valid
):
    row = attrs()
    row["answers"] = {
        "safe": {
            "type": "noul",
            "outcome": "eligible",
            "noul": probability,
            "probabilities": distribution,
        },
    }
    row["reference"] = {"safe": True}
    result = report(row)
    assert result["rows"]["valid"] == int(valid)
    assert result["rows"]["malformed"] == int(not valid)
    assert result["summary"]["comparable_rows"] == int(valid)
    if not valid:
        assert result["groups"] == []
        assert result["summary"]["agreement_rate"] is None


def test_empty_database_missing_path_and_read_only_cli(tmp_path, monkeypatch, capsys):
    import masterclaw.cli as cli

    database = tmp_path / "empty.sqlite3"
    sqlite3.connect(database).close()
    before = database.read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "report must not initialize, configure, call providers or mutate gameplay"
        )

    monkeypatch.setattr(SQLiteStore, "initialize", forbidden)
    monkeypatch.setattr(SQLiteStore, "connect", forbidden)
    monkeypatch.setattr(cli, "Settings", forbidden)
    monkeypatch.setattr(cli, "create_semantic_classifier", forbidden)
    monkeypatch.setattr(cli, "OpenHandsLLMRegistry", forbidden)
    assert (
        main(["classifier-calibration-report", "--database", str(database), "--format", "json"])
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["summary"]["total"] == 0 and result["summary"]["agreement_rate"] is None
    assert result["summary"]["latency_ms"] == {"p50": None, "p95": None}
    assert result["groups"] == [] and database.read_bytes() == before
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(SystemExit, match="unreadable database"):
        main(["classifier-calibration-report", "--database", str(missing)])
    assert not missing.exists()


def test_since_hours_query_filters_classifier_spans_without_database_changes(tmp_path):
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    now = datetime.now(UTC)
    for index, (stage, timestamp) in enumerate(
        [
            ("classifier.state_dispatch", now),
            ("classifier.state_dispatch", now - timedelta(hours=4)),
            ("pipeline.total", now),
        ]
    ):
        store.record_stage_span(
            span_id=f"span-{index}",
            trace_id="trace",
            stage=stage,
            component="classifier",
            operation="observe",
            status="ok",
            duration_ms=1,
            attributes=attrs(),
            started_at=timestamp.isoformat(),
        )
    before = store.path.read_bytes()
    with sqlite3.connect(store.path) as connection:
        schema = connection.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    result = classifier_calibration_report(SQLiteClassifierSpanSource(store.path), since_hours=2)
    assert result["summary"]["total"] == 1
    assert store.path.read_bytes() == before
    with sqlite3.connect(store.path) as connection:
        assert (
            connection.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall() == schema
        )


@pytest.mark.parametrize(
    "filters",
    [{"since_hours": 0}, {"limit": 0}, {"scope": "raw prose"}, {"use_case": "123456789012345678"}],
)
def test_invalid_filters_fail_before_query(filters):
    class Forbidden:
        def classifier_spans(self, **kwargs):
            raise AssertionError("invalid filter must not query")

    with pytest.raises(ValueError):
        classifier_calibration_report(Forbidden(), **filters)


def test_does_not_mutate_source_observations_and_huge_usage_is_ignored():
    original = attrs(
        usage={"input_tokens": 10**500, "output_tokens": -1, "total_tokens": "private"}
    )
    saved = copy.deepcopy(original)
    result = report(original)
    assert result["rows"]["valid"] == 1
    assert result["summary"]["tokens"]["input"] == {"sum": 0, "observed_rows": 0}
    assert original == saved


def test_off_and_errors_never_contribute_reference_agreement():
    result = report(
        attrs(outcome="off", reference={"command": "show_status"}, agreement=True),
        attrs(outcome="error", reference={"command": "show_status"}, agreement=True),
    )
    assert result["summary"]["outcomes"]["off"] == 1
    assert result["summary"]["outcomes"]["error"] == 1
    assert result["summary"]["comparable_rows"] == 0
    assert result["groups"][0]["questions"] == []


def test_extreme_finite_costs_do_not_break_json_render():
    result = report(attrs(cost=1e308), attrs(cost=1e308))
    assert result["summary"]["cost"]["sum"] is None
    assert result["summary"]["cost"]["observed_rows"] == 2
    json.loads(render_classifier_calibration_report(result, "json"))


def test_future_executor_records_explicit_version_accepted_by_reporter():
    from test_semantic_executor import evaluate

    from masterclaw.telemetry import bind_trace, reset_trace

    records = []

    class Sink:
        def record_stage_spans(self, values):
            records.extend(values)

    token = bind_trace(Sink(), trace_id="private-trace")
    try:
        evaluation = evaluate()
    finally:
        reset_trace(token)
    attributes = records[0]["attributes"]
    assert attributes["observation_schema_version"] == "v1"
    assert evaluation.observation.observation_schema_version == "v1"
    result = report(attributes)
    assert result["rows"]["valid"] == 1
    assert {question["type"] for question in result["groups"][0]["questions"]} == {
        "choice",
        "noul",
        "score",
    }
