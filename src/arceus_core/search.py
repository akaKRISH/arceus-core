"""Filename and path search over the metadata index.

Ranking is deterministic and explainable; every hit states why it matched:

* 100 — the file/directory name equals the query (case-folded)
* 80  — the name starts with the query
* 60  — the name contains the query
* 30  — only the root-relative path contains the query
* 10  — defensive fallback (should not normally occur)

Case folding happens in Python before SQL, so Unicode matching is consistent
across platforms and does not depend on SQLite's ASCII-only LIKE semantics.
No file contents are read or searched.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from arceus_core.errors import QueryError
from arceus_core.models import Entry, SearchHit, SearchResult, entry_from_row
from arceus_core.paths import display_path

MAX_QUERY_LENGTH = 500
MAX_LIMIT = 1000

_REASON_BY_SCORE = {
    100: "exact name match",
    80: "name prefix match",
    60: "name substring match",
    30: "path substring match",
    10: "weak match",
}


@dataclass(frozen=True, slots=True)
class SearchFilters:
    root_id: int | None = None
    extension: str | None = None
    entry_type: str | None = None
    include_missing: bool = False
    limit: int = 20


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search(
    conn: sqlite3.Connection, query: str, filters: SearchFilters | None = None
) -> SearchResult:
    """Search names and root-relative paths; returns ranked, explained hits."""

    options = filters or SearchFilters()
    needle = query.strip()
    if not needle:
        raise QueryError("search query must not be empty")
    if len(needle) > MAX_QUERY_LENGTH:
        raise QueryError(f"search query is too long (maximum {MAX_QUERY_LENGTH} characters)")
    if options.limit < 1 or options.limit > MAX_LIMIT:
        raise QueryError(f"limit must be between 1 and {MAX_LIMIT}")
    if options.entry_type is not None and options.entry_type not in ("file", "dir"):
        raise QueryError("entry type must be 'file' or 'dir'")

    name_query = needle.casefold()
    path_query = needle.replace("\\", "/").casefold()
    name_contains = f"%{_escape_like(name_query)}%"
    name_prefix = f"{_escape_like(name_query)}%"
    path_contains = f"%{_escape_like(path_query)}%"

    where = []
    params: dict[str, object] = {
        "name_query": name_query,
        "name_contains": name_contains,
        "name_prefix": name_prefix,
        "path_contains": path_contains,
        "limit": options.limit,
    }

    where.append(
        "(e.name_key LIKE :name_contains ESCAPE '\\' OR e.rel_key LIKE :path_contains ESCAPE '\\')"
    )
    if not options.include_missing:
        where.append("e.status = 'present'")
    if options.root_id is not None:
        where.append("e.root_id = :root_id")
        params["root_id"] = options.root_id
    if options.entry_type is not None:
        where.append("e.entry_type = :entry_type")
        params["entry_type"] = options.entry_type
    if options.extension:
        extension = options.extension.strip().lower().lstrip(".")
        if not extension:
            raise QueryError("extension filter must not be empty")
        where.append("e.extension = :extension")
        params["extension"] = extension

    sql = f"""
        SELECT e.*, r.path AS root_path,
            CASE
                WHEN e.name_key = :name_query THEN 100
                WHEN e.name_key LIKE :name_prefix ESCAPE '\\' THEN 80
                WHEN e.name_key LIKE :name_contains ESCAPE '\\' THEN 60
                WHEN e.rel_key LIKE :path_contains ESCAPE '\\' THEN 30
                ELSE 10
            END AS match_score
        FROM entries e
        JOIN roots r ON r.id = e.root_id
        WHERE {" AND ".join(where)}
        ORDER BY match_score DESC, e.name_key ASC, e.rel_key ASC
        LIMIT :limit
    """

    rows = conn.execute(sql, params).fetchall()
    hits: list[SearchHit] = []
    for row in rows:
        entry: Entry = entry_from_row(row)
        score = int(row["match_score"])
        hits.append(
            SearchHit(
                entry=entry,
                root_path=str(row["root_path"]),
                absolute_path=display_path(str(row["root_path"]), entry.rel_path),
                score=score,
                reason=_REASON_BY_SCORE.get(score, "weak match"),
            )
        )

    warnings = _collect_warnings(conn, hits, options)
    return SearchResult(query=needle, hits=hits, warnings=warnings, limit=options.limit)


def _collect_warnings(
    conn: sqlite3.Connection, hits: list[SearchHit], filters: SearchFilters
) -> list[str]:
    warnings: list[str] = []

    root_rows = conn.execute("SELECT COUNT(*) FROM roots").fetchone()
    if root_rows is None or int(root_rows[0]) == 0:
        return ["no indexing roots are registered; add one with 'arceus roots add <directory>'"]

    root_ids = {hit.entry.root_id for hit in hits}
    if not root_ids and filters.root_id is not None:
        root_ids = {filters.root_id}

    if root_ids:
        placeholders = ",".join("?" for _ in root_ids)
        rows = conn.execute(
            f"SELECT r.id, r.path, r.status AS root_status, "
            f"sr.status AS run_status, sr.finished_at "
            f"FROM roots r LEFT JOIN scan_runs sr ON sr.id = r.last_scan_run_id "
            f"WHERE r.id IN ({placeholders})",
            tuple(sorted(root_ids)),
        ).fetchall()
        for row in rows:
            run_status = row["run_status"]
            if row["root_status"] != "active":
                warnings.append(
                    f"root #{int(row['id'])} is marked {row['root_status']}: {row['path']}"
                )
            if run_status is None:
                warnings.append(f"root #{int(row['id'])} has never been scanned: {row['path']}")
            elif run_status != "completed":
                warnings.append(
                    f"root #{int(row['id'])} last scan is {run_status} "
                    f"(finished {row['finished_at']}); results may be incomplete or stale"
                )

    if not hits:
        warnings.append(
            "no matching entries; this search covers names and root-relative paths only, "
            "not file contents"
        )
    return warnings
