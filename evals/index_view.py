"""Read-only metadata view used by the path-derived baseline.

This interface deliberately exposes only scalar index fields. It performs no
traversal, creates no graph objects, and does not call ``arceus_core.graph``.
The path-derived retriever reconstructs parents and scope from ``rel_path``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from evals.config import EVAL_TREE_LIMIT


@dataclass(frozen=True, slots=True)
class IndexRecord:
    rel_path: str
    entry_type: str
    extension: str
    status: str

    @property
    def name(self) -> str:
        return self.rel_path.rsplit("/", 1)[-1]


@dataclass(frozen=True, slots=True)
class MetadataView:
    """Immutable snapshot of one root's scalar index rows."""

    records: tuple[IndexRecord, ...]
    _by_path: dict[str, IndexRecord]

    def get(self, rel_path: str) -> IndexRecord | None:
        return self._by_path.get(rel_path)

    def children(self, directory: str, *, include_missing: bool = False) -> tuple[IndexRecord, ...]:
        """Direct children by parsing each relative path's last separator."""

        prefix = "" if directory == "" else f"{directory}/"
        rows = []
        for record in self.records:
            if not record.rel_path.startswith(prefix):
                continue
            remainder = record.rel_path[len(prefix) :]
            if not remainder or "/" in remainder:
                continue
            if not include_missing and record.status != "present":
                continue
            rows.append(record)
        return tuple(sorted(rows, key=lambda row: row.name.casefold()))

    def subtree(
        self,
        directory: str,
        depth: int,
        *,
        limit: int = EVAL_TREE_LIMIT,
    ) -> tuple[tuple[IndexRecord, ...], bool]:
        """String-scoped descendants with a caller-supplied depth and limit."""

        if depth < 1:
            raise ValueError("subtree depth must be positive")
        prefix = f"{directory}/"
        rows: list[IndexRecord] = []
        truncated = False
        for record in self.records:
            if not record.rel_path.startswith(prefix) or record.status != "present":
                continue
            remainder = record.rel_path[len(prefix) :]
            if remainder.count("/") + 1 > depth:
                continue
            if len(rows) >= limit:
                truncated = True
                break
            rows.append(record)
        return tuple(sorted(rows, key=lambda row: row.rel_path)), truncated


def load_metadata_view(conn: sqlite3.Connection, root_id: int) -> MetadataView:
    """Load scalar metadata for one root with a single SELECT."""

    rows = conn.execute(
        "SELECT rel_path, entry_type, extension, status FROM entries "
        "WHERE root_id = ? ORDER BY rel_path",
        (root_id,),
    ).fetchall()
    records = tuple(
        IndexRecord(
            rel_path=str(row["rel_path"]),
            entry_type=str(row["entry_type"]),
            extension=str(row["extension"]),
            status=str(row["status"]),
        )
        for row in rows
    )
    return MetadataView(records=records, _by_path={record.rel_path: record for record in records})


__all__ = ["IndexRecord", "MetadataView", "load_metadata_view"]
