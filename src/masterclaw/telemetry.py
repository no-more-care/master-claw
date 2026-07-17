from __future__ import annotations

import inspect
import json
import logging
import re
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from typing import Protocol

logger = logging.getLogger(__name__)

_game_id: ContextVar[str | None] = ContextVar("masterclaw_game_id", default=None)
_trace: ContextVar[TraceContext | None] = ContextVar("masterclaw_trace", default=None)
_parent_span: ContextVar[str | None] = ContextVar("masterclaw_parent_span", default=None)
_recording_span: ContextVar[bool] = ContextVar("masterclaw_recording_span", default=False)

_SAFE_ERROR_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


class StageSink(Protocol):
    def record_stage_spans(self, records: list[dict[str, object]]) -> None: ...


@dataclass(slots=True)
class TraceContext:
    sink: StageSink
    trace_id: str
    channel_id: str | None
    event_id: str | None
    records: list[dict[str, object]]


@dataclass(frozen=True, slots=True)
class TraceBinding:
    trace_token: Token[TraceContext | None]
    parent_token: Token[str | None]


def sanitized_error_summary(caught: BaseException) -> str:
    """Return diagnostic metadata without persisting exception-controlled text."""

    parts = [type(caught).__name__]
    status_code = getattr(caught, "status_code", None)
    if isinstance(status_code, int):
        parts.append(f"status={status_code}")
    error_code = getattr(caught, "code", None)
    if isinstance(error_code, str) and _SAFE_ERROR_CODE.fullmatch(error_code):
        parts.append(f"code={error_code}")
    return " ".join(parts)


def bind_trace(
    sink: StageSink,
    *,
    trace_id: str,
    channel_id: str | None = None,
    event_id: str | None = None,
) -> TraceBinding:
    return TraceBinding(
        _trace.set(TraceContext(sink, trace_id, channel_id, event_id, [])),
        _parent_span.set(None),
    )


def reset_trace(binding: TraceBinding) -> None:
    trace = _trace.get()
    if trace is not None and trace.records:
        recording_token = _recording_span.set(True)
        try:
            trace.sink.record_stage_spans(trace.records)
        except Exception:
            logger.exception("Failed to persist performance trace %s", trace.trace_id)
        finally:
            _recording_span.reset(recording_token)
            trace.records.clear()
    _parent_span.reset(binding.parent_token)
    _trace.reset(binding.trace_token)


def current_trace_fields() -> dict[str, str | None]:
    trace = _trace.get()
    if trace is None:
        return {"trace_id": None, "channel_id": None, "event_id": None}
    return {
        "trace_id": trace.trace_id,
        "channel_id": trace.channel_id,
        "event_id": trace.event_id,
    }


@contextmanager
def stage_span(
    stage: str,
    *,
    component: str,
    operation: str | None = None,
    attributes: dict[str, object] | None = None,
):
    trace = _trace.get()
    if trace is None or _recording_span.get():
        yield
        return
    span_id = uuid.uuid4().hex
    parent_span_id = _parent_span.get()
    parent_token = _parent_span.set(span_id)
    started_at = datetime.now(UTC).isoformat()
    started = time.perf_counter_ns()
    status = "ok"
    error: str | None = None
    try:
        yield
    except Exception as caught:
        status = "error"
        error = sanitized_error_summary(caught)
        raise
    finally:
        duration_ms = (time.perf_counter_ns() - started) / 1_000_000
        _parent_span.reset(parent_token)
        record = {
            "span_id": span_id,
            "trace_id": trace.trace_id,
            "parent_span_id": parent_span_id,
            "game_id": current_game_id(),
            "channel_id": trace.channel_id,
            "event_id": trace.event_id,
            "stage": stage,
            "component": component,
            "operation": operation or stage,
            "status": status,
            "duration_ms": duration_ms,
            "attributes": attributes or {},
            "error": error,
            "started_at": started_at,
        }
        trace.records.append(record)
        logger.info(
            "stage_span %s",
            json.dumps(record, ensure_ascii=False, separators=(",", ":")),
        )


def traced_stage(stage: str, *, component: str):
    def decorate(function):
        if inspect.iscoroutinefunction(function):

            @wraps(function)
            async def async_wrapper(*args, **kwargs):
                with stage_span(stage, component=component, operation=function.__name__):
                    return await function(*args, **kwargs)

            return async_wrapper

        @wraps(function)
        def wrapper(*args, **kwargs):
            with stage_span(stage, component=component, operation=function.__name__):
                return function(*args, **kwargs)

        return wrapper

    return decorate


def current_game_id() -> str | None:
    return _game_id.get()


def bind_game(game_id: str | None) -> Token[str | None]:
    return _game_id.set(game_id)


def reset_game(token: Token[str | None]) -> None:
    _game_id.reset(token)
