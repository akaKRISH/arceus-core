from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

from arceus_core.roots import add_root
from arceus_core.scanner import scan_root
from arceus_core.search import SearchFilters, search


def _statuses(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        str(row["rel_path"]): str(row["status"])
        for row in conn.execute("SELECT rel_path, status FROM entries")
    }


def test_deleted_file_is_marked_missing(conn, root_dir: Path, make_files, root) -> None:
    make_files(root_dir, {"gone.txt": "x", "kept.txt": "y"})
    scan_root(conn, root)
    (root_dir / "gone.txt").unlink()

    summary = scan_root(conn, root)

    assert summary.status == "completed"
    assert summary.entries_marked_missing == 1
    statuses = _statuses(conn)
    assert statuses["gone.txt"] == "missing"
    assert statuses["kept.txt"] == "present"


def test_missing_rows_are_retained_for_history(conn, root_dir: Path, make_files, root) -> None:
    make_files(root_dir, {"gone.txt": "x"})
    scan_root(conn, root)
    before = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    (root_dir / "gone.txt").unlink()
    scan_root(conn, root)

    after = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    assert after == before
    row = conn.execute("SELECT * FROM entries WHERE rel_path = 'gone.txt'").fetchone()
    assert row["status"] == "missing"
    assert row["first_seen_run_id"] is not None


def test_deleted_directory_cascades_to_descendants(conn, root_dir: Path, make_files, root) -> None:
    make_files(root_dir, {"sub/deep/file.txt": "x", "kept.txt": "y"})
    scan_root(conn, root)
    shutil.rmtree(root_dir / "sub")

    summary = scan_root(conn, root)

    statuses = _statuses(conn)
    assert statuses["sub"] == "missing"
    assert statuses["sub/deep"] == "missing"
    assert statuses["sub/deep/file.txt"] == "missing"
    assert statuses["kept.txt"] == "present"
    assert summary.entries_marked_missing == 3


def test_rename_marks_old_path_missing_and_new_path_present(
    conn, root_dir: Path, make_files, root
) -> None:
    make_files(root_dir, {"old-name.txt": "x"})
    scan_root(conn, root)
    os.replace(root_dir / "old-name.txt", root_dir / "new-name.txt")

    summary = scan_root(conn, root)

    statuses = _statuses(conn)
    assert statuses["old-name.txt"] == "missing"
    assert statuses["new-name.txt"] == "present"
    assert summary.entries_marked_missing == 1


def test_partial_scan_does_not_mark_unobserved_children_missing(
    conn, root_dir: Path, make_files, root, monkeypatch
) -> None:
    make_files(
        root_dir,
        {
            "blocked/inside.txt": "x",
            "open/keep.txt": "y",
            "open/gone.txt": "z",
        },
    )
    scan_root(conn, root)
    (root_dir / "open" / "gone.txt").unlink()

    real_scandir = os.scandir

    def fake_scandir(path):
        if os.path.basename(os.path.normpath(str(path))) == "blocked":
            raise PermissionError(13, "permission denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", fake_scandir)
    summary = scan_root(conn, root)

    statuses = _statuses(conn)
    assert summary.status == "partial"
    assert statuses["blocked/inside.txt"] == "present"
    assert statuses["open/keep.txt"] == "present"
    assert statuses["open/gone.txt"] == "missing"


def test_root_failure_never_marks_entries_missing(conn, root_dir: Path, make_files, root) -> None:
    make_files(root_dir, {"a.txt": "x", "sub/b.txt": "y"})
    scan_root(conn, root)
    shutil.rmtree(root_dir)

    summary = scan_root(conn, root)

    assert summary.status == "failed"
    assert summary.entries_marked_missing == 0
    assert set(_statuses(conn).values()) == {"present"}


def test_reappearing_entry_returns_to_present(conn, root_dir: Path, make_files, root) -> None:
    make_files(root_dir, {"back.txt": "one"})
    scan_root(conn, root)
    (root_dir / "back.txt").unlink()
    scan_root(conn, root)
    assert _statuses(conn)["back.txt"] == "missing"

    (root_dir / "back.txt").write_text("two", encoding="utf-8")
    scan_root(conn, root)

    assert _statuses(conn)["back.txt"] == "present"


def test_emptied_root_marks_previous_entries_missing(
    conn, root_dir: Path, make_files, root
) -> None:
    make_files(root_dir, {"a.txt": "x", "sub/b.txt": "y"})
    scan_root(conn, root)
    for path in sorted(root_dir.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()

    summary = scan_root(conn, root)

    assert summary.status == "completed"
    assert set(_statuses(conn).values()) == {"missing"}


def test_missing_entries_are_hidden_from_default_search(
    conn, root_dir: Path, make_files, root
) -> None:
    make_files(root_dir, {"ephemeral.txt": "x"})
    scan_root(conn, root)
    (root_dir / "ephemeral.txt").unlink()
    scan_root(conn, root)

    assert search(conn, "ephemeral", SearchFilters()).hits == []
    included = search(conn, "ephemeral", SearchFilters(include_missing=True)).hits
    assert len(included) == 1
    assert included[0].entry.status == "missing"


def test_nested_roots_do_not_interfere(conn, root_dir: Path, tmp_path: Path, make_files) -> None:
    nested = root_dir / "inner"
    nested.mkdir()
    make_files(nested, {"inner.txt": "x"})
    outer_root = add_root(conn, root_dir)
    inner_root = add_root(conn, nested)

    scan_root(conn, outer_root)
    scan_root(conn, inner_root)

    counts = {
        int(row["root_id"]): int(row["count"])
        for row in conn.execute("SELECT root_id, COUNT(*) AS count FROM entries GROUP BY root_id")
    }
    assert counts[outer_root.id] == 2  # inner dir + inner/inner.txt
    assert counts[inner_root.id] == 1  # inner.txt only

    (nested / "inner.txt").unlink()
    scan_root(conn, outer_root)
    scan_root(conn, inner_root)
    inner_status = conn.execute(
        "SELECT status FROM entries WHERE root_id = ? AND rel_path = 'inner.txt'",
        (inner_root.id,),
    ).fetchone()[0]
    assert inner_status == "missing"
