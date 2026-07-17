from __future__ import annotations

import csv
import io
import json
import math
from collections import defaultdict

from masterclaw.storage.sqlite import SQLiteStore


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def performance_report(
    store: SQLiteStore,
    *,
    since_hours: int = 24,
    group_by: str = "stage",
    game_id: str | None = None,
    channel_id: str | None = None,
    stage_prefix: str | None = None,
    status: str | None = None,
    limit: int = 20,
) -> dict[str, object]:
    if group_by not in {"stage", "component", "operation", "status"}:
        raise ValueError("unsupported performance grouping")
    clauses = ["datetime(started_at) >= datetime('now', ?)"]
    parameters: list[object] = [f"-{since_hours} hours"]
    for column, value in (("game_id", game_id), ("channel_id", channel_id), ("status", status)):
        if value is not None:
            clauses.append(f"{column} = ?")
            parameters.append(value)
    if stage_prefix is not None:
        clauses.append("stage LIKE ?")
        parameters.append(stage_prefix + "%")
    where = " AND ".join(clauses)
    llm_clauses = ["datetime(created_at) >= datetime('now', ?)"]
    llm_parameters: list[object] = [f"-{since_hours} hours"]
    for column, value in (("game_id", game_id), ("channel_id", channel_id)):
        if value is not None:
            llm_clauses.append(f"{column} = ?")
            llm_parameters.append(value)
    if status is not None:
        llm_clauses.append("success = ?")
        llm_parameters.append(int(status == "ok"))
    llm_where = " AND ".join(llm_clauses)
    with store.connect() as connection:
        rows = connection.execute(
            f"""SELECT trace_id, stage, component, operation, status, duration_ms
                FROM stage_spans WHERE {where} ORDER BY started_at""",
            parameters,
        ).fetchall()
        llm_rows = connection.execute(
            f"""SELECT role, model, success, latency_ms, prompt_tokens, completion_tokens,
                      reasoning_tokens, cache_read_tokens, cost
               FROM llm_calls WHERE {llm_where}""",
            llm_parameters,
        ).fetchall()
    grouped: dict[str, list] = defaultdict(list)
    for row in rows:
        grouped[str(row[group_by])].append(row)
    stages = []
    for key, items in grouped.items():
        durations = [float(item["duration_ms"]) for item in items]
        stages.append(
            {
                "group": key,
                "calls": len(items),
                "errors": sum(item["status"] != "ok" for item in items),
                "total_ms": round(sum(durations), 3),
                "avg_ms": round(sum(durations) / len(durations), 3),
                "p50_ms": round(_percentile(durations, 0.50), 3),
                "p95_ms": round(_percentile(durations, 0.95), 3),
                "max_ms": round(max(durations), 3),
            }
        )
    stages.sort(key=lambda item: (-item["total_ms"], item["group"]))
    model_groups: dict[tuple[str, str], list] = defaultdict(list)
    for row in llm_rows:
        model_groups[(row["role"], row["model"])].append(row)
    models = []
    for (role, model), items in model_groups.items():
        models.append(
            {
                "role": role,
                "model": model,
                "calls": len(items),
                "errors": sum(not item["success"] for item in items),
                "latency_ms": sum(item["latency_ms"] for item in items),
                "prompt_tokens": sum(item["prompt_tokens"] for item in items),
                "completion_tokens": sum(item["completion_tokens"] for item in items),
                "reasoning_tokens": sum(item["reasoning_tokens"] for item in items),
                "cache_read_tokens": sum(item["cache_read_tokens"] for item in items),
                "cost": round(sum(item["cost"] for item in items), 8),
            }
        )
    models.sort(key=lambda item: (-item["cost"], -item["latency_ms"]))
    slow_messages = sorted(
        (
            {"trace_id": row["trace_id"], "duration_ms": round(row["duration_ms"], 3)}
            for row in rows
            if row["stage"] == "message.total"
        ),
        key=lambda item: -item["duration_ms"],
    )[:limit]
    return {
        "filters": {
            "since_hours": since_hours,
            "game_id": game_id,
            "channel_id": channel_id,
            "stage_prefix": stage_prefix,
            "status": status,
            "group_by": group_by,
        },
        "stage_summary": stages[:limit],
        "model_summary": models[:limit],
        "slow_messages": slow_messages,
    }


def render_report(report: dict[str, object], format_name: str) -> str:
    if format_name == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    stages = report["stage_summary"]
    if format_name == "csv":
        output = io.StringIO()
        columns = ("group", "calls", "errors", "total_ms", "avg_ms", "p50_ms", "p95_ms", "max_ms")
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        writer.writerows(stages)
        return output.getvalue().rstrip()
    lines = [
        f"{'stage/group':36} {'calls':>7} {'err':>5} {'total':>11} "
        f"{'avg':>9} {'p50':>9} {'p95':>9} {'max':>9}",
        "-" * 101,
    ]
    for item in stages:
        lines.append(
            f"{item['group'][:36]:36} {item['calls']:7d} {item['errors']:5d} "
            f"{item['total_ms']:11.3f} {item['avg_ms']:9.3f} {item['p50_ms']:9.3f} "
            f"{item['p95_ms']:9.3f} {item['max_ms']:9.3f}"
        )
    models = report["model_summary"]
    if models:
        lines.extend(("", "LLM usage:", "-" * 101))
        for item in models:
            lines.append(
                f"{item['role']:10} {item['model'][:42]:42} calls={item['calls']:3d} "
                f"latency={item['latency_ms']:7d}ms tokens="
                f"{item['prompt_tokens']}/{item['completion_tokens']} cost=${item['cost']:.6f}"
            )
    return "\n".join(lines)
