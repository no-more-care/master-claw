"""Read-only, privacy-allowlisted calibration aggregates from durable classifier spans."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Protocol

from masterclaw.classifiers.base import ChoiceAnswer, NoulAnswer
from masterclaw.classifiers.observations import (
    AnswerObservation,
    ClassifierObservation,
    metadata_identifier,
)

_LABEL = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_PRIVATE = re.compile(
    r"(?:\d{17,}|[a-fA-F0-9]{32,}|[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12})"
)
_DIMENSIONS = (
    "use_case",
    "scope",
    "mode",
    "taxonomy_version",
    "requested_model",
    "resolved_model",
    "provider",
    "upstream_provider",
    "version",
)
_OUTCOMES = ("eligible", "uncertain", "blocked", "error", "off")
_BUCKETS = (
    (0.5, "[0,.5)"),
    (0.8, "[.5,.8)"),
    (0.9, "[.8,.9)"),
    (0.95, "[.9,.95)"),
    (1.01, "[.95,1]"),
)


@dataclass(frozen=True, slots=True)
class ClassifierSpan:
    stage: str
    attributes_json: str


class ClassifierSpanSource(Protocol):
    def classifier_spans(self, *, since_hours: int) -> Iterable[ClassifierSpan]: ...


class SQLiteClassifierSpanSource:
    """Never uses SQLiteStore.connect/initialize (those may create or migrate a database)."""

    def __init__(self, database: str | Path) -> None:
        self.path = Path(database)

    def classifier_spans(self, *, since_hours: int) -> Iterable[ClassifierSpan]:
        if not self.path.is_file():
            raise ValueError("classifier calibration database does not exist")
        with closing(
            sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as connection:
            if not connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stage_spans'"
            ).fetchone():
                return
            # No IDs, raw span errors, gameplay rows or LLM usage table are selected.
            for stage, attributes in connection.execute(
                """SELECT stage, attributes_json FROM stage_spans
                   WHERE stage LIKE 'classifier.%'
                     AND datetime(started_at) >= datetime('now', ?)
                   ORDER BY started_at, span_id""",
                (f"-{since_hours} hours",),
            ):
                yield ClassifierSpan(stage, attributes)


def _label(value: str) -> bool:
    return bool(_LABEL.fullmatch(value)) and not _PRIVATE.search(value)


def _finite_nonnegative(value: object) -> bool:
    try:
        return type(value) in {int, float} and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _validate(observation: ClassifierObservation, stage: str) -> None:
    """Strengthen the storage shape with semantic and safe-aggregate-value checks."""
    if stage != f"classifier.{observation.use_case}":
        raise ValueError("stage mismatch")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*\.v[1-9][0-9]*", observation.taxonomy_version):
        raise ValueError("unversioned taxonomy")
    private_values = {value for value in (observation.request_id, observation.request_key) if value}
    labels = [
        observation.use_case,
        observation.scope,
        observation.taxonomy_version,
        observation.decision,
        observation.decision_reference,
        observation.decision_comparison,
        observation.decision_reason,
        *observation.answers,
        *observation.reference,
        *observation.decision_thresholds,
    ]
    for answer in observation.answers.values():
        labels.extend([answer.choice] if answer.type == "choice" else [])
        if answer.type == "choice":
            labels.extend(answer.probabilities)
    labels.extend(value for value in observation.reference.values() if isinstance(value, str))
    if any(
        value is not None and (not _label(value) or value in private_values) for value in labels
    ):
        raise ValueError("unsafe aggregate label")
    for name in ("requested_model", "resolved_model", "provider", "upstream_provider", "version"):
        value = getattr(observation, name)
        if value is not None and (
            metadata_identifier(value) is None
            or _PRIVATE.search(value)
            or value in private_values
            or "://" in value
            or value.startswith(("sk-", "ghp_", "github_pat_"))
        ):
            raise ValueError("unsafe aggregate metadata")
    if not _finite_nonnegative(observation.latency_ms) or (
        observation.cost is not None and not _finite_nonnegative(observation.cost)
    ):
        raise ValueError("invalid numeric metadata")
    if observation.outcome not in {"error", "off"} and not observation.answers:
        raise ValueError("missing answers")
    for answer in observation.answers.values():
        probabilities = answer.probabilities
        if not probabilities or not math.isclose(sum(probabilities.values()), 1, abs_tol=0.01):
            raise ValueError("invalid distribution")
        if answer.type == "choice":
            ChoiceAnswer.model_validate(
                {
                    "type": "choice",
                    "choice": answer.choice,
                    "probabilities": probabilities,
                    "confidence": answer.confidence,
                }
            )
            if (
                answer.score is not None
                or answer.noul is not None
                or answer.choice not in probabilities
            ):
                raise ValueError("invalid choice")
            if probabilities[answer.choice] < max(probabilities.values()) - 1e-6:
                raise ValueError("invalid choice maximum")
        elif answer.type == "noul":
            if set(probabilities) != {"true", "false"}:
                raise ValueError("invalid noul probability keys")
            NoulAnswer.model_validate(
                {"type": "noul", "noul": answer.noul, "probabilities": probabilities}
            )
            if (
                answer.choice is not None
                or answer.score is not None
                or not math.isclose(probabilities["true"], answer.noul, abs_tol=1e-6)
            ):
                raise ValueError("invalid noul")
        else:
            positions = [float(key) for key in probabilities]
            if (
                answer.score is None
                or answer.confidence is None
                or answer.choice is not None
                or answer.noul is not None
                or len(positions) < 2
                or not all(math.isfinite(value) for value in positions)
                or len(set(positions)) != len(positions)
                or not min(positions) <= answer.score <= max(positions)
                or not math.isclose(
                    answer.score,
                    sum(float(key) * value for key, value in probabilities.items()),
                    abs_tol=0.02,
                )
            ):
                raise ValueError("invalid score")


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _percentile(values: list[float], fraction: float) -> float | None:
    return (
        round(sorted(values)[max(0, math.ceil(fraction * len(values)) - 1)], 3) if values else None
    )


def _cost_sum(values: list[float]) -> float | None:
    try:
        return round(math.fsum(values), 8)
    except OverflowError:
        return None


def _comparison(answer: AnswerObservation, reference: object) -> tuple[object, object, bool] | None:
    if answer.type == "choice" and isinstance(reference, str) and reference in answer.probabilities:
        return answer.choice, reference, answer.choice == reference
    if answer.type == "noul" and type(reference) is bool:
        predicted = answer.noul >= 0.5
        return predicted, reference, predicted == reference
    if answer.type == "score" and type(reference) in {int, float} and math.isfinite(reference):
        if (
            min(map(float, answer.probabilities))
            <= reference
            <= max(map(float, answer.probabilities))
        ):
            return answer.score, reference, math.isclose(answer.score, reference, abs_tol=1e-6)
    return None


def _matrix(comparisons: list[tuple[object, object, bool]]) -> list[dict[str, object]]:
    counts = Counter(
        (json.dumps(predicted), json.dumps(reference)) for predicted, reference, _ in comparisons
    )
    return [
        {"predicted": json.loads(predicted), "reference": json.loads(reference), "count": count}
        for (predicted, reference), count in sorted(counts.items())
    ]


def _buckets(values: list[tuple[float, bool | None]]) -> list[dict[str, object]]:
    result = []
    lower = 0.0
    for upper, label in _BUCKETS:
        selected = [agreement for value, agreement in values if lower <= value < upper]
        comparable = [agreement for agreement in selected if agreement is not None]
        result.append(
            {
                "bucket": label,
                "count": len(selected),
                "comparable": len(comparable),
                "agreements": sum(comparable),
                "agreement_rate": _rate(sum(comparable), len(comparable)),
            }
        )
        lower = upper
    return result


def _token(usage: dict, *keys: str) -> int | None:
    for key in keys:
        value = usage.get(key)
        if _finite_nonnegative(value) and float(value).is_integer():
            return int(value)
    return None


def _summarize(observations: list[ClassifierObservation]) -> dict[str, object]:
    outcomes = Counter(item.outcome for item in observations)
    row_agreements = []
    decisions = []
    question_rows = defaultdict(list)
    costs = []
    tokens = defaultdict(list)
    errors = Counter()
    for item in observations:
        cost = item.cost if item.cost is not None else item.usage.get("cost")
        if _finite_nonnegative(cost):
            costs.append(cost)
        inputs = _token(item.usage, "input_tokens", "prompt_tokens")
        outputs = _token(item.usage, "output_tokens", "completion_tokens")
        total = _token(item.usage, "total_tokens")
        if total is None and inputs is not None and outputs is not None:
            total = inputs + outputs
        for name, value in (("input", inputs), ("output", outputs), ("total", total)):
            if value is not None:
                tokens[name].append(value)
        if item.outcome == "error":
            errors[
                (
                    item.error_category.value if item.error_category else "unknown",
                    item.error_transient,
                )
            ] += 1
        if item.outcome in {"error", "off"}:
            continue
        comparisons = []
        for key, answer in item.answers.items():
            comparison = _comparison(answer, item.reference.get(key))
            question_rows[(key, answer.type)].append((answer, comparison))
            if comparison is not None:
                comparisons.append(comparison)
        if item.decision is not None and item.decision_reference is not None:
            predicted = item.decision_comparison or item.decision
            agreement = predicted == item.decision_reference
            decisions.append((predicted, item.decision_reference, agreement))
            row_agreements.append(agreement)
        elif comparisons:
            row_agreements.append(all(comparison[2] for comparison in comparisons))
    questions = []
    for (key, answer_type), rows in sorted(question_rows.items()):
        comparisons = [comparison for _, comparison in rows if comparison is not None]
        metrics = {
            "question": key,
            "type": answer_type,
            "count": len(rows),
            "comparable": len(comparisons),
            "agreements": sum(value[2] for value in comparisons),
            "agreement_rate": _rate(sum(value[2] for value in comparisons), len(comparisons)),
            "confusion": _matrix(comparisons),
        }
        if answer_type == "noul":
            metrics["p_true_buckets"] = _buckets(
                [
                    (answer.noul, comparison[2] if comparison else None)
                    for answer, comparison in rows
                ]
            )
        else:
            metrics["confidence_buckets"] = _buckets(
                [
                    (answer.confidence, comparison[2] if comparison else None)
                    for answer, comparison in rows
                ]
            )
            if answer_type == "choice":
                metrics["selected_probability_buckets"] = _buckets(
                    [
                        (answer.probabilities[answer.choice], comparison[2] if comparison else None)
                        for answer, comparison in rows
                    ]
                )
            else:
                scores = [answer.score for answer, _ in rows]
                metrics["score"] = {
                    "min": min(scores),
                    "max": max(scores),
                    "mean": round(mean(scores), 6),
                }
        questions.append(metrics)
    return {
        "total": len(observations),
        "outcomes": {key: outcomes[key] for key in _OUTCOMES},
        "outcome_rates": {key: _rate(outcomes[key], len(observations)) for key in _OUTCOMES},
        "errors": [
            {"category": category, "transient": transient, "count": count}
            for (category, transient), count in sorted(
                errors.items(), key=lambda value: (value[0][0], str(value[0][1]))
            )
        ],
        "latency_ms": {
            "p50": _percentile([item.latency_ms for item in observations], 0.5),
            "p95": _percentile([item.latency_ms for item in observations], 0.95),
        },
        "cost": {
            "sum": _cost_sum(costs),
            "observed_rows": len(costs),
            "missing_rows": len(observations) - len(costs),
        },
        "tokens": {
            name: {"sum": sum(tokens[name]), "observed_rows": len(tokens[name])}
            for name in ("input", "output", "total")
        },
        "comparable_rows": len(row_agreements),
        "agreements": sum(row_agreements),
        "agreement_rate": _rate(sum(row_agreements), len(row_agreements)),
        "decision_confusion": _matrix(decisions),
        "questions": questions,
    }


def classifier_calibration_report(
    source: ClassifierSpanSource,
    *,
    since_hours: int = 168,
    use_case: str | None = None,
    scope: str | None = None,
    limit: int = 50,
) -> dict[str, object]:
    if since_hours <= 0 or limit <= 0:
        raise ValueError("classifier calibration window and limit must be positive")
    if any(value is not None and not _label(value) for value in (use_case, scope)):
        raise ValueError("invalid classifier calibration filter")
    counts = Counter()
    observations = []
    groups = defaultdict(list)
    for span in source.classifier_spans(since_hours=since_hours):
        counts["scanned"] += 1
        try:
            if len(span.attributes_json) > 256_000:
                raise ValueError("oversized attributes")
            attributes = json.loads(span.attributes_json)
            if not isinstance(attributes, dict):
                raise ValueError("invalid attributes")
        except (ValueError, TypeError, RecursionError):
            counts["malformed"] += 1
            continue
        if any(
            value is not None and attributes.get(key) != value
            for key, value in (("use_case", use_case), ("scope", scope))
        ):
            counts["filtered_out"] += 1
            continue
        if "observation_schema_version" not in attributes:
            counts["legacy"] += 1
            continue
        if attributes["observation_schema_version"] != "v1":
            counts["unknown_version"] += 1
            continue
        try:
            observation = ClassifierObservation.model_validate(attributes)
            _validate(observation, span.stage)
        except (ValueError, TypeError, KeyError, OverflowError):
            counts["malformed"] += 1
            continue
        counts["valid"] += 1
        observations.append(observation)
        groups[tuple(getattr(observation, name) for name in _DIMENSIONS)].append(observation)
    ordered = sorted(
        groups.items(), key=lambda value: (-len(value[1]), tuple(item or "" for item in value[0]))
    )
    summary = _summarize(observations)
    # Never combine question scales or label matrices across distinct taxonomies/use cases.
    summary.pop("questions")
    summary.pop("decision_confusion")
    return {
        "report_version": "classifier_calibration.v1",
        "filters": {
            "since_hours": since_hours,
            "use_case": use_case,
            "scope": scope,
            "limit": limit,
        },
        "rows": {
            name: counts[name]
            for name in (
                "scanned",
                "filtered_out",
                "valid",
                "legacy",
                "malformed",
                "unknown_version",
            )
        },
        "summary": summary,
        "group_count": len(groups),
        "omitted_groups": max(0, len(groups) - limit),
        "groups": [
            {**dict(zip(_DIMENSIONS, key, strict=True)), **_summarize(items)}
            for key, items in ordered[:limit]
        ],
    }


def render_classifier_calibration_report(report: dict[str, object], format_name: str) -> str:
    if format_name == "json":
        return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
    if format_name != "table":
        raise ValueError("unsupported classifier calibration format")
    lines = [
        "Classifier calibration (shadow evidence, not authority)",
        "Rows: " + " ".join(f"{key}={value}" for key, value in report["rows"].items()),
        "Summary: " + json.dumps(report["summary"], sort_keys=True),
        " | ".join(_DIMENSIONS),
    ]
    for group in report["groups"]:
        lines.append(" | ".join(str(group[name] or "-") for name in _DIMENSIONS))
        outcomes = " ".join(f"{key}={value}" for key, value in group["outcomes"].items())
        lines.append(
            f"  total={group['total']} {outcomes} "
            f"agreement={group['agreements']}/{group['comparable_rows']} "
            f"rate={group['agreement_rate']}"
        )
        lines.append(
            f"  latency_ms={json.dumps(group['latency_ms'], sort_keys=True)} "
            f"cost={json.dumps(group['cost'], sort_keys=True)} "
            f"tokens={json.dumps(group['tokens'], sort_keys=True)}"
        )
        for name in ("errors", "decision_confusion", "questions"):
            lines.append(f"  {name}={json.dumps(group[name], sort_keys=True, ensure_ascii=False)}")
    lines.append(f"Groups: {report['group_count']}; omitted by limit: {report['omitted_groups']}")
    return "\n".join(lines)
