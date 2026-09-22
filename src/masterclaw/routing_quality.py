from __future__ import annotations

import json
from collections import defaultdict

from masterclaw.storage.sqlite import SQLiteStore

_QUALITY_STAGES = (
    "pipeline.total",
    "pipeline.repair_completion",
    "fallback.primary",
    "fallback.secondary",
)


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def routing_quality_report(
    store: SQLiteStore,
    *,
    since_hours: int = 24 * 7,
    min_count: int = 2,
    limit: int = 50,
) -> dict[str, object]:
    """Aggregate replay-safe routing observations without calling a model."""

    if since_hours <= 0:
        raise ValueError("routing quality window must be positive")
    if min_count <= 0:
        raise ValueError("lexicon candidate minimum count must be positive")
    if limit <= 0:
        raise ValueError("routing quality limit must be positive")
    window = f"-{since_hours} hours"
    placeholders = ",".join("?" for _ in _QUALITY_STAGES)
    with store.connect() as connection:
        candidate_rows = connection.execute(
            """SELECT scenario, command, normalized_phrase,
                      COUNT(*) AS observations,
                      AVG(confidence) AS average_confidence,
                      MIN(created_at) AS first_seen,
                      MAX(created_at) AS last_seen
               FROM lexicon_candidates
               WHERE datetime(created_at) >= datetime('now', ?)
               GROUP BY scenario, command, normalized_phrase
               HAVING COUNT(*) >= ?
               ORDER BY observations DESC, last_seen DESC,
                        scenario, command, normalized_phrase
               LIMIT ?""",
            (window, min_count, limit),
        ).fetchall()
        stage_rows = connection.execute(
            f"""SELECT stage, operation, status, error
                FROM stage_spans
                WHERE datetime(started_at) >= datetime('now', ?)
                  AND stage IN ({placeholders})
                ORDER BY started_at, span_id""",
            (window, *_QUALITY_STAGES),
        ).fetchall()

    candidates = [
        {
            "scenario": str(row["scenario"]),
            "command": str(row["command"]),
            "normalized_phrase": str(row["normalized_phrase"]),
            "observations": int(row["observations"]),
            "average_confidence": round(float(row["average_confidence"]), 6),
            "first_seen": str(row["first_seen"]),
            "last_seen": str(row["last_seen"]),
        }
        for row in candidate_rows
    ]

    counters: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "pipeline_runs": 0,
            "repair_attempts": 0,
            "invalid_results": 0,
            "model_primary_attempts": 0,
            "model_fallback_attempts": 0,
        }
    )
    for row in stage_rows:
        operation = str(row["operation"])
        item = counters[operation]
        stage = str(row["stage"])
        if stage == "pipeline.total":
            item["pipeline_runs"] += 1
            if row["status"] == "error" and row["error"] == "PipelineValidationError":
                item["invalid_results"] += 1
        elif stage == "pipeline.repair_completion":
            item["repair_attempts"] += 1
        elif stage == "fallback.primary":
            item["model_primary_attempts"] += 1
        elif stage == "fallback.secondary":
            item["model_fallback_attempts"] += 1

    quality = []
    for operation, item in counters.items():
        quality.append(
            {
                "pipeline_contract": operation,
                **item,
                "repair_rate": _rate(
                    item["repair_attempts"],
                    item["pipeline_runs"],
                ),
                "invalid_result_rate": _rate(
                    item["invalid_results"],
                    item["pipeline_runs"],
                ),
                "model_fallback_rate": _rate(
                    item["model_fallback_attempts"],
                    item["model_primary_attempts"],
                ),
            }
        )
    quality.sort(
        key=lambda item: (
            -int(item["pipeline_runs"]),
            -int(item["model_primary_attempts"]),
            str(item["pipeline_contract"]),
        )
    )

    return {
        "filters": {
            "since_hours": since_hours,
            "min_count": min_count,
            "limit": limit,
        },
        "rate_definitions": {
            "repair_rate": (
                "schema-repair completions / bounded pipeline runs for the same "
                "typed output contract"
            ),
            "invalid_result_rate": (
                "bounded pipeline runs ending in PipelineValidationError / bounded "
                "pipeline runs for the same typed output contract; this does not infer "
                "whether a caller caught the error"
            ),
            "model_fallback_rate": (
                "secondary-model completion attempts / primary-model completion "
                "attempts for the same typed output contract"
            ),
        },
        "lexicon_candidates": candidates,
        "pipeline_quality": quality[:limit],
    }


def _percent(value: object) -> str:
    if value is None:
        return "-"
    return f"{float(value) * 100:.1f}%"


def render_routing_quality_report(report: dict[str, object], format_name: str) -> str:
    if format_name == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    if format_name != "table":
        raise ValueError("unsupported routing quality report format")

    lines = [
        "Lexicon candidates (manual review required):",
        f"{'count':>5} {'conf':>6} {'scenario':24} {'command':27} phrase",
        "-" * 106,
    ]
    candidates = report["lexicon_candidates"]
    if not candidates:
        lines.append("(none)")
    else:
        for item in candidates:
            phrase = str(item["normalized_phrase"])
            lines.append(
                f"{int(item['observations']):5d} "
                f"{float(item['average_confidence']):6.3f} "
                f"{str(item['scenario'])[:24]:24} "
                f"{str(item['command'])[:27]:27} "
                f"{phrase[:80]}"
            )

    lines.extend(
        (
            "",
            "Pipeline quality (denominators are intentionally separate):",
            f"{'typed output contract':34} {'runs':>6} {'repair':>8} "
            f"{'invalid':>8} {'primary':>8} {'model-fb':>9}",
            "-" * 91,
        )
    )
    quality = report["pipeline_quality"]
    if not quality:
        lines.append("(none)")
    else:
        for item in quality:
            lines.append(
                f"{str(item['pipeline_contract'])[:34]:34} "
                f"{int(item['pipeline_runs']):6d} "
                f"{_percent(item['repair_rate']):>8} "
                f"{_percent(item['invalid_result_rate']):>8} "
                f"{int(item['model_primary_attempts']):8d} "
                f"{_percent(item['model_fallback_rate']):>9}"
            )
    return "\n".join(lines)
