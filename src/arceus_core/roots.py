"""Registered indexing roots: the only directories ARCEUS may scan.

Registration is explicit and validated. The registry never records a path that
is not an existing directory, and it stores a case-normalized key so duplicate
registration is rejected on case-insensitive filesystems.
"""

from __future__ import annotations

import os
import sqlite3

from arceus_core.errors import RootError, RootNotFoundError
from arceus_core.models import Root, root_from_row, utc_now
from arceus_core.paths import is_within, normalize_abs, path_key


def add_root(
    conn: sqlite3.Connection,
    path: str | os.PathLike[str],
    *,
    label: str | None = None,
) -> Root:
    """Register an existing directory as an indexing root."""

    raw = os.fspath(path).strip()
    if not raw:
        raise RootError("root path must not be empty")

    absolute = normalize_abs(raw)
    if not os.path.exists(absolute):
        raise RootError(f"directory does not exist: {absolute}")
    if not os.path.isdir(absolute):
        raise RootError(f"not a directory: {absolute}")

    if label is not None:
        label = label.strip() or None
        if label is not None and len(label) > 120:
            raise RootError("root label must be 120 characters or fewer")

    key = path_key(absolute)
    existing = conn.execute("SELECT * FROM roots WHERE path_key = ?", (key,)).fetchone()
    if existing is not None:
        raise RootError(
            f"directory is already registered as root #{int(existing['id'])}: {existing['path']}"
        )

    try:
        with conn:
            cursor = conn.execute(
                "INSERT INTO roots (path, path_key, label, created_at, status) "
                "VALUES (?, ?, ?, ?, 'active')",
                (absolute, key, label, utc_now()),
            )
    except sqlite3.IntegrityError as exc:  # pragma: no cover - guarded above, race safety
        raise RootError(f"directory is already registered: {absolute}") from exc

    root_id = int(cursor.lastrowid)
    row = conn.execute("SELECT * FROM roots WHERE id = ?", (root_id,)).fetchone()
    assert row is not None
    return root_from_row(row)


def list_roots(conn: sqlite3.Connection) -> list[Root]:
    rows = conn.execute("SELECT * FROM roots ORDER BY id").fetchall()
    return [root_from_row(row) for row in rows]


def get_root(conn: sqlite3.Connection, identifier: str | int) -> Root:
    """Look up a root by numeric ID, registered path, or unique label."""

    if isinstance(identifier, int):
        row = conn.execute("SELECT * FROM roots WHERE id = ?", (identifier,)).fetchone()
        if row is None:
            raise RootNotFoundError(f"no root with id {identifier}")
        return root_from_row(row)

    text = str(identifier).strip()
    if not text:
        raise RootNotFoundError("root identifier must not be empty")

    if text.isdigit():
        root = get_root(conn, int(text))
        return root

    try:
        key = path_key(text)
    except ValueError:
        key = ""
    if key:
        row = conn.execute("SELECT * FROM roots WHERE path_key = ?", (key,)).fetchone()
        if row is not None:
            return root_from_row(row)

    rows = conn.execute("SELECT * FROM roots WHERE label = ?", (text,)).fetchall()
    if len(rows) == 1:
        return root_from_row(rows[0])
    if len(rows) > 1:
        raise RootError(f"label {text!r} matches multiple roots; use the numeric id")

    raise RootNotFoundError(f"no registered root matches {text!r}; run 'arceus roots list'")


def remove_root(conn: sqlite3.Connection, identifier: str | int) -> tuple[Root, int]:
    """Unregister a root and delete its index records (explicit forget)."""

    root = get_root(conn, identifier)
    count_row = conn.execute(
        "SELECT COUNT(*) FROM entries WHERE root_id = ?", (root.id,)
    ).fetchone()
    entry_count = int(count_row[0]) if count_row is not None else 0
    with conn:
        conn.execute("DELETE FROM roots WHERE id = ?", (root.id,))
    return root, entry_count


def set_root_status(conn: sqlite3.Connection, root_id: int, status: str) -> None:
    if status not in ("active", "missing"):
        raise ValueError(f"invalid root status: {status}")
    with conn:
        conn.execute("UPDATE roots SET status = ? WHERE id = ?", (status, root_id))


def set_root_last_scan(conn: sqlite3.Connection, root_id: int, run_id: int) -> None:
    with conn:
        conn.execute("UPDATE roots SET last_scan_run_id = ? WHERE id = ?", (run_id, root_id))


def find_root_for_path(conn: sqlite3.Connection, absolute_path: str) -> Root | None:
    """Return the most specific registered root containing ``absolute_path``."""

    candidate = normalize_abs(absolute_path)
    best: Root | None = None
    for root in list_roots(conn):
        if is_within(candidate, root.path) and (best is None or len(root.path) > len(best.path)):
            best = root
    return best
