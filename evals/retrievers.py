"""Path-only baseline, graph-assisted system, and shared retriever registry.

All three systems receive exactly the same ``RetrievalSpec`` objects and operate
against the same index. The path-only system uses the production ``search`` API.
The graph system adds the production structural query functions. The
path-derived system is implemented in :mod:`evals.path_derived` and uses scalar
metadata plus string filters, never the graph API.
"""

from __future__ import annotations

import sqlite3

from arceus_core.graph import (
    ancestors,
    children,
    lookup_entry,
    resolve_path,
    subtree,
)
from arceus_core.models import Entry, Root
from arceus_core.search import SearchFilters, search
from evals.config import EVAL_SEARCH_LIMIT
from evals.path_derived import PathDerivedRetriever
from evals.retrieval import Retrieval, Retriever
from evals.tasks import (
    RELATIONSHIP_KINDS,
    SEARCH_KINDS,
    RetrievalSpec,
    contains_edge,
    indexed_label,
    missing_label,
)

__all__ = [
    "GraphAssistedRetriever",
    "PathDerivedRetriever",
    "PathOnlyRetriever",
    "Retrieval",
    "Retriever",
    "default_retrievers",
]


class PathOnlyRetriever(Retriever):
    """Existing name/path search only; no structural traversal."""

    name = "path_only"

    def _retrieve(self, conn: sqlite3.Connection, root: Root, task: RetrievalSpec) -> Retrieval:
        if task.kind in SEARCH_KINDS:
            return self._search(conn, root, task)

        if task.kind == "nested_path":
            path = str(task.params["path"])
            hits = self._search(conn, root, task, text=path).targets
            targets = tuple(hit for hit in hits if hit == path)
            return Retrieval(targets, frozenset(indexed_label(item) for item in targets))

        if task.kind == "missing":
            path = str(task.params["path"])
            hits = search(
                conn,
                task.baseline_text,
                SearchFilters(
                    root_id=root.id,
                    include_missing=True,
                    limit=EVAL_SEARCH_LIMIT,
                ),
            ).hits
            targets = tuple(hit.entry.rel_path for hit in hits if hit.entry.rel_path == path)
            evidence = frozenset(
                missing_label(hit.entry.rel_path)
                if hit.entry.status == "missing"
                else indexed_label(hit.entry.rel_path)
                for hit in hits
                if hit.entry.rel_path == path
            )
            return Retrieval(targets, evidence)

        if task.kind in RELATIONSHIP_KINDS:
            # Extension is a documented search filter; the baseline cannot
            # enumerate a directory to apply parent or depth scoping.
            extension = task.params.get("extension")
            return self._search(
                conn,
                root,
                task,
                extension=str(extension) if extension else None,
            )

        raise ValueError(f"unsupported task kind for path-only baseline: {task.kind}")


class GraphAssistedRetriever(Retriever):
    """Filename/path search plus the production structural graph queries."""

    name = "graph_assisted"

    def _resolve(self, conn: sqlite3.Connection, root: Root, rel_path: str):
        return resolve_path(conn, rel_path, root_identifier=root.id)

    def _retrieve(self, conn: sqlite3.Connection, root: Root, task: RetrievalSpec) -> Retrieval:
        kind = task.kind
        if kind in SEARCH_KINDS:
            return self._search(conn, root, task)

        if kind in ("nested_path", "missing"):
            resolved = self._resolve(conn, root, str(task.params["path"]))
            entry: Entry | None = lookup_entry(conn, root.id, resolved.rel_path)
            if entry is None:
                return Retrieval((), frozenset(), notes="path is not present in the index")
            label = missing_label if entry.status == "missing" else indexed_label
            return Retrieval(
                (entry.rel_path,),
                frozenset({label(entry.rel_path)}),
                metadata={"method": "graph_api"},
            )

        if kind == "children":
            resolved = self._resolve(conn, root, str(task.params["directory"]))
            entries = children(conn, root.id, resolved.rel_path)
            targets = tuple(sorted(entry.rel_path for entry in entries))
            evidence = frozenset(
                contains_edge(resolved.rel_path, entry.rel_path) for entry in entries
            )
            return Retrieval(targets, evidence, metadata={"method": "graph_api"})

        if kind == "subtree":
            resolved = self._resolve(conn, root, str(task.params["directory"]))
            rows, truncated = subtree(
                conn,
                root.id,
                resolved.rel_path,
                max_depth=int(task.params["depth"]),
            )
            targets = tuple(sorted(entry.rel_path for _, entry in rows))
            evidence = frozenset(
                contains_edge(entry.parent_rel_path, entry.rel_path) for _, entry in rows
            )
            notes = "traversal truncated at the evaluator limit" if truncated else ""
            return Retrieval(
                targets,
                evidence,
                notes=notes,
                metadata={"method": "graph_api"},
            )

        if kind == "ancestors":
            rel_path = str(task.params["file"])
            resolved = self._resolve(conn, root, rel_path)
            chain = ancestors(conn, resolved)
            ancestor_dirs = tuple(link.rel_path for link in chain if link.rel_path)
            nodes = ["", *ancestor_dirs, resolved.rel_path]
            evidence = frozenset(
                contains_edge(nodes[index], nodes[index + 1]) for index in range(len(nodes) - 1)
            )
            return Retrieval(
                ancestor_dirs,
                evidence,
                metadata={"method": "graph_api"},
            )

        if kind == "extension_in_directory":
            resolved = self._resolve(conn, root, str(task.params["directory"]))
            suffix = str(task.params["extension"]).lower().lstrip(".")
            entries = [
                entry
                for entry in children(conn, root.id, resolved.rel_path)
                if entry.entry_type == "file" and entry.extension == suffix
            ]
            targets = tuple(sorted(entry.rel_path for entry in entries))
            evidence = frozenset(
                contains_edge(resolved.rel_path, entry.rel_path) for entry in entries
            )
            return Retrieval(targets, evidence, metadata={"method": "graph_api"})

        raise ValueError(f"unsupported task kind for graph-assisted retrieval: {kind}")


def default_retrievers() -> list[Retriever]:
    return [PathOnlyRetriever(), PathDerivedRetriever(), GraphAssistedRetriever()]
