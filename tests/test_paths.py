from __future__ import annotations

import os
from pathlib import Path

import pytest

from arceus_core.paths import (
    display_path,
    extension_of,
    is_descendant_rel,
    is_within,
    join_rel,
    normalize_abs,
    path_key,
    rel_depth,
    rel_key_for,
    rel_parent,
    relative_to_root,
    safe_name,
    to_posix_rel,
)


def test_normalize_abs_makes_absolute(tmp_path: Path) -> None:
    result = normalize_abs(tmp_path / "a" / ".." / "b")
    assert os.path.isabs(result)
    assert result == os.path.normpath(str(tmp_path / "b"))


def test_normalize_abs_expands_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert normalize_abs("~") == os.path.normpath(str(tmp_path))


def test_relative_to_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    assert relative_to_root(root, root) == ""
    assert relative_to_root(root / "a" / "b.txt", root) == "a/b.txt"


def test_relative_to_root_rejects_outside(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        relative_to_root(tmp_path / "other", tmp_path / "root")


def test_is_within_does_not_match_sibling_prefix(tmp_path: Path) -> None:
    root = tmp_path / "data"
    sibling = tmp_path / "database"
    assert not is_within(sibling, root)
    assert is_within(root, root)
    assert is_within(root / "x", root)


def test_rel_key_platform_behavior() -> None:
    expected = "case/name.txt" if os.name == "nt" else "Case/Name.TXT"
    assert rel_key_for("Case/Name.TXT") == expected


def test_rel_parent_and_depth() -> None:
    assert rel_parent("a/b/c") == "a/b"
    assert rel_parent("a") == ""
    assert rel_depth("") == 0
    assert rel_depth("a") == 1
    assert rel_depth("a/b") == 2


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("notes.TXT", "txt"),
        ("archive.tar.gz", "gz"),
        (".gitignore", ""),
        ("README", ""),
        ("trailing.", ""),
    ],
)
def test_extension_of(name: str, expected: str) -> None:
    assert extension_of(name) == expected


def test_join_rel_and_descendant() -> None:
    assert join_rel("", "a") == "a"
    assert join_rel("a", "b") == "a/b"
    assert is_descendant_rel("a/b", "a")
    assert not is_descendant_rel("ab", "a")
    assert is_descendant_rel("a", "")


def test_to_posix_rel() -> None:
    assert to_posix_rel(["a", "", ".", "b"]) == "a/b"


def test_display_path_roundtrip(tmp_path: Path) -> None:
    root = str(tmp_path)
    assert display_path(root, "a/b.txt") == os.path.join(root, "a", "b.txt")
    assert display_path(root, "") == root


def test_path_key_is_normalized(tmp_path: Path) -> None:
    assert path_key(tmp_path / "x") == path_key(str(tmp_path) + os.sep + "x")


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "a\\b", "a\x00b"])
def test_safe_name_rejects_invalid(bad: str) -> None:
    with pytest.raises(ValueError):
        safe_name(bad)


def test_safe_name_accepts_unicode_and_spaces() -> None:
    assert safe_name("übersicht final.txt") == "übersicht final.txt"
