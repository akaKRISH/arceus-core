"""Reproducible baseline benchmark for the metadata index.

Creates a synthetic fixture tree in a temporary directory (never a real user
folder), scans it, measures search latency, reconciliation, database size, and
Python-heap peak during a scan, then prints a JSON report.

Usage:
    python experiments/bench_index.py --files 2000 --dirs 20 --repeats 50

Notes:
* ``python_heap_peak_mib`` is measured with ``tracemalloc`` and covers Python
  allocations only. SQLite page cache and native allocations are not included.
* Timings are single-machine desktop numbers; treat them as a baseline, not a
  portable performance claim.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

from arceus_core.database import database_size_bytes, open_database
from arceus_core.roots import add_root
from arceus_core.scanner import scan_root
from arceus_core.search import SearchFilters, search

EXTENSIONS = ("txt", "md", "py")


def build_tree(root: Path, files: int, dirs: int) -> None:
    for index in range(dirs):
        directory = root / f"dir{index:03d}" / "nested"
        directory.mkdir(parents=True, exist_ok=True)
    for index in range(files):
        directory = root / f"dir{index % dirs:03d}" / "nested"
        extension = EXTENSIONS[index % len(EXTENSIONS)]
        (directory / f"file {index:05d}.{extension}").write_text("", encoding="utf-8")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def measure_search(conn, query: str, repeats: int) -> dict[str, float]:
    durations: list[float] = []
    hits = 0
    for _ in range(repeats):
        started = time.perf_counter()
        result = search(conn, query, SearchFilters(limit=20))
        durations.append((time.perf_counter() - started) * 1000.0)
        hits = len(result.hits)
    return {
        "median_ms": round(statistics.median(durations), 3),
        "p95_ms": round(percentile(durations, 0.95), 3),
        "hits": hits,
    }


def run(files: int, dirs: int, repeats: int) -> dict:
    if files < 1 or dirs < 1:
        raise SystemExit("--files and --dirs must both be positive")

    with tempfile.TemporaryDirectory(prefix="arceus-bench-") as temp:
        base = Path(temp)
        root_dir = base / "fixture"
        root_dir.mkdir()
        db_path = base / "data" / "arceus.sqlite3"
        build_tree(root_dir, files, dirs)

        with open_database(db_path) as conn:
            root = add_root(conn, root_dir)

            started = time.perf_counter()
            first_scan = scan_root(conn, root)
            first_scan_seconds = time.perf_counter() - started

            started = time.perf_counter()
            second_scan = scan_root(conn, root)
            second_scan_seconds = time.perf_counter() - started

            tracemalloc.start()
            scan_root(conn, root)
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()

            searches = {
                "exact_name": measure_search(conn, "file 00042.txt", repeats),
                "prefix": measure_search(conn, "file 00", repeats),
                "common_substring_limited": measure_search(conn, "file", repeats),
                "path_only": measure_search(conn, "dir007", repeats),
                "no_match_worst_case": measure_search(conn, "zzzz-no-such-entry", repeats),
            }

            indexed_files = conn.execute(
                "SELECT COUNT(*) FROM entries WHERE entry_type = 'file' AND status = 'present'"
            ).fetchone()[0]
            indexed_dirs = conn.execute(
                "SELECT COUNT(*) FROM entries WHERE entry_type = 'dir' AND status = 'present'"
            ).fetchone()[0]

            # Delete 10% of files, then measure reconciliation.
            deleted = 0
            for index in range(0, files, 10):
                path = (
                    root_dir
                    / f"dir{index % dirs:03d}"
                    / "nested"
                    / (f"file {index:05d}.{EXTENSIONS[index % len(EXTENSIONS)]}")
                )
                if path.exists():
                    path.unlink()
                    deleted += 1
            started = time.perf_counter()
            reconcile_scan = scan_root(conn, root)
            reconcile_seconds = time.perf_counter() - started

            checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            db_bytes = database_size_bytes(db_path)
            missing_rows = conn.execute(
                "SELECT COUNT(*) FROM entries WHERE status = 'missing'"
            ).fetchone()[0]

    return {
        "fixture": {"requested_files": files, "requested_dirs": dirs},
        "index": {
            "files": int(indexed_files),
            "dirs": int(indexed_dirs),
            "db_bytes": db_bytes,
            "bytes_per_entry": round(db_bytes / max(1, int(indexed_files) + int(indexed_dirs)), 2),
        },
        "scans": {
            "first_scan_seconds": round(first_scan_seconds, 3),
            "second_scan_seconds": round(second_scan_seconds, 3),
            "files_per_second_first_scan": round(first_scan.files_seen / first_scan_seconds, 1)
            if first_scan_seconds
            else None,
            "idempotent_rescan_marked_missing": second_scan.entries_marked_missing,
            "python_heap_peak_mib": round(peak / (1024 * 1024), 2),
            "deleted_files": deleted,
            "reconcile_scan_seconds": round(reconcile_seconds, 3),
            "reconciled_missing_entries": reconcile_scan.entries_marked_missing,
            "missing_rows_after": int(missing_rows),
            "wal_checkpoint": checkpoint[0] if checkpoint else None,
        },
        "search": searches,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=2000)
    parser.add_argument("--dirs", type=int, default=20)
    parser.add_argument("--repeats", type=int, default=50)
    parser.add_argument("--json-out", help="also write the report to this path")
    args = parser.parse_args(argv)

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "processor": platform.processor() or "unknown",
            "logical_cpus": os.cpu_count(),
            "machine": platform.machine(),
        },
        **run(args.files, args.dirs, args.repeats),
    }

    text = json.dumps(report, indent=2)
    print(text)
    if args.json_out:
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
