"""Aggregate index health for the ``status`` command and future UI."""

from __future__ import annotations

import sqlite3
from typing import Any

from arceus_core import __version__
from arceus_core.database import database_size_bytes, integrity_ok, schema_version


def collect_status(conn: sqlite3.Connection, db_path: str) -> dict[str, Any]:
    """Return a JSON-serializable summary of database, roots, and scan state."""

    roots = conn.execute("SELECT * FROM roots ORDER BY id").fetchall()
    counts = conn.execute(
        "SELECT root_id, entry_type, status, COUNT(*) AS count "
        "FROM entries GROUP BY root_id, entry_type, status"
    ).fetchall()

    per_root: dict[int, dict[str, int]] = {}
    totals = {"present": 0, "missing": 0, "files": 0, "dirs": 0}
    for row in counts:
        root_id = int(row["root_id"])
        bucket = per_root.setdefault(root_id, {"present": 0, "missing": 0, "files": 0, "dirs": 0})
        count = int(row["count"])
        entry_type = str(row["entry_type"])
        status = str(row["status"])
        bucket[status] += count
        totals[status] += count
        if status != "present":
            # The files/dirs counters describe the current view of the tree;
            # stale rows are reported separately as ``missing``.
            continue
        if entry_type == "file":
            bucket["files"] += count
            totals["files"] += count
        elif entry_type == "dir":
            bucket["dirs"] += count
            totals["dirs"] += count

    root_reports: list[dict[str, Any]] = []
    for row in roots:
        root_id = int(row["id"])
        last_scan = None
        if row["last_scan_run_id"] is not None:
            scan_row = conn.execute(
                "SELECT * FROM scan_runs WHERE id = ?", (row["last_scan_run_id"],)
            ).fetchone()
            if scan_row is not None:
                last_scan = {
                    "run_id": int(scan_row["id"]),
                    "started_at": scan_row["started_at"],
                    "finished_at": scan_row["finished_at"],
                    "status": scan_row["status"],
                    "files_seen": int(scan_row["files_seen"]),
                    "dirs_seen": int(scan_row["dirs_seen"]),
                    "entries_marked_missing": int(scan_row["entries_marked_missing"]),
                    "errors": int(scan_row["error_count"]),
                    "skipped_symlinks": int(scan_row["skipped_symlinks"]),
                    "notes": scan_row["notes"],
                }
        entry_counts = per_root.get(root_id, {"present": 0, "missing": 0, "files": 0, "dirs": 0})
        root_reports.append(
            {
                "id": root_id,
                "path": str(row["path"]),
                "label": row["label"],
                "status": str(row["status"]),
                "created_at": str(row["created_at"]),
                "entries": dict(entry_counts),
                "last_scan": last_scan,
            }
        )

    interrupted = conn.execute(
        "SELECT COUNT(*) FROM scan_runs WHERE status IN ('running', 'interrupted')"
    ).fetchone()
    interrupted_count = int(interrupted[0]) if interrupted is not None else 0

    return {
        "version": __version__,
        "database": {
            "path": db_path,
            "schema_version": schema_version(conn),
            "size_bytes": database_size_bytes(db_path),
            "integrity_ok": integrity_ok(conn),
        },
        "roots": root_reports,
        "totals": {
            "roots": len(root_reports),
            **totals,
        },
        "interrupted_or_running_scans": interrupted_count,
    }


def has_warnings(status: dict[str, Any]) -> list[str]:
    """Human-readable warnings derived from a status report."""

    warnings: list[str] = []
    if not status["database"]["integrity_ok"]:
        warnings.append("database quick_check did not return 'ok'; back up and rescan")
    for root in status["roots"]:
        if root["status"] != "active":
            warnings.append(f"root #{root['id']} is {root['status']}: {root['path']}")
        scan = root["last_scan"]
        if scan is None:
            warnings.append(f"root #{root['id']} has never been scanned: {root['path']}")
        elif scan["status"] != "completed":
            warnings.append(
                f"root #{root['id']} last scan status is {scan['status']}; "
                "results may be incomplete"
            )
    if status["interrupted_or_running_scans"]:
        warnings.append(
            f"{status['interrupted_or_running_scans']} scan run(s) are running or interrupted; "
            "the next scan of that root will mark stale runs interrupted"
        )
    return warnings
