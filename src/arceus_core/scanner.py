"""Metadata-only filesystem scanning.

Guarantees of this module:

* Only the registered root is traversed; the scanner never expands the scope.
* Symbolic links and Windows junctions are skipped, never followed.
* File contents are never read. Only directory-entry metadata is stored.
* Per-directory errors (permission denied, transient I/O errors) are recorded
  and do not abort the rest of the scan. A directory that could not be
  enumerated is not treated as empty during reconciliation.
* Writes are batched inside transactions; an interrupted scan leaves a run
  marked ``interrupted`` so the next scan can recover and report it.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from arceus_core.errors import ScanError
from arceus_core.models import Root, ScanErrorRecord, ScanSummary, utc_now
from arceus_core.paths import extension_of, join_rel, path_key, rel_key_for
from arceus_core.reconcile import reconcile_missing
from arceus_core.roots import set_root_last_scan, set_root_status

DEFAULT_BATCH_SIZE = 500
MAX_RECORDED_ERRORS = 200
PROGRESS_INTERVAL = 500

_UPSERT_SQL = """
INSERT INTO entries (
    root_id, rel_path, rel_key, parent_rel_path, parent_rel_key,
    name, name_key, extension, entry_type, size_bytes, mtime_ns,
    first_seen_run_id, last_seen_run_id, status, observed_at
) VALUES (
    :root_id, :rel_path, :rel_key, :parent_rel_path, :parent_rel_key,
    :name, :name_key, :extension, :entry_type, :size_bytes, :mtime_ns,
    :run_id, :run_id, 'present', :observed_at
)
ON CONFLICT (root_id, rel_key) DO UPDATE SET
    rel_path = excluded.rel_path,
    parent_rel_path = excluded.parent_rel_path,
    parent_rel_key = excluded.parent_rel_key,
    name = excluded.name,
    name_key = excluded.name_key,
    extension = excluded.extension,
    entry_type = excluded.entry_type,
    size_bytes = excluded.size_bytes,
    mtime_ns = excluded.mtime_ns,
    last_seen_run_id = excluded.last_seen_run_id,
    status = 'present',
    observed_at = excluded.observed_at
