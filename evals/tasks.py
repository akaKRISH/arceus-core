"""Versioned benchmark tasks and independent ground truth.

Ground truth is derived from the fixture model, not from the production graph
or search implementations. Retrievers receive only ``RetrievalSpec`` objects,
which contain no expected targets or evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from evals.config import TASKS_PER_SEED
from evals.fixture import FixtureModel

CATEGORIES: tuple[str, ...] = (
    "exact_name",
    "partial_name",
    "ambiguous_name",
    "children",
    "subtree",
    "ancestors",
    "extension_in_directory",
    "nested_path",
    "no_result",
    "missing_entry",
)

CATEGORY_COUNTS: dict[str, int] = {
    "exact_name": 4,
    "partial_name": 4,
    "ambiguous_name": 4,
    "children": 4,
    "subtree": 4,
    "ancestors": 3,
    "extension_in_directory": 4,
    "nested_path": 3,
    "no_result": 3,
    "missing_entry": 2,
}

SEARCH_KINDS = frozenset({"search", "no_result"})
RELATIONSHIP_KINDS = frozenset({"children", "subtree", "ancestors", "extension_in_directory"})


@dataclass(frozen=True, slots=True)
class RetrievalSpec:
    """The only task information a retriever receives."""

    id: str
    category: str
    kind: str
    baseline_text: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BenchmarkTask:
    id: str
    category: str
    kind: str
    baseline_text: str
    params: dict[str, Any] = field(default_factory=dict)
    expected_targets: tuple[str, ...] = ()
    expected_evidence: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError(f"unknown category: {self.category}")
        if not self.id:
            raise ValueError("task id must not be empty")
        if not self.baseline_text and self.kind != "missing":
            raise ValueError(f"{self.id}: baseline_text must not be empty")

    def retrieval_spec(self) -> RetrievalSpec:
        """Return a ground-truth-free view for retrieval systems."""

        return RetrievalSpec(
            id=self.id,
            category=self.category,
            kind=self.kind,
            baseline_text=self.baseline_text,
            params=dict(self.params),
        )


def _basename(rel_path: str) -> str:
    return rel_path.rsplit("/", 1)[-1]


def _parent(rel_path: str) -> str:
    return rel_path.rsplit("/", 1)[0] if "/" in rel_path else ""


def contains_edge(parent: str, child: str) -> str:
    """Evidence label for an observed parent→child relation ('' means root)."""

    return f"contains:{parent or '.'}|{child}"


def indexed_label(rel_path: str) -> str:
    return f"indexed:{rel_path}"


def missing_label(rel_path: str) -> str:
    return f"missing:{rel_path}"


def _search_matches(model: FixtureModel, query: str) -> tuple[str, ...]:
    """Name/path substring matches over present entries, case-folded."""

    needle = query.casefold()
    matches = [
        entry
        for entry in model.entries()
        if needle in _basename(entry).casefold() or needle in entry.casefold()
    ]
    return tuple(sorted(matches))


def _children(model: FixtureModel, directory: str) -> tuple[str, ...]:
    return tuple(sorted(path for path in model.entries() if _parent(path) == directory))


def _subtree(model: FixtureModel, directory: str, depth: int) -> tuple[str, ...]:
    if depth < 1:
        raise ValueError("subtree depth must be positive")
    prefix = f"{directory}/"
    return tuple(
        sorted(
            path
            for path in model.entries()
            if path.startswith(prefix) and path[len(prefix) :].count("/") + 1 <= depth
        )
    )


def _ancestors(model: FixtureModel, rel_path: str) -> tuple[str, ...]:
    chain: list[str] = []
    parent = _parent(rel_path)
    while parent:
        chain.append(parent)
        parent = _parent(parent)
    return tuple(reversed(chain))


def _files_with_extension(model: FixtureModel, directory: str, extension: str) -> tuple[str, ...]:
    suffix = extension.lower().lstrip(".")
    return tuple(
        path
        for path in _children(model, directory)
        if path in model.files and path.rsplit(".", 1)[-1].lower() == suffix
    )


def _edges_for_targets(model: FixtureModel, targets: tuple[str, ...]) -> frozenset[str]:
    return frozenset(contains_edge(_parent(path), path) for path in targets)


def _ancestor_evidence(model: FixtureModel, rel_path: str) -> frozenset[str]:
    nodes = ["", *_ancestors(model, rel_path), rel_path]
    return frozenset(
        contains_edge(nodes[index], nodes[index + 1]) for index in range(len(nodes) - 1)
    )


def build_tasks(model: FixtureModel) -> tuple[BenchmarkTask, ...]:
    """Build the fixed v2 task suite over one fixture model."""

    tasks: list[BenchmarkTask] = []
    layout = model.layout

    def add(
        task_id: str,
        category: str,
        kind: str,
        baseline_text: str,
        params: dict[str, Any],
        expected_targets: tuple[str, ...],
        expected_evidence: frozenset[str],
    ) -> None:
        tasks.append(
            BenchmarkTask(
                id=task_id,
                category=category,
                kind=kind,
                baseline_text=baseline_text,
                params=params,
                expected_targets=expected_targets,
                expected_evidence=expected_evidence,
            )
        )

    def add_search(
        task_id: str,
        category: str,
        query: str,
        *,
        exact_count: int | None = None,
        minimum_count: int = 1,
    ) -> None:
        expected = _search_matches(model, query)
        if len(expected) < minimum_count:
            raise ValueError(
                f"{task_id}: expected at least {minimum_count} matches for {query!r}, "
                f"got {len(expected)}"
            )
        if exact_count is not None and len(expected) != exact_count:
            raise ValueError(
                f"{task_id}: expected {exact_count} matches for {query!r}, got {len(expected)}"
            )
        add(
            task_id,
            category,
            "search",
            query,
            {"query": query},
            expected,
            frozenset(indexed_label(path) for path in expected),
        )

    def add_children(task_id: str, directory: str) -> None:
        expected = _children(model, directory)
        if not expected:
            raise ValueError(f"{task_id}: no children for {directory}")
        add(
            task_id,
            "children",
            "children",
            _basename(directory),
            {"directory": directory},
            expected,
            _edges_for_targets(model, expected),
        )

    def add_subtree(task_id: str, directory: str, depth: int) -> None:
        expected = _subtree(model, directory, depth)
        if not expected:
            raise ValueError(f"{task_id}: empty subtree for {directory}")
        add(
            task_id,
            "subtree",
            "subtree",
            _basename(directory),
            {"directory": directory, "depth": depth},
            expected,
            _edges_for_targets(model, expected),
        )

    def add_ancestors(task_id: str, rel_path: str) -> None:
        expected = _ancestors(model, rel_path)
        if not expected:
            raise ValueError(f"{task_id}: no ancestors for {rel_path}")
        add(
            task_id,
            "ancestors",
            "ancestors",
            _basename(rel_path),
            {"file": rel_path},
            expected,
            _ancestor_evidence(model, rel_path),
        )

    def add_extension(task_id: str, directory: str, extension: str) -> None:
        expected = _files_with_extension(model, directory, extension)
        if not expected:
            raise ValueError(f"{task_id}: no .{extension} files in {directory}")
        add(
            task_id,
            "extension_in_directory",
            "extension_in_directory",
            _basename(directory),
            {"directory": directory, "extension": extension},
            expected,
            _edges_for_targets(model, expected),
        )

    def add_nested_path(task_id: str, rel_path: str) -> None:
        if rel_path not in model.files:
            raise ValueError(f"{task_id}: nested-path target is absent: {rel_path}")
        add(
            task_id,
            "nested_path",
            "nested_path",
            rel_path,
            {"path": rel_path},
            (rel_path,),
            frozenset({indexed_label(rel_path)}),
        )

    def add_no_result(task_id: str, query: str) -> None:
        if _search_matches(model, query):
            raise ValueError(f"{task_id}: no-result query unexpectedly matches: {query!r}")
        add(
            task_id,
            "no_result",
            "no_result",
            query,
            {"query": query},
            (),
            frozenset(),
        )

    def add_missing(task_id: str, rel_path: str) -> None:
        if rel_path not in model.missing_files:
            raise ValueError(f"{task_id}: {rel_path} is not a planned missing file")
        add(
            task_id,
            "missing_entry",
            "missing",
            _basename(rel_path),
            {"path": rel_path},
            (rel_path,),
            frozenset({missing_label(rel_path)}),
        )

    # 1. Exact filename lookup (each query has exactly one present match).
    add_search(
        "exact-01-unique",
        "exact_name",
        _basename(layout.unique_file),
        exact_count=1,
    )
    add_search(
        "exact-02-singleton",
        "exact_name",
        _basename(layout.singleton_file),
        exact_count=1,
    )
    add_search(
        "exact-03-budget",
        "exact_name",
        _basename(layout.budget_file),
        exact_count=1,
    )
    add_search(
        "exact-04-test",
        "exact_name",
        _basename(layout.test_file),
        exact_count=1,
    )

    # 2. Partial filename lookup.
    add_search("partial-01-photos", "partial_name", "photo-000", exact_count=3)
    add_search("partial-02-util", "partial_name", "util", exact_count=1)
    add_search("partial-03-notes", "partial_name", "notes", exact_count=3)
    add_search("partial-04-readme", "partial_name", "README", exact_count=3)

    # 3. Ambiguous names in different directories.
    add_search(
        "ambiguous-01-report",
        "ambiguous_name",
        layout.report_name,
        exact_count=3,
    )
    add_search(
        "ambiguous-02-notes",
        "ambiguous_name",
        f"{layout.note_stem}.md",
        exact_count=2,
    )
    add_search("ambiguous-03-readme", "ambiguous_name", "README.md", exact_count=3)
    add_search(
        "ambiguous-04-settings",
        "ambiguous_name",
        layout.settings_name,
        exact_count=2,
    )

    # 4. Direct children.
    add_children("children-01-docs", layout.docs)
    add_children(
        "children-02-module",
        f"{layout.project_root}/{layout.project}/{layout.module}",
    )
    add_children("children-03-empty", layout.empty)
    add_children("children-04-project-root", layout.project_root)

    # 5. Bounded descendants. Depths vary with each seed's nesting.
    add_subtree("subtree-01-archive", f"{layout.docs}/{layout.archive}", 2)
    add_subtree("subtree-02-project", f"{layout.project_root}/{layout.project}", 2)
    add_subtree("subtree-03-mixed", layout.mixed, 1)
    add_subtree("subtree-04-photos", layout.photos_root, 3)

    # 6. Ancestor chains and parent evidence.
    add_ancestors("ancestors-01-main", layout.main_file)
    add_ancestors(
        "ancestors-02-report",
        f"{layout.docs}/{layout.archive}/{layout.archive_year}/{layout.report_name}",
    )
    add_ancestors("ancestors-03-gamma", layout.gamma_file)

    # 7. Extension within one directory (direct children only).
    add_extension("extension-01-mixed-py", layout.mixed, "py")
    add_extension(
        "extension-02-project-md",
        f"{layout.project_root}/{layout.project}",
        "md",
    )
    add_extension(
        "extension-03-module-py",
        f"{layout.project_root}/{layout.project}/{layout.module}",
        "py",
    )
    add_extension(
        "extension-04-trip-jpg",
        f"{layout.photos_root}/{layout.photo_year}/{layout.trip}",
        "jpg",
    )

    # 8. Exact nested path resolution.
    add_nested_path("nested-01-data", layout.data_file)
    add_nested_path("nested-02-unique", layout.unique_file)
    add_nested_path(
        "nested-03-report",
        f"{layout.docs}/{layout.archive}/{layout.archive_year}/{layout.report_name}",
    )

    # 9. No-result behavior.
    add_no_result("noresult-01-token", "zzzz-absent-token-987")
    add_no_result("noresult-02-extension", "report.docx")
    add_no_result("noresult-03-prefix", "absent-prefix-000")

    # 10. Entries observed missing after a later scan.
    add_missing("missing-01-obsolete", layout.obsolete_file)
    add_missing("missing-02-removed", layout.removed_file)

    ids = [task.id for task in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate task ids in benchmark suite")
    if len(tasks) != TASKS_PER_SEED:
        raise ValueError(f"task suite has {len(tasks)} tasks, expected {TASKS_PER_SEED}")

    counts = {category: 0 for category in CATEGORIES}
    for task in tasks:
        counts[task.category] += 1
    if counts != CATEGORY_COUNTS:
        raise ValueError(f"category counts differ from the frozen definition: {counts}")

    return tuple(tasks)


__all__ = [
    "CATEGORIES",
    "CATEGORY_COUNTS",
    "RELATIONSHIP_KINDS",
    "SEARCH_KINDS",
    "BenchmarkTask",
    "RetrievalSpec",
    "build_tasks",
    "contains_edge",
    "indexed_label",
    "missing_label",
]
