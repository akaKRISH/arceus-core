"""Shared retriever interface and result type for the retrieval evaluation."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from arceus_core.errors import ArceusError
from arceus_core.models import Root, SearchHit
from arceus_core.search import SearchFilters, search
from evals.config import EVAL_SEARCH_LIMIT
from evals.tasks import RetrievalSpec, indexed_label


@dataclass(frozen=True, slots=True)
class Retrieval:
    targets: tuple[str, ...]
    evidence: frozenset[str]
    notes: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


def _hits_to_retrieval(hits: list[SearchHit]) -> Retrieval:
    targets = tuple(hit.entry.rel_path for hit in hits)
    evidence = frozenset(indexed_label(path) for path in targets)
    return Retrieval(targets=targets, evidence=evidence)


def _error_retrieval(exc: Exception) -> Retrieval:
    return Retrieval(
        targets=(),
        evidence=frozenset(),
        notes=f"retrieval error: {type(exc).__name__}: {exc}",
    )


class Retriever:
    """Shared interface and error containment for measured retrieval."""

    name = "base"

    def retrieve(self, conn: sqlite3.Connection, root: Root, task: RetrievalSpec) -> Retrieval:
        try:
            return self._retrieve(conn, root, task)
        except ArceusError as exc:
            # A retriever failure is a measured outcome, not an evaluator crash.
            return _error_retrieval(exc)

    def _retrieve(
        self, conn: sqlite3.Connection, root: Root, task: RetrievalSpec
    ) -> Retrieval:  # pragma: no cover - abstract
        raise NotImplementedError

    def _search(
        self,
        conn: sqlite3.Connection,
        root: Root,
        task: RetrievalSpec,
        *,
        text: str | None = None,
        include_missing: bool = False,
        extension: str | None = None,
    ) -> Retrieval:
        result = search(
            conn,
            text if text is not None else task.baseline_text,
            SearchFilters(
                root_id=root.id,
                extension=extension,
                include_missing=include_missing,
                limit=EVAL_SEARCH_LIMIT,
            ),
        )
        return _hits_to_retrieval(result.hits)


__all__ = ["Retrieval", "Retriever"]
