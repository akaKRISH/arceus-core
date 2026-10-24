"""Task scoring and aggregate metrics for the retrieval evaluation.

Denominators are explicit:

* success rate: all tasks, equally weighted;
* target precision/recall/F1 macro: tasks with non-empty expected targets
  (no-result tasks are scored separately);
* evidence precision/recall/F1 macro: tasks with non-empty expected evidence;
* top-1 accuracy: tasks with exactly one expected target;
* no-result false-positive rate: tasks whose ``kind`` is ``no_result``.

A task contributes equally regardless of how many targets it has. Tasks where
a metric does not apply are excluded from that metric's denominator rather than
counted as zero.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from evals.retrieval import Retrieval
from evals.tasks import BenchmarkTask


@dataclass(frozen=True, slots=True)
class TaskScore:
    success: bool
    top1: bool | None
    precision: float | None
    recall: float | None
    f1: float | None
    evidence_precision: float | None
    evidence_recall: float | None
    evidence_f1: float | None


@dataclass(frozen=True, slots=True)
class SystemOutcome:
    task: BenchmarkTask
    retrieval: Retrieval
    score: TaskScore
    latencies_ms: tuple[float, ...]
    deterministic: bool = True


def _prf(predicted: set[str], expected: set[str]) -> tuple[float, float, float]:
    if not predicted or not expected:
        return 0.0, 0.0, 0.0
    overlap = len(predicted & expected)
    precision = overlap / len(predicted)
    recall = overlap / len(expected)
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return precision, recall, f1


def score_task(task: BenchmarkTask, retrieval: Retrieval) -> TaskScore:
    expected_targets = set(task.expected_targets)
    predicted_targets = set(retrieval.targets)
    success = predicted_targets == expected_targets

    top1: bool | None = None
    if len(task.expected_targets) == 1:
        top1 = bool(retrieval.targets) and retrieval.targets[0] == task.expected_targets[0]

    if task.expected_targets:
        precision, recall, f1 = _prf(predicted_targets, expected_targets)
    else:
        precision = recall = f1 = None

    if task.expected_evidence:
        evidence_precision, evidence_recall, evidence_f1 = _prf(
            set(retrieval.evidence), set(task.expected_evidence)
        )
    else:
        evidence_precision = evidence_recall = evidence_f1 = None

    return TaskScore(
        success=success,
        top1=top1,
        precision=precision,
        recall=recall,
        f1=f1,
        evidence_precision=evidence_precision,
        evidence_recall=evidence_recall,
        evidence_f1=evidence_f1,
    )


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 4) if values else None


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        raise ValueError("cannot compute a percentile of an empty sample")
    index = min(len(sorted_values) - 1, max(0, round(fraction * (len(sorted_values) - 1))))
    return sorted_values[index]


def summarize(outcomes: list[SystemOutcome]) -> dict[str, object]:
    """Aggregate one system's outcomes with documented denominators."""

    if not outcomes:
        raise ValueError("cannot summarize zero outcomes")

    total = len(outcomes)
    successes = sum(1 for outcome in outcomes if outcome.score.success)
    top1_values = [outcome.score.top1 for outcome in outcomes if outcome.score.top1 is not None]
    no_result = [outcome for outcome in outcomes if outcome.task.kind == "no_result"]
    no_result_false_positives = sum(1 for outcome in no_result if outcome.retrieval.targets)

    empty_answers = sum(
        1 for outcome in outcomes if outcome.task.expected_targets and not outcome.retrieval.targets
    )
    retrieval_errors = sum(
        1 for outcome in outcomes if outcome.retrieval.notes.startswith("retrieval error:")
    )
    unsupported = sum(1 for outcome in outcomes if "unsupported" in outcome.retrieval.notes.lower())
    latencies = sorted(latency for outcome in outcomes for latency in outcome.latencies_ms)

    return {
        "task_count": total,
        "unique_task_count": len({outcome.task.id for outcome in outcomes}),
        "success": {
            "count": successes,
            "rate": round(successes / total, 4),
        },
        "top1": {
            "task_count": len(top1_values),
            "correct": sum(1 for value in top1_values if value),
            "accuracy": round(sum(1 for value in top1_values if value) / len(top1_values), 4)
            if top1_values
            else None,
        },
        "target_precision_macro": _mean(
            [outcome.score.precision for outcome in outcomes if outcome.score.precision is not None]
        ),
        "target_recall_macro": _mean(
            [outcome.score.recall for outcome in outcomes if outcome.score.recall is not None]
        ),
        "target_f1_macro": _mean(
            [outcome.score.f1 for outcome in outcomes if outcome.score.f1 is not None]
        ),
        "evidence_precision_macro": _mean(
            [
                outcome.score.evidence_precision
                for outcome in outcomes
                if outcome.score.evidence_precision is not None
            ]
        ),
        "evidence_recall_macro": _mean(
            [
                outcome.score.evidence_recall
                for outcome in outcomes
                if outcome.score.evidence_recall is not None
            ]
        ),
        "evidence_f1_macro": _mean(
            [
                outcome.score.evidence_f1
                for outcome in outcomes
                if outcome.score.evidence_f1 is not None
            ]
        ),
        "no_result": {
            "task_count": len(no_result),
            "false_positives": no_result_false_positives,
            "false_positive_rate": round(no_result_false_positives / len(no_result), 4)
            if no_result
            else None,
        },
        "failures": {
            "empty_answers": empty_answers,
            "retrieval_errors": retrieval_errors,
            "unsupported_tasks": unsupported,
        },
        "latency_ms": {
            "sample_count": len(latencies),
            "median": round(statistics.median(latencies), 3) if latencies else None,
            "p95": round(_percentile(latencies, 0.95), 3) if latencies else None,
        },
        "deterministic": all(outcome.deterministic for outcome in outcomes),
    }


def summarize_categories(
    outcomes: list[SystemOutcome],
) -> dict[str, dict[str, object]]:
    """Aggregate metrics per category using the same task-level definitions."""

    categories = sorted({outcome.task.category for outcome in outcomes})
    return {
        category: summarize([outcome for outcome in outcomes if outcome.task.category == category])
        for category in categories
    }


__all__ = [
    "SystemOutcome",
    "TaskScore",
    "score_task",
    "summarize",
    "summarize_categories",
]
