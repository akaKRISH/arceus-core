"""Reproducible benchmark orchestration for the Phase 3.1 evaluation.

The harness generates fresh temporary fixtures and indexes, validates task
ground truth against the generated filesystem, runs all three retrievers over
identical ``RetrievalSpec`` objects, scores them, and builds one JSON artifact.
It never writes inside the fixture root, and deletes the temporary workspace.
"""

from __future__ import annotations

import json
import os
import platform
import sqlite3
import statistics
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arceus_core import __version__
from arceus_core.database import database_size_bytes, open_database
from arceus_core.models import Root
from arceus_core.roots import add_root
from arceus_core.scanner import ScanOptions, scan_root
from evals.config import (
    ALL_SEEDS,
    BENCHMARK_VERSION,
    CONFIG_VERSION,
    DEFAULT_REPEATS,
    DEVELOPMENT_SEEDS,
    EVAL_SEARCH_LIMIT,
    EVAL_TREE_LIMIT,
    FIXTURE_VERSION,
    HELDOUT_SEEDS,
    TASKS_PER_SEED,
    split_for_seed,
)
from evals.fixture import (
    FixtureModel,
    apply_post_index_mutations,
    build_fixture,
)
from evals.metrics import SystemOutcome, score_task, summarize
from evals.retrieval import Retrieval, Retriever
from evals.retrievers import default_retrievers
from evals.tasks import CATEGORIES, BenchmarkTask, build_tasks

EXPERIMENT_ID = "ARCEUS-GRAPH-RETRIEVAL-002"

LIMITATIONS: tuple[str, ...] = (
    "Synthetic fixtures only; no user documents were indexed.",
    "The graph system and the path-derived system share the same scalar metadata, "
    "but the graph system uses the production graph API while the path-derived "
    "system reconstructs structure from strings.",
    "Path-only receives task text and the documented extension search filter; it "
    "cannot express directory enumeration, depth, or ancestor queries.",
    "Latency is an exploratory engineering measurement, not a controlled performance "
    "experiment; repeated timings are not independent task observations.",
    "All seeds use the same 35-task category definition even though names, nesting, "
    "package presence, placement, and filler counts vary.",
    "Verification is Windows-only; no cross-platform claim is made.",
)


def _signature(retrieval: Retrieval) -> tuple[object, ...]:
    return (
        retrieval.targets,
        tuple(sorted(retrieval.evidence)),
        retrieval.notes,
    )


def _scrub(text: str, fixture_root: Path) -> str:
    """Remove the ephemeral workspace path from any diagnostic text."""

    if not text:
        return ""
    return text.replace(str(fixture_root), "<fixture>").replace(
        str(fixture_root).replace("\\", "/"), "<fixture>"
    )


def _measure(
    conn: sqlite3.Connection,
    root: Root,
    task: BenchmarkTask,
    retriever: Retriever,
    repeats: int,
    fixture_root: Path,
) -> tuple[Retrieval, tuple[float, ...], bool]:
    """Warm up once, then time ``repeats`` identical retrievals.

    Correctness comes from the final result. The signature is compared across
    every run so nondeterminism is reported rather than hidden. Retrievers
    receive a ground-truth-free spec.
    """

    spec = task.retrieval_spec()
    warmup = retriever.retrieve(conn, root, spec)
    signatures = {_signature(warmup)}
    latencies: list[float] = []
    latest = warmup
    for _ in range(repeats):
        started = time.perf_counter()
        latest = retriever.retrieve(conn, root, spec)
        latencies.append((time.perf_counter() - started) * 1000.0)
        signatures.add(_signature(latest))
    latest = Retrieval(
        targets=latest.targets,
        evidence=latest.evidence,
        notes=_scrub(latest.notes, fixture_root),
        metadata=latest.metadata,
    )
    return latest, tuple(latencies), len(signatures) == 1


def _count_entries(conn: sqlite3.Connection, root_id: int, status: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM entries WHERE root_id = ? AND status = ?",
        (root_id, status),
    ).fetchone()
    return int(row[0]) if row is not None else 0


