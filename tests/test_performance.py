import pytest

from masterclaw.performance import performance_report, render_report
from masterclaw.storage.sqlite import SQLiteStore
from masterclaw.telemetry import (
    bind_trace,
    reset_trace,
    sanitized_error_summary,
    stage_span,
)


def test_nested_stage_spans_are_persisted_and_reported(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    token = bind_trace(
        store,
        trace_id="message:test",
        channel_id="channel",
        event_id="event",
    )
    try:
        with stage_span("message.total", component="application"):
            with stage_span("context.assembly", component="context_assembler"):
                pass
            with stage_span("db.transaction", component="sqlite"):
                pass
    finally:
        reset_trace(token)

    report = performance_report(store, since_hours=1, group_by="stage")
    groups = {item["group"]: item for item in report["stage_summary"]}
    assert groups["message.total"]["calls"] == 1
    assert groups["context.assembly"]["errors"] == 0
    assert "p95_ms" in groups["db.transaction"]
    assert "stage/group" in render_report(report, "table")
    assert "message.total" in render_report(report, "csv")


def test_error_summary_does_not_persist_exception_text() -> None:
    class ProviderFailure(RuntimeError):
        status_code = 401
        code = "invalid_api_key"

    summary = sanitized_error_summary(
        ProviderFailure("Bearer sk-live-secret user supplied private text")
    )

    assert summary == "ProviderFailure status=401 code=invalid_api_key"
    assert "secret" not in summary
    assert "private text" not in summary


def test_stage_span_persists_only_sanitized_error(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    token = bind_trace(store, trace_id="message:error")
    try:
        with pytest.raises(RuntimeError, match="private payload"):
            with stage_span("message.total", component="application"):
                raise RuntimeError("private payload with sk-live-secret")
    finally:
        reset_trace(token)

    with store.connect() as connection:
        row = connection.execute(
            "SELECT status, error FROM stage_spans WHERE trace_id = ?",
            ("message:error",),
        ).fetchone()
    assert row is not None
    assert row["status"] == "error"
    assert row["error"] == "RuntimeError"
