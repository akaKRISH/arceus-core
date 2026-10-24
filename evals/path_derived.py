"""Path-derived baseline.

This retriever never calls the production graph API. It loads scalar metadata
through :mod:`evals.index_view` and reconstructs parent, depth, descendant, and
directory-scope relationships by parsing normalized root-relative paths. It is
the strong non-graph control for the claim that structural retrieval needs the
graph index rather than string manipulation over the same metadata.
"""

from __future__ import annotations

import sqlite3

from arceus_core.models import Root
from evals.index_view import IndexRecord, load_metadata_view
from evals.retrieval import Retrieval, Retriever
from evals.tasks import (
    SEARCH_KINDS,
    RetrievalSpec,
    contains_edge,
    indexed_label,
    missing_label,
)


def _parent(rel_path: str) -> str:
    index = rel_path.rfind("/")
    return "" if index < 0 else rel_path[:index]


def _ancestor_paths(rel_path: str) -> tuple[str, ...]:
    parts = rel_path.split("/")
    return tuple("/".join(parts[:depth]) for depth in range(1, len(parts)))


def _with_edges(records: tuple[IndexRecord, ...]) -> Retrieval:
    targets = tuple(sorted(record.rel_path for record in records))
    evidence = frozenset(contains_edge(_parent(path), path) for path in targets)
    return Retrieval(
        targets,
        evidence,
        metadata={"method": "path_string_filters"},
    )


class PathDerivedRetriever(Retriever):
    """Structural filters implemented only with strings over indexed metadata."""

    name = "path_derived"

    def _retrieve(self, conn: sqlite3.Connection, root: Root, task: RetrievalSpec) -> Retrieval:
        if task.kind in SEARCH_KINDS:
            return self._search(conn, root, task)

        view = load_metadata_view(conn, root.id)

        if task.kind == "nested_path":
            path = str(task.params["path"])
            record = view.get(path)
            if record is None:
                return Retrieval((), frozenset(), notes="path is not in the index")
            label = missing_label if record.status == "missing" else indexed_label
            return Retrieval(
                (path,),
                frozenset({label(path)}),
                metadata={"method": "path_string_filters"},
            )

        if task.kind == "missing":
            path = str(task.params["path"])
            record = view.get(path)
            if record is None or record.status != "missing":
                return Retrieval((), frozenset(), notes="no missing index row for path")
            return Retrieval(
                (path,),
                frozenset({missing_label(path)}),
                metadata={"method": "path_string_filters"},
            )

        if task.kind == "children":
            directory = str(task.params["directory"])
            return _with_edges(view.children(directory))

        if task.kind == "subtree":
            directory = str(task.params["directory"])
            depth = int(task.params["depth"])
            rows, truncated = view.subtree(directory, depth)
            result = _with_edges(rows)
            if truncated:
                return Retrieval(
                    result.targets,
                    result.evidence,
                    notes="traversal truncated at the evaluator limit",
                    metadata=result.metadata,
                )
            return result

        if task.kind == "ancestors":
            path = str(task.params["file"])
            targets = _ancestor_paths(path)
            nodes = ["", *targets, path]
            evidence = frozenset(
                contains_edge(nodes[index], nodes[index + 1]) for index in range(len(nodes) - 1)
            )
            return Retrieval(
                targets,
                evidence,
                metadata={"method": "path_string_filters"},
            )

        if task.kind == "extension_in_directory":
            directory = str(task.params["directory"])
            suffix = str(task.params["extension"]).lower().lstrip(".")
            rows = tuple(
                record
                for record in view.children(directory)
                if record.entry_type == "file" and record.extension == suffix
            )
            return _with_edges(rows)

        raise ValueError(f"unsupported task kind for path-derived retrieval: {task.kind}")


__all__ = ["PathDerivedRetriever"]