def _validate_ground_truth(
    fixture_root: Path,
    model: FixtureModel,
    tasks: tuple[BenchmarkTask, ...],
) -> None:
    """Check every expected target and evidence label against disk and model."""

    for task in tasks:
        for target in task.expected_targets:
            on_disk = (fixture_root / target).exists()
            if task.kind == "missing":
                if target not in model.missing_files or on_disk:
                    raise RuntimeError(f"{task.id}: missing target is not absent: {target}")
            elif target not in model.entries() or not on_disk:
                raise RuntimeError(f"{task.id}: present target is not on disk: {target}")

        for label in task.expected_evidence:
            kind, _, payload = label.partition(":")
            if kind == "indexed":
                if payload not in model.entries() or not (fixture_root / payload).exists():
                    raise RuntimeError(f"{task.id}: invalid indexed evidence: {payload}")
                continue
            if kind == "missing":
                if payload not in model.missing_files or (fixture_root / payload).exists():
                    raise RuntimeError(f"{task.id}: invalid missing evidence: {payload}")
                continue
            if kind == "contains":
                parent, separator, child = payload.partition("|")
                if not separator or not child or not (fixture_root / child).exists():
                    raise RuntimeError(f"{task.id}: invalid contains evidence: {label}")
                expected_parent = "" if parent == "." else parent
                if model.parent(child) != expected_parent:
                    raise RuntimeError(f"{task.id}: parent mismatch in evidence: {label}")
                if expected_parent and not (fixture_root / expected_parent).is_dir():
                    raise RuntimeError(f"{task.id}: parent directory absent: {expected_parent}")
                continue
            raise RuntimeError(f"{task.id}: unknown evidence label: {label}")


def _task_payload(
    task: BenchmarkTask,
    results: dict[str, tuple[Retrieval, tuple[float, ...], bool, Any]],
    system_names: list[str],
) -> dict[str, Any]:
    return {
        "id": task.id,
        "category": task.category,
        "kind": task.kind,
        "baseline_text": task.baseline_text,
        "params": task.params,
        "expected_targets": list(task.expected_targets),
        "expected_evidence": sorted(task.expected_evidence),
        "systems": {
            name: {
                "targets": list(results[name][0].targets),
                "evidence": sorted(results[name][0].evidence),
                "success": results[name][3].success,
                "top1": results[name][3].top1,
                "precision": results[name][3].precision,
                "recall": results[name][3].recall,
                "f1": results[name][3].f1,
                "evidence_precision": results[name][3].evidence_precision,
                "evidence_recall": results[name][3].evidence_recall,
                "evidence_f1": results[name][3].evidence_f1,
                "latency_ms_median": round(statistics.median(results[name][1]), 4),
                "deterministic": results[name][2],
                "notes": results[name][0].notes or None,
            }
            for name in system_names
        },
    }


def _environment() -> dict[str, object]:
    """Environment details without user directories or machine identifiers."""

    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "arceus_version": __version__,
    }


