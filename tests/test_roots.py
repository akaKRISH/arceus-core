from __future__ import annotations

import os
from pathlib import Path

import pytest

from arceus_core.errors import RootError, RootNotFoundError
from arceus_core.paths import normalize_abs
from arceus_core.roots import (
    add_root,
    find_root_for_path,
    get_root,
    list_roots,
    remove_root,
    set_root_status,
)
from arceus_core.scanner import scan_root


def test_add_and_list_root(conn, root_dir: Path) -> None:
    root = add_root(conn, root_dir, label="fixture")
    assert root.path == normalize_abs(root_dir)
    assert root.label == "fixture"
    assert root.status == "active"
    roots = list_roots(conn)
    assert [item.id for item in roots] == [root.id]


def test_duplicate_root_is_rejected(conn, root_dir: Path) -> None:
    add_root(conn, root_dir)
    with pytest.raises(RootError) as excinfo:
        add_root(conn, root_dir)
    assert "already registered" in str(excinfo.value)


@pytest.mark.skipif(os.name != "nt", reason="case-insensitive duplicate check is Windows-specific")
def test_case_variant_duplicate_is_rejected_on_windows(conn, root_dir: Path) -> None:
    add_root(conn, root_dir)
    with pytest.raises(RootError):
        add_root(conn, str(root_dir).upper())


def test_registration_requires_existing_directory(conn, tmp_path: Path) -> None:
    with pytest.raises(RootError):
        add_root(conn, tmp_path / "does-not-exist")


def test_registration_rejects_files(conn, tmp_path: Path) -> None:
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(RootError):
        add_root(conn, file_path)


def test_lookup_by_id_path_and_label(conn, root_dir: Path, tmp_path: Path) -> None:
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    first = add_root(conn, root_dir, label="first")
    second = add_root(conn, other_dir, label="second")

    assert get_root(conn, first.id).id == first.id
    assert get_root(conn, str(first.id)).id == first.id
    assert get_root(conn, first.path).id == first.id
    assert get_root(conn, "second").id == second.id


def test_unknown_root_raises(conn) -> None:
    with pytest.raises(RootNotFoundError):
        get_root(conn, 12345)
    with pytest.raises(RootNotFoundError):
        get_root(conn, "no such label")


def test_ambiguous_label_is_rejected(conn, root_dir: Path, tmp_path: Path) -> None:
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    add_root(conn, root_dir, label="same")
    add_root(conn, other_dir, label="same")
    with pytest.raises(RootError):
        get_root(conn, "same")


def test_remove_root_deletes_index_records_but_not_files(conn, root_dir: Path, make_files) -> None:
    make_files(root_dir, {"keep.txt": "original"})
    root = add_root(conn, root_dir)
    scan_root(conn, root)

    removed, count = remove_root(conn, root.id)
    assert removed.id == root.id
    assert count == 1
    assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0
    assert (root_dir / "keep.txt").read_text(encoding="utf-8") == "original"


def test_find_root_for_path_picks_most_specific(conn, root_dir: Path) -> None:
    nested = root_dir / "nested"
    nested.mkdir()
    parent = add_root(conn, root_dir)
    child = add_root(conn, nested)
    found = find_root_for_path(conn, str(nested / "file.txt"))
    assert found is not None
    assert found.id == child.id
    found_parent = find_root_for_path(conn, str(root_dir / "other.txt"))
    assert found_parent is not None
    assert found_parent.id == parent.id


def test_set_root_status_validates(conn, root_dir: Path) -> None:
    root = add_root(conn, root_dir)
    set_root_status(conn, root.id, "missing")
    assert get_root(conn, root.id).status == "missing"
    with pytest.raises(ValueError):
        set_root_status(conn, root.id, "deleted")
