"""Typed records returned by the storage, scan, search, and graph services."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime


def utc_now() -> str:
    """Return a second-precision UTC timestamp suitable for storing in SQLite."""

    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class Root:
    id: int
    path: str
    label: str | None
    created_at: str
    status: str
    last_scan_run_id: int | None


@dataclass(frozen=True, slots=True)
class Entry:
    id: int
    root_id: int
    rel_path: str
    parent_rel_path: str
    name: str
    extension: str
    entry_type: str
    size_bytes: int | None
    mtime_ns: int | None
    first_seen_run_id: int | None
    last_seen_run_id: int
    status: str
    observed_at: str


@dataclass(frozen=True, slots=True)
class ScanRun:
    id: int
    root_id: int
    started_at: str
    finished_at: str | None
    status: str
    files_seen: int
    dirs_seen: int
    entries_marked_missing: int
    error_count: int
    skipped_symlinks: int
    notes: str | None


@dataclass(frozen=True, slots=True)
class ScanErrorRecord:
    rel_path: str | None
    kind: str
    message: str


@dataclass(frozen=True, slots=True)
class ScanSummary:
    run_id: int
    root_id: int
    root_path: str
    status: str
    files_seen: int
    dirs_seen: int
    entries_marked_missing: int
    errors: int
    skipped_symlinks: int
    skipped_other: int
    duration_seconds: float
    interrupted_runs_recovered: int = 0
    error_samples: tuple[ScanErrorRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class SearchHit:
    entry: Entry
    root_path: str
    absolute_path: str
    score: int
    reason: str


@dataclass(frozen=True, slots=True)
class SearchResult:
    query: str
    hits: list[SearchHit]
    warnings: list[str] = field(default_factory=list)
    limit: int = 20


@dataclass(frozen=True, slots=True)
class AncestorLink:
    rel_path: str
    display_name: str
    observed: bool
    entry_type: str


@dataclass(frozen=True, slots=True)
class ResolvedPath:
    root: Root
    rel_path: str
    absolute_path: str


def root_from_row(row: sqlite3.Row) -> Root:
    return Root(
        id=int(row["id"]),
        path=str(row["path"]),
        label=row["label"],
        created_at=str(row["created_at"]),
        status=str(row["status"]),
        last_scan_run_id=row["last_scan_run_id"],
    )


def entry_from_row(row: sqlite3.Row) -> Entry:
    return Entry(
        id=int(row["id"]),
        root_id=int(row["root_id"]),
        rel_path=str(row["rel_path"]),
        parent_rel_path=str(row["parent_rel_path"]),
        name=str(row["name"]),
        extension=str(row["extension"]),
        entry_type=str(row["entry_type"]),
        size_bytes=row["size_bytes"],
        mtime_ns=row["mtime_ns"],
        first_seen_run_id=row["first_seen_run_id"],
        last_seen_run_id=int(row["last_seen_run_id"]),
        status=str(row["status"]),
        observed_at=str(row["observed_at"]),
    )