def _run_seed(
    seed: int,
    workspace: Path,
    repeats: int,
) -> dict[str, Any]:
    split = split_for_seed(seed)
    seed_root = workspace / f"seed-{seed}"
    fixture_root = seed_root / "fixture"
    fixture_root.mkdir(parents=True)
    model = build_fixture(fixture_root, seed)
    db_path = seed_root / "index" / "eval.sqlite3"
    exclusions = (
        str(db_path),
        f"{db_path}-wal",
        f"{db_path}-shm",
        str(db_path.parent),
    )

    with open_database(db_path) as conn:
        root = add_root(conn, fixture_root)
        first_scan = scan_root(conn, root, ScanOptions(exclude_paths=exclusions))
        apply_post_index_mutations(fixture_root, model)
        second_scan = scan_root(conn, root, ScanOptions(exclude_paths=exclusions))

        expected_present = len(model.files) + len(model.dirs)
        present_entries = _count_entries(conn, root.id, "present")
        missing_entries = _count_entries(conn, root.id, "missing")

        if first_scan.status != "completed" or first_scan.errors:
            raise RuntimeError(
                f"seed {seed}: first scan was not clean: "
                f"{first_scan.status}, {first_scan.errors} error(s)"
            )
        if second_scan.status != "completed" or second_scan.errors:
            raise RuntimeError(
                f"seed {seed}: post-mutation scan was not clean: "
                f"{second_scan.status}, {second_scan.errors} error(s)"
            )
        if present_entries != expected_present:
            raise RuntimeError(
                f"seed {seed}: index integrity failure: {present_entries} present entries, "
                f"expected {expected_present}"
            )
        if missing_entries != len(model.missing_files):
            raise RuntimeError(
                f"seed {seed}: index integrity failure: {missing_entries} missing entries, "
                f"expected {len(model.missing_files)}"
            )

        tasks = build_tasks(model)
        if len(tasks) != TASKS_PER_SEED:
            raise RuntimeError(f"seed {seed}: task count {len(tasks)} is not frozen")
        _validate_ground_truth(fixture_root, model, tasks)

        systems = default_retrievers()
        system_names = [retriever.name for retriever in systems]
        outcomes: dict[str, list[SystemOutcome]] = {name: [] for name in system_names}
        task_reports: list[dict[str, Any]] = []

        for task in tasks:
            per_system: dict[str, tuple[Retrieval, tuple[float, ...], bool, Any]] = {}
            for retriever in systems:
                retrieval, latencies, deterministic = _measure(
                    conn,
                    root,
                    task,
                    retriever,
                    repeats,
                    fixture_root,
                )
                score = score_task(task, retrieval)
                outcomes[retriever.name].append(
                    SystemOutcome(
                        task=task,
                        retrieval=retrieval,
                        score=score,
                        latencies_ms=latencies,
                        deterministic=deterministic,
                    )
                )
                per_system[retriever.name] = (
                    retrieval,
                    latencies,
                    deterministic,
                    score,
                )
            task_reports.append(_task_payload(task, per_system, system_names))

        aggregates = {name: summarize(outcomes[name]) for name in system_names}
        categories = {
            category: {
                name: summarize(
                    [outcome for outcome in outcomes[name] if outcome.task.category == category]
                )
                for name in system_names
            }
            for category in CATEGORIES
        }

        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db_bytes = database_size_bytes(db_path)

    report: dict[str, Any] = {
        "seed": seed,
        "split": split,
        "fixture": {
            "present_files": len(model.files),
            "directories": len(model.dirs),
            "filler_files": model.filler_file_count,
            "missing_after_scan": sorted(model.missing_files),
            "max_depth": max(model.depth(path) for path in model.entries()),
        },
        "index": {
            "present_entries": present_entries,
            "missing_entries": missing_entries,
            "db_bytes": db_bytes,
            "first_scan_seconds": first_scan.duration_seconds,
            "post_mutation_scan_seconds": second_scan.duration_seconds,
        },
        "ground_truth": {
            "validated_against_disk": True,
            "task_count": len(tasks),
        },
        "aggregates": aggregates,
        "categories": categories,
        "tasks": task_reports,
    }
    return {
        "seed": seed,
        "split": split,
        "report": report,
        "outcomes": outcomes,
    }


def _paired(
    left: dict[tuple[int, str], SystemOutcome],
    right: dict[tuple[int, str], SystemOutcome],
    *,
    left_name: str,
    right_name: str,
) -> dict[str, Any]:
    improvements = 0
    regressions = 0
    ties_success = 0
    ties_failure = 0
    by_category: dict[str, dict[str, int]] = {}

    for key in sorted(left):
        if key not in right:
            continue
        left_success = left[key].score.success
        right_success = right[key].score.success
        category = left[key].task.category
        row = by_category.setdefault(
            category,
            {"improvements": 0, "regressions": 0, "ties": 0},
        )
        if left_success and not right_success:
            improvements += 1
            row["improvements"] += 1
        elif right_success and not left_success:
            regressions += 1
            row["regressions"] += 1
        else:
            row["ties"] += 1
            if left_success:
                ties_success += 1
            else:
                ties_failure += 1

    return {
        "left": left_name,
        "right": right_name,
        "pair_count": sum(
            row["improvements"] + row["regressions"] + row["ties"] for row in by_category.values()
        ),
        "improvements": improvements,
        "regressions": regressions,
        "ties_success": ties_success,
        "ties_failure": ties_failure,
        "by_category": by_category,
    }


def _split_summary(
    seed_results: list[dict[str, Any]],
    system_names: list[str],
) -> dict[str, Any]:
    if not seed_results:
        raise ValueError("cannot summarize an empty split")

    outcomes = {name: [] for name in system_names}
    for result in seed_results:
        for name in system_names:
            outcomes[name].extend(result["outcomes"][name])

    per_seed_success = {
        str(result["seed"]): {
            name: result["report"]["aggregates"][name]["success"] for name in system_names
        }
        for result in seed_results
    }
    categories = {
        category: {
            name: summarize(
                [outcome for outcome in outcomes[name] if outcome.task.category == category]
            )
            for name in system_names
        }
        for category in CATEGORIES
    }
    return {
        "seed_count": len(seed_results),
        "task_instances": sum(len(result["report"]["tasks"]) for result in seed_results),
        "aggregates": {name: summarize(outcomes[name]) for name in system_names},
        "categories": categories,
        "per_seed_success": per_seed_success,
    }


