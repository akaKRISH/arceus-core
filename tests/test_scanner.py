from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from arceus_core.database import open_database
from arceus_core.roots import add_root, get_root
from arceus_core.scanner import ScanOptions, scan_root
from arceus_core.search import SearchFilters, search

CONTENT_MARKER = "ARCEUS_SECRET_FILE_CONTENT_9f3b7c1d5e"


def _entries(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {str(row["rel_path"]): row for row in conn.execute("SELECT * FROM entries")}


def _scan(conn, root):
    return scan_root(conn, root)


def test_scan_records_metadata(conn, root_dir: Path, make_files, root) -> None:
    make_files(root_dir, {"a.txt": "hello", "sub/b.md": "world!"})
    (root_dir / "empty").mkdir()

    summary = _scan(conn, root)

    assert summary.status == "completed"
    assert summary.files_seen == 2
    assert summary.dirs_seen == 2
    assert summary.errors == 0
    rows = _entries(conn)
    assert set(rows) == {"a.txt", "sub", "sub/b.md", "empty"}
    assert rows["a.txt"]["entry_type"] == "file"
    assert rows["a.txt"]["size_bytes"] == 5
    assert rows["a.txt"]["extension"] == "txt"
    assert rows["sub"]["entry_type"] == "dir"
    assert rows["sub"]["size_bytes"] is None
    assert rows["sub/b.md"]["parent_rel_path"] == "sub"
    assert rows["a.txt"]["status"] == "present"
    assert rows["a.txt"]["mtime_ns"] is not None


def test_scan_is_metadata_only(tmp_path: Path) -> None:
    root_dir = tmp_path / "root"
    root_dir.mkdir()
    (root_dir / "secret.txt").write_text(CONTENT_MARKER, encoding="utf-8")
    db_path = tmp_path / "data" / "index.sqlite3"

    with open_database(db_path) as conn:
        root = add_root(conn, root_dir)
        scan_root(conn, root)
        columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(entries)")}
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        found = search(conn, "secret.txt", SearchFilters())

    assert "content" not in columns
    assert "contents" not in columns
    assert found.hits, "metadata search must still find the file"
    raw = db_path.read_bytes()
    assert CONTENT_MARKER.encode() not in raw


def test_unicode_spaces_and_nested_paths(conn, root_dir: Path, make_files, root) -> None:
    make_files(
        root_dir,
        {
            "résumé final.pdf": "a",
            "子目录/文件 名.txt": "b",
            "dir with spaces/deep name.txt": "c",
        },
    )
    summary = _scan(conn, root)
    assert summary.status == "completed"
    assert summary.files_seen == 3
    assert summary.dirs_seen == 2
    rows = _entries(conn)
    assert "résumé final.pdf" in rows
    assert "子目录/文件 名.txt" in rows
    hits = search(conn, "文件", SearchFilters()).hits
    assert hits and hits[0].entry.rel_path == "子目录/文件 名.txt"


def test_persistence_across_reopen(db_path: Path, root_dir: Path, make_files) -> None:
    make_files(root_dir, {"persisted.txt": "x"})
    with open_database(db_path) as conn:
        root = add_root(conn, root_dir)
        scan_root(conn, root)

    with open_database(db_path) as conn:
        rows = _entries(conn)
        assert "persisted.txt" in rows
        assert rows["persisted.txt"]["status"] == "present"


def test_rescan_is_idempotent_and_preserves_first_seen(
    conn, root_dir: Path, make_files, root
) -> None:
    make_files(root_dir, {"a.txt": "x", "sub/b.txt": "y"})
    first = _scan(conn, root)
    first_seen = {
        str(row["rel_path"]): row["first_seen_run_id"]
        for row in conn.execute("SELECT rel_path, first_seen_run_id FROM entries")
    }
    before = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]

    second = _scan(conn, root)

    assert second.status == "completed"
    assert second.files_seen == 2
    assert second.entries_marked_missing == 0
    assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == before
    after_seen = {
        str(row["rel_path"]): row["first_seen_run_id"]
        for row in conn.execute("SELECT rel_path, first_seen_run_id FROM entries")
    }
    assert after_seen == first_seen
    assert first.run_id != second.run_id


def test_modified_file_metadata_is_updated(conn, root_dir: Path, make_files, root) -> None:
    make_files(root_dir, {"note.txt": "short"})
    target = root_dir / "note.txt"
    _scan(conn, root)
    original = _entries(conn)["note.txt"]

    target.write_text("a much longer payload", encoding="utf-8")
    bumped_mtime = original["mtime_ns"] + 10_000_000_000
    os.utime(target, ns=(bumped_mtime, bumped_mtime))
    _scan(conn, root)
    updated = _entries(conn)["note.txt"]

    assert updated["size_bytes"] == len("a much longer payload")
    assert updated["mtime_ns"] != original["mtime_ns"]
    assert updated["first_seen_run_id"] == original["first_seen_run_id"]
    assert updated["last_seen_run_id"] != original["last_seen_run_id"]


def test_symlinks_are_not_followed(conn, root_dir: Path, make_files, root) -> None:
    make_files(root_dir, {"real.txt": "real"})
    link_file = root_dir / "link.txt"
    try:
        os.symlink(root_dir / "real.txt", link_file)
    except (OSError, NotImplementedError):
        pytest.skip("this platform/user cannot create symlinks")

    summary = _scan(conn, root)

    assert summary.skipped_symlinks == 1
    assert "link.txt" not in _entries(conn)
    assert "real.txt" in _entries(conn)