"""


@dataclass(slots=True)
class ScanOptions:
    """Scan configuration. Defaults are deliberately conservative."""

    exclude_paths: tuple[str, ...] = ()
    max_entries: int | None = None
    batch_size: int = DEFAULT_BATCH_SIZE

    def exclude_keys(self) -> frozenset[str]:
        return frozenset(path_key(path) for path in self.exclude_paths if path)


@dataclass(slots=True)
class ScanStats:
    files: int = 0
    dirs: int = 0
    skipped_symlinks: int = 0
    skipped_other: int = 0
    errors: int = 0

    @property
    def observed(self) -> int:
        return self.files + self.dirs

    def as_dict(self) -> dict[str, int]:
        return {
            "files": self.files,
            "dirs": self.dirs,
            "observed": self.observed,
            "skipped_symlinks": self.skipped_symlinks,
            "skipped_other": self.skipped_other,
            "errors": self.errors,
        }


@dataclass(frozen=True, slots=True)
class _EntryRow:
    rel_path: str
    rel_key: str
    parent_rel_path: str
    parent_rel_key: str
    name: str
    name_key: str
    extension: str
    entry_type: str
    size_bytes: int | None
    mtime_ns: int | None


@dataclass(slots=True)
class _ScanBuffer:
    """Batched writer so a huge tree is not accumulated in memory."""

    conn: sqlite3.Connection
    root_id: int
    run_id: int
    batch_size: int
    observed_at: str = field(default_factory=utc_now)
    pending: list[dict[str, Any]] = field(default_factory=list)

    def add(self, row: _EntryRow) -> None:
        self.pending.append(
            {
                "root_id": self.root_id,
                "rel_path": row.rel_path,
                "rel_key": row.rel_key,
                "parent_rel_path": row.parent_rel_path,
                "parent_rel_key": row.parent_rel_key,
                "name": row.name,
                "name_key": row.name_key,
                "extension": row.extension,
                "entry_type": row.entry_type,
                "size_bytes": row.size_bytes,
                "mtime_ns": row.mtime_ns,
                "run_id": self.run_id,
                "observed_at": self.observed_at,
            }
        )
        if len(self.pending) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if not self.pending:
            return
        with self.conn:
            self.conn.executemany(_UPSERT_SQL, self.pending)
        self.pending.clear()


def _is_link(entry: os.DirEntry[str]) -> bool:
    """Detect symlinks and Windows junctions without following them."""

    try:
        if entry.is_symlink():
            return True
    except OSError:
        return False
    is_junction = getattr(entry, "is_junction", None)
    if is_junction is not None:
        try:
            return bool(is_junction())
        except OSError:
            return False
    return False


def recover_interrupted_runs(conn: sqlite3.Connection, root_id: int) -> int:
    """Mark scans left ``running`` by a previous process as interrupted."""

    with conn:
        cursor = conn.execute(
            "UPDATE scan_runs "
            "SET status = 'interrupted', finished_at = COALESCE(finished_at, ?), "
            "    notes = COALESCE(notes, 'process ended before the scan completed') "
            "WHERE root_id = ? AND status = 'running'",
            (utc_now(), root_id),
        )
    return max(cursor.rowcount, 0)


def _start_run(conn: sqlite3.Connection, root_id: int) -> int:
    with conn:
        cursor = conn.execute(
            "INSERT INTO scan_runs (root_id, started_at, status) VALUES (?, ?, 'running')",
            (root_id, utc_now()),
        )
    return int(cursor.lastrowid)


def _finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    stats: ScanStats,
    marked_missing: int,
    errors: list[ScanErrorRecord],
    note: str | None = None,
) -> None:
    with conn:
        conn.execute(
            "UPDATE scan_runs SET status = ?, finished_at = ?, files_seen = ?, dirs_seen = ?, "
            "entries_marked_missing = ?, error_count = ?, skipped_symlinks = ?, notes = ? "
            "WHERE id = ?",
            (
                status,
                utc_now(),
                stats.files,
                stats.dirs,
                marked_missing,
                stats.errors,
                stats.skipped_symlinks,
                note,
                run_id,
            ),
        )
        if errors:
            conn.executemany(
                "INSERT INTO scan_errors (run_id, rel_path, kind, message) VALUES (?, ?, ?, ?)",
                [(run_id, item.rel_path, item.kind, item.message) for item in errors],
            )


def scan_root(
    conn: sqlite3.Connection,
    root: Root,
    options: ScanOptions | None = None,
    progress: Callable[[ScanStats], None] | None = None,
) -> ScanSummary:
    """Scan one registered root and reconcile the index with what was observed."""

    opts = options or ScanOptions()
    if opts.batch_size <= 0:
        raise ScanError("batch_size must be positive")
    started = time.monotonic()
    recovered = recover_interrupted_runs(conn, root.id)
    run_id = _start_run(conn, root.id)

    stats = ScanStats()
    errors: list[ScanErrorRecord] = []
    complete_dirs: set[str] = set()
    exclude_keys = opts.exclude_keys()
    buffer = _ScanBuffer(conn=conn, root_id=root.id, run_id=run_id, batch_size=opts.batch_size)
    status = "completed"
    marked_missing = 0

    def record_error(rel_path: str | None, kind: str, message: str) -> None:
        stats.errors += 1
        if len(errors) < MAX_RECORDED_ERRORS:
            errors.append(ScanErrorRecord(rel_path=rel_path, kind=kind, message=message))

    def note_entry() -> None:
        if progress is not None and stats.observed % PROGRESS_INTERVAL == 0:
            progress(stats)

    try:
        if not os.path.isdir(root.path):
            record_error(
                None,
                "root_unavailable",
                f"root is not an accessible directory: {root.path}",
            )
            status = "failed"
        else:
            _walk(
                root_path=root.path,
                options=opts,
                exclude_keys=exclude_keys,
                buffer=buffer,
                stats=stats,
                complete_dirs=complete_dirs,
                record_error=record_error,
                note_entry=note_entry,
            )
            buffer.flush()
            status = "partial" if stats.errors else "completed"
    except ScanError as exc:
        buffer.flush()
        record_error(None, "limit", str(exc))
        status = "failed"
    except BaseException:
        with contextlib.suppress(sqlite3.Error):
            # Preserve already-observed rows even though the scan is aborting.
            buffer.flush()
        _finish_run(
            conn,
            run_id,
            "interrupted",
            stats,
            marked_missing=0,
            errors=errors,
            note="scan aborted before completion",
        )
        raise

    if status != "failed":
        marked_missing = reconcile_missing(conn, root.id, run_id, complete_dirs)

    _finish_run(
        conn,
        run_id,
        status,
        stats,
        marked_missing=marked_missing,
        errors=errors,
    )
    set_root_last_scan(conn, root.id, run_id)

    root_available = os.path.isdir(root.path)
    set_root_status(conn, root.id, "active" if root_available else "missing")

    if progress is not None:
        progress(stats)

    duration = time.monotonic() - started
    return ScanSummary(
        run_id=run_id,
        root_id=root.id,
        root_path=root.path,
        status=status,
        files_seen=stats.files,
        dirs_seen=stats.dirs,
        entries_marked_missing=marked_missing,
        errors=stats.errors,
        skipped_symlinks=stats.skipped_symlinks,
        skipped_other=stats.skipped_other,
        duration_seconds=round(duration, 4),
        interrupted_runs_recovered=recovered,
        error_samples=tuple(errors[:10]),
    )


def _walk(
    *,
    root_path: str,
    options: ScanOptions,
    exclude_keys: frozenset[str],
    buffer: _ScanBuffer,
    stats: ScanStats,
    complete_dirs: set[str],
    record_error: Callable[[str | None, str, str], None],
    note_entry: Callable[[], None],
) -> None:
    stack: list[tuple[str, str]] = [(root_path, "")]

    while stack:
        abs_dir, rel_dir = stack.pop()
        try:
            with os.scandir(abs_dir) as iterator:
                children = sorted(iterator, key=lambda item: item.name.casefold())
        except PermissionError as exc:
            record_error(rel_dir or None, "permission_denied", str(exc))
            continue
        except OSError as exc:
            record_error(rel_dir or None, "directory_unreadable", str(exc))
            continue

        # Mark coverage only after a successful enumeration: this is the exact
        # evidence reconciliation relies on.
        complete_dirs.add(rel_key_for(rel_dir))

        for child in children:
            if path_key(child.path) in exclude_keys:
                continue

            if _is_link(child):
                stats.skipped_symlinks += 1
                continue

            child_rel = join_rel(rel_dir, child.name)
            try:
                stat_result = child.stat(follow_symlinks=False)
                is_dir = child.is_dir(follow_symlinks=False)
                is_file = child.is_file(follow_symlinks=False)
            except OSError as exc:
                record_error(child_rel, "stat_failed", str(exc))
                continue

            if is_dir:
                entry_type = "dir"
                size_bytes = None
                mtime_ns: int | None = _mtime_ns(stat_result)
                stats.dirs += 1
                stack.append((child.path, child_rel))
            elif is_file:
                entry_type = "file"
                size_bytes = int(stat_result.st_size)
                mtime_ns = _mtime_ns(stat_result)
                stats.files += 1
            else:
                stats.skipped_other += 1
                continue

            if options.max_entries is not None and stats.observed > options.max_entries:
                raise ScanError(
                    f"scan stopped after exceeding max_entries={options.max_entries}; "
                    "raise the limit explicitly if this root is intentionally large"
                )

            buffer.add(
                _EntryRow(
                    rel_path=child_rel,
                    rel_key=rel_key_for(child_rel),
                    parent_rel_path=rel_dir,
                    parent_rel_key=rel_key_for(rel_dir),
                    name=child.name,
                    name_key=child.name.casefold(),
                    extension=extension_of(child.name),
                    entry_type=entry_type,
                    size_bytes=size_bytes,
                    mtime_ns=mtime_ns,
                )
            )
            note_entry()


def _mtime_ns(stat_result: os.stat_result) -> int:
    value = getattr(stat_result, "st_mtime_ns", None)
    if value is not None:
        return int(value)
    return int(stat_result.st_mtime * 1_000_000_000)