def run_evaluation(
    *,
    repeats: int = DEFAULT_REPEATS,
    seeds: tuple[int, ...] = ALL_SEEDS,
    output_path: str | Path | None = None,
    workspace_parent: str | Path | None = None,
) -> dict[str, Any]:
    """Run the frozen evaluation and return the JSON-serializable artifact."""

    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    if not seeds:
        raise ValueError("at least one seed is required")
    if len(set(seeds)) != len(seeds):
        raise ValueError("seeds must be unique")
    for seed in seeds:
        split_for_seed(seed)  # validates membership in the frozen configuration

    parent = Path(workspace_parent) if workspace_parent is not None else None
    if parent is not None:
        parent.mkdir(parents=True, exist_ok=True)

    seed_results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(
        prefix="arceus-eval-",
        dir=str(parent) if parent is not None else None,
    ) as temporary:
        workspace = Path(temporary)
        for seed in seeds:
            seed_results.append(_run_seed(seed, workspace, repeats))

    system_names = [retriever.name for retriever in default_retrievers()]
    all_outcomes: dict[str, list[SystemOutcome]] = {name: [] for name in system_names}
    keyed: dict[str, dict[tuple[int, str], SystemOutcome]] = {name: {} for name in system_names}
    for result in seed_results:
        for name in system_names:
            all_outcomes[name].extend(result["outcomes"][name])
            for outcome in result["outcomes"][name]:
                keyed[name][(result["seed"], outcome.task.id)] = outcome

    splits = sorted({result["split"] for result in seed_results})
    split_reports = {
        split: _split_summary(
            [result for result in seed_results if result["split"] == split],
            system_names,
        )
        for split in splits
    }

    comparisons: dict[str, Any] = {}
    for split in ["all", *splits]:
        selected = [result for result in seed_results if split == "all" or result["split"] == split]
        selected_keys = {
            (result["seed"], task["id"])
            for result in selected
            for task in result["report"]["tasks"]
        }
        split_keyed = {
            name: {key: keyed[name][key] for key in selected_keys if key in keyed[name]}
            for name in system_names
        }
        comparisons[split] = {
            "path_derived_vs_path_only": _paired(
                split_keyed["path_derived"],
                split_keyed["path_only"],
                left_name="path_derived",
                right_name="path_only",
            ),
            "graph_assisted_vs_path_only": _paired(
                split_keyed["graph_assisted"],
                split_keyed["path_only"],
                left_name="graph_assisted",
                right_name="path_only",
            ),
            "graph_assisted_vs_path_derived": _paired(
                split_keyed["graph_assisted"],
                split_keyed["path_derived"],
                left_name="graph_assisted",
                right_name="path_derived",
            ),
        }

    aggregate_categories = {
        category: {
            name: summarize(
                [outcome for outcome in all_outcomes[name] if outcome.task.category == category]
            )
            for name in system_names
        }
        for category in CATEGORIES
    }

    artifact: dict[str, Any] = {
        "experiment": {
            "id": EXPERIMENT_ID,
            "benchmark_version": BENCHMARK_VERSION,
            "fixture_version": FIXTURE_VERSION,
            "config_version": CONFIG_VERSION,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "repeats": repeats,
            "task_count_per_seed": TASKS_PER_SEED,
            "seed_count": len(seeds),
            "task_instances": len(seeds) * TASKS_PER_SEED,
            "seeds": {
                "development": list(DEVELOPMENT_SEEDS),
                "heldout": list(HELDOUT_SEEDS),
            },
            "evaluated_seeds": {
                split: [result["seed"] for result in seed_results if result["split"] == split]
                for split in splits
            },
            "search_limit": EVAL_SEARCH_LIMIT,
            "tree_limit": EVAL_TREE_LIMIT,
            "systems": system_names,
            "criteria": {
                "success": "returned target set equals expected target set",
                "top1": "first returned target equals the single expected target",
                "evidence": "parent-child, indexed, or missing labels match expected labels",
                "paired": "success changes per (seed, task) pair",
            },
            "frozen": True,
            "limitations": list(LIMITATIONS),
        },
        "environment": _environment(),
        "aggregates": {name: summarize(all_outcomes[name]) for name in system_names},
        "categories": aggregate_categories,
        "splits": split_reports,
        "paired_comparisons": comparisons,
        "seeds": [result["report"] for result in seed_results],
    }

    if output_path is not None:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(artifact, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    return artifact


__all__ = [
    "EXPERIMENT_ID",
    "LIMITATIONS",
    "run_evaluation",
]
