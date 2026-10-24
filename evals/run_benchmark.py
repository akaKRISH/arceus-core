"""CLI entry point for the Phase 3.1 graph-retrieval evaluation.

Run the complete frozen experiment from the repository root:

    python -m evals.run_benchmark

Optional:

    python -m evals.run_benchmark --suite development --repeats 1
    python -m evals.run_benchmark --suite heldout
    python -m evals.run_benchmark --output reports/my_results.json

The run uses only temporary synthetic fixtures and temporary SQLite indexes.
No network access or model inference is involved.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evals.config import DEFAULT_REPEATS, SUITE_DESCRIPTIONS, seeds_for_suite
from evals.harness import run_evaluation
from evals.tasks import CATEGORIES

DEFAULT_OUTPUT = Path("reports") / "graph_retrieval_evaluation_v2.json"


def _format_rate(success: dict[str, object], total: int) -> str:
    return f"{success['count']}/{total} ({float(success['rate']):.1%})"


def _format_optional(value: object, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.{digits}f}"


def _print_table(title: str, aggregates: dict[str, dict], systems: list[str]) -> None:
    print(title)
    print(
        f"{'system':<16} {'success':>18} {'target F1':>10} {'evid. F1':>9} "
        f"{'top-1':>7} {'median ms':>10} {'p95 ms':>8}"
    )
    for name in systems:
        aggregate = aggregates[name]
        top1 = aggregate["top1"]
        top1_text = "n/a" if top1["accuracy"] is None else f"{top1['correct']}/{top1['task_count']}"
        print(
            f"{name:<16} {_format_rate(aggregate['success'], aggregate['task_count']):>18} "
            f"{_format_optional(aggregate['target_f1_macro']):>10} "
            f"{_format_optional(aggregate['evidence_f1_macro']):>9} "
            f"{top1_text:>7} "
            f"{_format_optional(aggregate['latency_ms']['median']):>10} "
            f"{_format_optional(aggregate['latency_ms']['p95']):>8}"
        )
    print()


def _print_categories(categories: dict[str, dict], systems: list[str]) -> None:
    print("success by category (count/total per system):")
    header = f"  {'category':<24}" + "".join(f"{name:>20}" for name in systems)
    print(header)
    for category in CATEGORIES:
        row = categories[category]
        cells = []
        for name in systems:
            success = row[name]["success"]
            total = row[name]["task_count"]
            cells.append(f"{success['count']}/{total}".rjust(20))
        print(f"  {category:<24}" + "".join(cells))
    print()


def _print_paired(label: str, comparisons: dict[str, dict]) -> None:
    print(f"paired task-instance success ({label}):")
    for key in (
        "path_derived_vs_path_only",
        "graph_assisted_vs_path_only",
        "graph_assisted_vs_path_derived",
    ):
        row = comparisons[key]
        print(
            f"  {row['left']} vs {row['right']}: "
            f"+{row['improvements']} / -{row['regressions']} / "
            f"ties {row['ties_success']} success, {row['ties_failure']} failure"
        )
    print()


def _print_summary(artifact: dict, output: Path | None, suite: str) -> None:
    experiment = artifact["experiment"]
    systems = experiment["systems"]
    print(f"ARCEUS retrieval evaluation ({experiment['benchmark_version']})")
    print(
        f"config: {experiment['config_version']}  seeds: {experiment['seed_count']}  "
        f"tasks/seed: {experiment['task_count_per_seed']}  "
        f"instances: {experiment['task_instances']}  repeats: {experiment['repeats']}"
    )
    print(f"suite: {suite} — {SUITE_DESCRIPTIONS[suite]}")
    print()

    _print_table("all evaluated seeds", artifact["aggregates"], systems)
    for split, summary in artifact["splits"].items():
        _print_table(
            f"{split} split ({summary['seed_count']} seeds, "
            f"{summary['task_instances']} task instances)",
            summary["aggregates"],
            systems,
        )

    _print_categories(artifact["categories"], systems)
    for split in ("heldout", "development"):
        if split in artifact["paired_comparisons"]:
            _print_paired(split, artifact["paired_comparisons"][split])

    if output is not None:
        print(f"artifact: {output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        choices=("all", "development", "heldout"),
        default="all",
        help="frozen seed split to run (default: all)",
    )
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"JSON artifact path (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args(argv)

    artifact = run_evaluation(
        repeats=args.repeats,
        seeds=seeds_for_suite(args.suite),
        output_path=args.output,
    )
    _print_summary(artifact, args.output, args.suite)
    return 0


if __name__ == "__main__":
    sys.exit(main())
