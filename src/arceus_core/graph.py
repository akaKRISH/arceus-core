"""Structural relationships over observed index records.

Only relationships that the scanner actually observed are exposed as facts:

* ``root contains entry`` — the entry's ``root_id`` row
* ``directory contains entry`` — ``parent_rel_key``
* ``entry was observed in scan`` — ``last_seen_run_id`` / ``observed_at``

Ancestor chains are derived from the root-relative path. Each link therefore
states whether it was *observed* as an indexed directory or merely *inferred*
from the path string, so callers never confuse structural inference with
indexed evidence. No semantic edges (imports, similarity, "related files") are
invented in this milestone.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

from arceus_core.errors import PathResolutionError, QueryError
from arceus_core.models import AncestorLink, Entry, ResolvedPath, entry_from_row
from arceus_core.paths import (
    display_path,
    is_within,
    normalize_abs,
    rel_key_for,
    relative_to_root,
)
from arceus_core.roots import find_root_for_path, get_root

MAX_TREE_DEPTH = 12
MAX_TREE_ENTRIES = 5000


def normalize_rel_input(raw_path: str) -> str:
    """Normalize a user-supplied root-relative path to 'a/b/c' form."""

    text = raw_path.strip().replace("\\", "/")
    if text in ("", "."):
        return ""
    parts: list[str] = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise PathResolutionError("relative paths must not contain '..'")
        parts.append(part)
    return "/".join(parts)


def resolve_path(
    conn: sqlite3.Connection,
    raw_path: str,
    *,
    root_identifier: str | int | None = None,
) -> ResolvedPath:
    """Map an absolute path (or a root-relative path with --root) to one root."""

    text = raw_path.strip()
    if not text:
        raise PathResolutionError("path must not be empty")

    if root_identifier is not None:
        root = get_root(conn, root_identifier)
        if os.path.isabs(text):
            if not is_within(text, root.path):
                raise PathResolutionError(
                    f"path is outside root #{root.id} ({root.path}): {normalize_abs(text)}"
                )
            absolute = normalize_abs(text)
        else:
            absolute = display_path(root.path, normalize_rel_input(text))
        rel_path = relative_to_root(absolute, root.path)
        return ResolvedPath(root=root, rel_path=rel_path, absolute_path=absolute)

    if not os.path.isabs(text):
        raise PathResolutionError(
            "provide an absolute path, or a root-relative path together with --root"
        )
    absolute = normalize_abs(text)
    root = find_root_for_path(conn, absolute)
    if root is None:
        raise PathResolutionError(
            f"path is not inside any registered root: {absolute}; run 'arceus roots list'"
        )
    return ResolvedPath(
        root=root,
        rel_path=relative_to_root(absolute, root.path),
        absolute_path=absolute,
    )


def lookup_entry(conn: sqlite3.Connection, root_id: int, rel_path: str) -> Entry | None:
    """Return the indexed row for a relative path, present or missing."""

    row = conn.execute(
        "SELECT * FROM entries WHERE root_id = ? AND rel_key = ?",
        (root_id, rel_key_for(rel_path)),
    ).fetchone()
    return entry_from_row(row) if row is not None else None


def children(
    conn: sqlite3.Connection,
    root_id: int,
    rel_path: str,
    *,
    include_missing: bool = False,
) -> list[Entry]:
    """Direct children of a directory according to the index."""

    sql = (
        "SELECT * FROM entries WHERE root_id = ? AND parent_rel_key = ? "
        + ("" if include_missing else "AND status = 'present' ")
        + "ORDER BY CASE WHEN entry_type = 'dir' THEN 0 ELSE 1 END, name_key ASC"
    )
    rows = conn.execute(sql, (root_id, rel_key_for(rel_path))).fetchall()
    return [entry_from_row(row) for row in rows]


def ancestors(conn: sqlite3.Connection, resolved: ResolvedPath) -> list[AncestorLink]:
    """Ancestor chain from the root to the entry's parent, with observation status.

    Links are marked ``observed`` only when the index contains a directory row
    for that path. The root itself is always observed because it is registered.
    """

    if resolved.rel_path == "":
        return [
            AncestorLink(
                rel_path="",
                display_name=resolved.root.path,
                observed=True,
                entry_type="dir",
            )
        ]

    chain: list[AncestorLink] = [
        AncestorLink(
            rel_path="",
            display_name=resolved.root.path,
            observed=True,
            entry_type="dir",
        )
    ]
    parts = resolved.rel_path.split("/")
    for depth in range(1, len(parts)):
        prefix = "/".join(parts[:depth])
        entry = lookup_entry(conn, resolved.root.id, prefix)
        chain.append(
            AncestorLink(
                rel_path=prefix,
                display_name=parts[depth - 1],
                observed=entry is not None and entry.entry_type == "dir",
                entry_type="dir" if entry is None else entry.entry_type,
            )
        )
    return chain


def subtree(
    conn: sqlite3.Connection,
    root_id: int,
    rel_path: str,
    *,
    max_depth: int = 2,
    limit: int = 500,
) -> tuple[list[tuple[int, Entry]], bool]:
    """Breadth-first listing below a directory. Returns (rows, truncated)."""

    if max_depth < 0 or max_depth > MAX_TREE_DEPTH:
        raise QueryError(f"depth must be between 0 and {MAX_TREE_DEPTH}")
    if limit < 1 or limit > MAX_TREE_ENTRIES:
        raise QueryError(f"tree limit must be between 1 and {MAX_TREE_ENTRIES}")

    results: list[tuple[int, Entry]] = []
    frontier: list[tuple[str, int]] = [(rel_path, 0)]
    truncated = False

    while frontier:
        current, depth = frontier.pop(0)
        if depth >= max_depth:
            continue
        for child in children(conn, root_id, current):
            if len(results) >= limit:
                return results, True
            results.append((depth + 1, child))
            if child.entry_type == "dir":
                frontier.append((child.rel_path, depth + 1))
    return results, truncated


@dataclass(frozen=True, slots=True)
class GraphStats:
    root_id: int
    files: int
    dirs: int
    missing: int
    max_depth: int


def graph_stats(conn: sqlite3.Connection, root_id: int) -> GraphStats:
    row = conn.execute(
        "SELECT "
        " SUM(CASE WHEN entry_type = 'file' AND status = 'present' THEN 1 ELSE 0 END) AS files,"
        " SUM(CASE WHEN entry_type = 'dir' AND status = 'present' THEN 1 ELSE 0 END) AS dirs,"
        " SUM(CASE WHEN status = 'missing' THEN 1 ELSE 0 END) AS missing"
        " FROM entries WHERE root_id = ?",
        (root_id,),
    ).fetchone()

    files = int(row["files"] or 0) if row is not None else 0
    dirs = int(row["dirs"] or 0) if row is not None else 0
    missing = int(row["missing"] or 0) if row is not None else 0

    depth_row = conn.execute(
        "SELECT MAX(LENGTH(rel_path) - LENGTH(REPLACE(rel_path, '/', ''))) "
        "FROM entries WHERE root_id = ? AND status = 'present'",
        (root_id,),
    ).fetchone()
    has_depth = depth_row is not None and depth_row[0] is not None
    max_depth = int(depth_row[0] or 0) + 1 if has_depth else 0
    return GraphStats(root_id=root_id, files=files, dirs=dirs, missing=missing, max_depth=max_depth)