def test_directory_symlink_is_not_traversed(conn, root_dir: Path, make_files, root) -> None:
    outside = root_dir.parent / "outside"
    make_files(outside, {"hidden.txt": "x"})
    link_dir = root_dir / "escape"
    try:
        os.symlink(outside, link_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform/user cannot create directory symlinks")

    summary = _scan(conn, root)

    assert summary.skipped_symlinks == 1
    assert not any(path.startswith("escape/") for path in _entries(conn))


@pytest.mark.skipif(os.name != "nt", reason="junctions are Windows-specific")
def test_windows_junctions_are_not_followed(conn, root_dir: Path, make_files, root) -> None:
    target = root_dir.parent / "junction target"
    make_files(target, {"inside.txt": "x"})
    link = root_dir / "junction"
    # Junctions do not require the symlink privilege, unlike symbolic links.
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        pytest.skip(f"this environment cannot create junctions: {detail}")

    summary = _scan(conn, root)

    entries = _entries(conn)
    assert summary.skipped_symlinks == 1
    assert "junction" not in entries
    assert not any(path.startswith("junction/") for path in entries)
    assert "inside.txt" not in entries


def test_permission_error_is_recorded_and_scan_continues(
    conn, root_dir: Path, make_files, root, monkeypatch
) -> None:
    make_files(root_dir, {"open/visible.txt": "x", "blocked/hidden.txt": "y"})
    real_scandir = os.scandir

    def fake_scandir(path):
        if os.path.basename(os.path.normpath(str(path))) == "blocked":
            raise PermissionError(13, "permission denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", fake_scandir)
    summary = _scan(conn, root)

    assert summary.status == "partial"
    assert summary.errors == 1
    assert summary.files_seen == 1
    errors = conn.execute("SELECT * FROM scan_errors").fetchall()
    assert len(errors) == 1
    assert errors[0]["kind"] == "permission_denied"
    assert errors[0]["rel_path"] == "blocked"
    assert "open/visible.txt" in _entries(conn)


def test_unavailable_root_marks_run_failed(conn, root_dir: Path, root) -> None:
    for child in root_dir.iterdir():
        child.unlink()
    root_dir.rmdir()

    summary = _scan(conn, root)

    assert summary.status == "failed"
    assert summary.errors == 1
    assert get_root(conn, root.id).status == "missing"
    run = conn.execute("SELECT * FROM scan_runs WHERE id = ?", (summary.run_id,)).fetchone()
    assert run["status"] == "failed"


def test_database_files_inside_root_are_excluded(tmp_path: Path) -> None:
    root_dir = tmp_path / "root"
    root_dir.mkdir()
    (root_dir / "keep.txt").write_text("x", encoding="utf-8")
    db_path = root_dir / "arceus.sqlite3"

    with open_database(db_path) as conn:
        root = add_root(conn, root_dir)
        summary = scan_root(
            conn,
            root,
            ScanOptions(exclude_paths=(str(db_path), f"{db_path}-wal", f"{db_path}-shm")),
        )
        names = {str(row["name"]) for row in conn.execute("SELECT name FROM entries")}

    assert summary.files_seen == 1
    assert names == {"keep.txt"}


def test_max_entries_aborts_without_deleting_previous_index(
    conn, root_dir: Path, make_files, root
) -> None:
    make_files(root_dir, {"one.txt": "1"})
    _scan(conn, root)

    make_files(root_dir, {"two.txt": "2", "three.txt": "3", "four.txt": "4"})
    summary = scan_root(conn, root, ScanOptions(max_entries=2))

    assert summary.status == "failed"
    assert any(sample.kind == "limit" for sample in summary.error_samples)
    statuses = {
        str(row["rel_path"]): row["status"]
        for row in conn.execute("SELECT rel_path, status FROM entries")
    }
    assert statuses["one.txt"] == "present"


def test_interrupted_runs_are_recovered(conn, root_dir: Path, make_files, root) -> None:
    make_files(root_dir, {"a.txt": "x"})
    with conn:
        conn.execute(
            "INSERT INTO scan_runs (root_id, started_at, status) VALUES (?, ?, 'running')",
            (root.id, "2026-01-01T00:00:00+00:00"),
        )
        stale_id = conn.execute("SELECT MAX(id) FROM scan_runs").fetchone()[0]

    summary = _scan(conn, root)

    assert summary.interrupted_runs_recovered == 1
    stale = conn.execute("SELECT * FROM scan_runs WHERE id = ?", (stale_id,)).fetchone()
    assert stale["status"] == "interrupted"
    assert stale["finished_at"] is not None


def test_progress_callback_receives_final_stats(conn, root_dir: Path, make_files, root) -> None:
    make_files(root_dir, {"a.txt": "x", "b/c.txt": "y"})
    calls = []
    scan_root(conn, root, progress=calls.append)
    assert calls
    assert calls[-1].files == 2
    assert calls[-1].dirs == 1


def test_scan_does_not_store_root_itself(conn, root_dir: Path, root) -> None:
    summary = _scan(conn, root)
    assert summary.dirs_seen == 0
    assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0
