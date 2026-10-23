from __future__ import annotations

from pathlib import Path

import pytest

from arceus_core.errors import PathResolutionError, QueryError
from arceus_core.graph import (
    ancestors,
    children,
    graph_stats,
    lookup_entry,
    normalize_rel_input,
    resolve_path,
    subtree,
)
from arceus_core.roots import add_root
from arceus_core.scanner import scan_root


def test_children_are_direct_and_dirs_first(conn, root_dir: Path, make_files, root) -> None:
    make_files(root_dir, {"alpha/z.txt": "x", "alpha/b.txt": "y", "top.txt": "z"})
    scan_root(conn, root)

    entries = children(conn, root.id, "alpha")
    assert [entry.name for entry in entries] == ["b.txt", "z.txt"]
    top = children(conn, root.id, "")
    assert [entry.name for entry in top] == ["alpha", "top.txt"]


def test_ancestors_distinguish_observed_and_inferred(
    conn, root_dir: Path, make_files, root
) -> None:
    make_files(root_dir, {"real/nested/file.txt": "x"})
    scan_root(conn, root)

    observed = ancestors(conn, resolve_path(conn, str(root_dir / "real" / "nested" / "file.txt")))
    assert observed[0].observed is True
    assert observed[1].display_name == "real"
    assert observed[1].observed is True
    assert observed[2].display_name == "nested"
    assert observed[2].observed is True

    inferred = ancestors(conn, resolve_path(conn, str(root_dir / "never" / "seen" / "file.txt")))
    assert inferred[1].display_name == "never"
    assert inferred[1].observed is False
    assert inferred[2].observed is False


def test_subtree_respects_depth_and_limit(conn, root_dir: Path, make_files, root) -> None:
    make_files(root_dir, {"a/b/c/deep.txt": "x", "a/file.txt": "y"})
    scan_root(conn, root)

    depth_one, _ = subtree(conn, root.id, "", max_depth=1)
    assert [entry.name for _, entry in depth_one] == ["a"]

    depth_two, truncated = subtree(conn, root.id, "", max_depth=2)
    assert (2, "a/file.txt") in [(depth, entry.rel_path) for depth, entry in depth_two] or any(
        entry.rel_path == "a/file.txt" for _, entry in depth_two
    )
    assert not truncated

    limited, limited_truncated = subtree(conn, root.id, "", max_depth=5, limit=1)
    assert len(limited) == 1
    assert limited_truncated is True


def test_subtree_depth_validation(conn, root, root_dir: Path) -> None:
    with pytest.raises(QueryError):
        subtree(conn, root.id, "", max_depth=99)
    with pytest.raises(QueryError):
        subtree(conn, root.id, "", limit=0)


def test_resolve_relative_with_root(conn, root_dir: Path, make_files, root) -> None:
    make_files(root_dir, {"sub/file.txt": "x"})
    scan_root(conn, root)

    resolved = resolve_path(conn, r"sub\file.txt", root_identifier=root.id)
    assert resolved.rel_path == "sub/file.txt"
    assert resolved.absolute_path == str(root_dir / "sub" / "file.txt")


def test_resolve_absolute_without_root(conn, root_dir: Path, root) -> None:
    resolved = resolve_path(conn, str(root_dir / "x" / "y.txt"))
    assert resolved.root.id == root.id
    assert resolved.rel_path == "x/y.txt"


def test_resolve_rejects_outside_root(conn, root_dir: Path, tmp_path: Path, root) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(PathResolutionError):
        resolve_path(conn, str(outside), root_identifier=root.id)
    with pytest.raises(PathResolutionError):
        resolve_path(conn, str(outside))


def test_resolve_requires_absolute_without_root(conn, root) -> None:
    with pytest.raises(PathResolutionError):
        resolve_path(conn, "relative/path")


def test_nested_roots_choose_most_specific(conn, root_dir: Path, make_files) -> None:
    nested = root_dir / "inner"
    nested.mkdir()
    make_files(nested, {"file.txt": "x"})
    add_root(conn, root_dir)
    inner = add_root(conn, nested)

    resolved = resolve_path(conn, str(nested / "file.txt"))
    assert resolved.root.id == inner.id
    assert resolved.rel_path == "file.txt"


def test_normalize_rel_input() -> None:
    assert normalize_rel_input("") == ""
    assert normalize_rel_input(".") == ""
    assert normalize_rel_input("./a//b/") == "a/b"
    assert normalize_rel_input("a\\b") == "a/b"
    with pytest.raises(PathResolutionError):
        normalize_rel_input("../escape")


def test_lookup_entry_returns_none_for_unknown(conn, root_dir: Path, make_files, root) -> None:
    make_files(root_dir, {"a.txt": "x"})
    scan_root(conn, root)
    assert lookup_entry(conn, root.id, "a.txt") is not None
    assert lookup_entry(conn, root.id, "missing.txt") is None


def test_graph_stats_counts_and_depth(conn, root_dir: Path, make_files, root) -> None:
    make_files(root_dir, {"a/b/c.txt": "x", "top.txt": "y"})
    scan_root(conn, root)
    stats = graph_stats(conn, root.id)
    assert stats.files == 2
    assert stats.dirs == 2
    assert stats.missing == 0
    assert stats.max_depth == 3
