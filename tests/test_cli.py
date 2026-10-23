from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from arceus_core.cli import main
from arceus_core.database import open_database


def _run(capsys, *args: str) -> tuple[int, str, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_version_flags(capsys) -> None:
    code, out, _ = _run(capsys, "--version")
    assert code == 0
    assert out.strip() == "arceus 0.1.0"

    code, out, _ = _run(capsys, "version")
    assert code == 0
    assert "arceus 0.1.0" in out


def test_help_and_missing_command(capsys) -> None:
    code, out, _ = _run(capsys, "--help")
    assert code == 0
    assert "usage: arceus" in out
    assert "roots" in out and "search" in out

    code, _, err = _run(capsys)
    assert code == 2
    assert "usage" in err.lower()


def test_unknown_command(capsys) -> None:
    code, _, err = _run(capsys, "bogus")
    assert code == 2
    assert "invalid choice" in err.lower()


def test_status_without_database(capsys, db_path: Path) -> None:
    code, out, _ = _run(capsys, "--db", str(db_path), "status")
    assert code == 0
    assert "not created yet" in out

    code, out, _ = _run(capsys, "status", "--db", str(db_path), "--json")
    payload = json.loads(out)
    assert code == 0
    assert payload["database"]["exists"] is False


def test_roots_add_list_and_duplicate(capsys, db_path: Path, root_dir: Path) -> None:
    code, out, err = _run(capsys, "--db", str(db_path), "roots", "add", str(root_dir))
    assert code == 0
    assert "registered root #1" in out
    assert err == ""

    code, out, _ = _run(capsys, "--db", str(db_path), "roots", "list")
    assert code == 0
    assert str(root_dir) in out

    code, _, err = _run(capsys, "--db", str(db_path), "roots", "add", str(root_dir))
    assert code == 1
    assert "already registered" in err


def test_roots_remove_requires_confirmation(capsys, db_path: Path, root_dir: Path) -> None:
    _run(capsys, "--db", str(db_path), "roots", "add", str(root_dir))

    code, _, err = _run(capsys, "--db", str(db_path), "roots", "remove", "1")
    assert code == 1
    assert "--yes" in err

    code, out, _ = _run(capsys, "--db", str(db_path), "roots", "remove", "1", "--yes")
    assert code == 0
    assert "removed root #1" in out


def test_scan_and_search_end_to_end(capsys, db_path: Path, root_dir: Path, make_files) -> None:
    make_files(root_dir, {"docs/Report Final.txt": "alpha", "notes.txt": "beta"})
    _run(capsys, "--db", str(db_path), "roots", "add", str(root_dir))

    code, out, _ = _run(capsys, "--db", str(db_path), "scan")
    assert code == 0
    assert "completed" in out

    code, out, _ = _run(capsys, "--db", str(db_path), "search", "report")
    assert code == 0
    assert "Report Final.txt" in out
    assert str(root_dir) in out

    code, out, _ = _run(capsys, "--db", str(db_path), "search", "report", "--json")
    payload = json.loads(out)
    assert code == 0
    assert payload["count"] == 1
    assert payload["hits"][0]["rel_path"] == "docs/Report Final.txt"
    assert payload["hits"][0]["reason"] == "name prefix match"


def test_index_scan_alias(capsys, db_path: Path, root_dir: Path, make_files) -> None:
    make_files(root_dir, {"a.txt": "x"})
    _run(capsys, "--db", str(db_path), "roots", "add", str(root_dir))
    code, out, _ = _run(capsys, "--db", str(db_path), "index", "scan")
    assert code == 0
    assert "files=1" in out


def test_scan_json_output(capsys, db_path: Path, root_dir: Path, make_files) -> None:
    make_files(root_dir, {"a.txt": "x"})
    _run(capsys, "--db", str(db_path), "roots", "add", str(root_dir))
    code, out, _ = _run(capsys, "--db", str(db_path), "scan", "--json")
    payload = json.loads(out)
    assert code == 0
    assert payload["summaries"][0]["files_seen"] == 1
    assert payload["failed"] == []


def test_scan_unknown_root_fails(capsys, db_path: Path) -> None:
    code, _, err = _run(capsys, "--db", str(db_path), "scan", "--root", "999")
    assert code == 1
    assert "no root with id 999" in err


def test_scan_requires_registered_roots(capsys, db_path: Path) -> None:
    code, _, err = _run(capsys, "--db", str(db_path), "scan")
    assert code == 1
    assert "no roots registered" in err


def test_database_file_inside_root_is_not_indexed(capsys, tmp_path: Path, make_files) -> None:
    root_dir = tmp_path / "root"
    root_dir.mkdir()
    make_files(root_dir, {"visible.txt": "x"})
    db_path = root_dir / "arceus.sqlite3"

    _run(capsys, "--db", str(db_path), "roots", "add", str(root_dir))
    code, _, _ = _run(capsys, "--db", str(db_path), "scan")
    assert code == 0

    code, out, _ = _run(capsys, "--db", str(db_path), "search", "arceus.sqlite3", "--json")
    payload = json.loads(out)
    assert code == 0
    assert payload["count"] == 0


def test_search_with_no_matches_exits_zero(
    capsys, db_path: Path, root_dir: Path, make_files
) -> None:
    make_files(root_dir, {"a.txt": "x"})
    _run(capsys, "--db", str(db_path), "roots", "add", str(root_dir))
    _run(capsys, "--db", str(db_path), "scan")
    code, out, err = _run(capsys, "--db", str(db_path), "search", "zzz-not-there")
    assert code == 0
    assert "0 result(s)" in out
    assert "warning:" in err


def test_show_indexed_and_unknown_path(capsys, db_path: Path, root_dir: Path, make_files) -> None:
    make_files(root_dir, {"sub/file.txt": "x"})
    _run(capsys, "--db", str(db_path), "roots", "add", str(root_dir))
    _run(capsys, "--db", str(db_path), "scan")

    code, out, _ = _run(capsys, "--db", str(db_path), "show", str(root_dir / "sub" / "file.txt"))
    assert code == 0
    assert "type: file" in out
    assert "contents were not read" in out

    code, out, _ = _run(capsys, "--db", str(db_path), "show", str(root_dir / "absent.txt"))
    assert code == 0
    assert "not indexed" in out


def test_show_requires_absolute_or_root_relative(capsys, db_path: Path, root_dir: Path) -> None:
    _run(capsys, "--db", str(db_path), "roots", "add", str(root_dir))
    code, _, err = _run(capsys, "--db", str(db_path), "show", "relative.txt")
    assert code == 1
    assert "absolute path" in err


def test_graph_commands(capsys, db_path: Path, root_dir: Path, make_files) -> None:
    make_files(root_dir, {"alpha/beta/file.txt": "x"})
    _run(capsys, "--db", str(db_path), "roots", "add", str(root_dir))
    _run(capsys, "--db", str(db_path), "scan")

    code, out, _ = _run(capsys, "--db", str(db_path), "graph", "children", str(root_dir))
    assert code == 0
    assert "alpha" in out

    code, out, _ = _run(
        capsys,
        "--db",
        str(db_path),
        "graph",
        "ancestors",
        str(root_dir / "alpha" / "beta" / "file.txt"),
    )
    assert code == 0
    assert "observed" in out

    code, out, _ = _run(
        capsys, "--db", str(db_path), "graph", "tree", str(root_dir), "--depth", "3"
    )
    assert code == 0
    assert "file.txt" in out

    code, out, _ = _run(capsys, "--db", str(db_path), "graph", "stats")
    assert code == 0
    assert "1 files" in out


def test_graph_children_json(capsys, db_path: Path, root_dir: Path, make_files) -> None:
    make_files(root_dir, {"alpha/file.txt": "x"})
    _run(capsys, "--db", str(db_path), "roots", "add", str(root_dir))
    _run(capsys, "--db", str(db_path), "scan")
    code, out, _ = _run(capsys, "--db", str(db_path), "graph", "children", str(root_dir), "--json")
    payload = json.loads(out)
    assert code == 0
    assert payload["children"][0]["name"] == "alpha"


def test_status_after_scan_reports_counts(
    capsys, db_path: Path, root_dir: Path, make_files
) -> None:
    make_files(root_dir, {"a.txt": "x", "sub/b.txt": "y"})
    _run(capsys, "--db", str(db_path), "roots", "add", str(root_dir))
    _run(capsys, "--db", str(db_path), "scan")
    code, out, _ = _run(capsys, "--db", str(db_path), "status", "--json")
    payload = json.loads(out)
    assert code == 0
    assert payload["totals"]["present"] == 3
    assert payload["totals"]["files"] == 2
    assert payload["totals"]["dirs"] == 1
    assert payload["roots"][0]["last_scan"]["status"] == "completed"
    assert payload["database"]["integrity_ok"] is True
    assert "health: no warnings" in _run(capsys, "--db", str(db_path), "status")[1]

    (root_dir / "a.txt").unlink()
    _run(capsys, "--db", str(db_path), "scan")
    payload = json.loads(_run(capsys, "--db", str(db_path), "status", "--json")[1])
    assert payload["totals"]["present"] == 2
    assert payload["totals"]["missing"] == 1
    assert payload["totals"]["files"] == 1
    assert payload["totals"]["dirs"] == 1


def test_double_dash_allows_literal_query(
    capsys, db_path: Path, root_dir: Path, make_files
) -> None:
    make_files(root_dir, {"--json": "x"})
    _run(capsys, "--db", str(db_path), "roots", "add", str(root_dir))
    _run(capsys, "--db", str(db_path), "scan")
    code, out, _ = _run(capsys, "--db", str(db_path), "search", "--", "--json")
    assert code == 0
    assert "1 result(s)" in out


def test_module_entry_point_subprocess() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "arceus_core", "--version"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0
    assert "arceus 0.1.0" in result.stdout


def test_cli_scan_reports_partial_status(
    capsys, db_path: Path, root_dir: Path, make_files, monkeypatch
) -> None:
    make_files(root_dir, {"blocked/hidden.txt": "x", "open/file.txt": "y"})
    _run(capsys, "--db", str(db_path), "roots", "add", str(root_dir))

    real_scandir = os.scandir

    def fake_scandir(path):
        if os.path.basename(os.path.normpath(str(path))) == "blocked":
            raise PermissionError(13, "permission denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", fake_scandir)
    code, out, err = _run(capsys, "--db", str(db_path), "scan")
    assert code == 0
    assert "partial" in out
    assert "error [permission_denied]" in err
    with open_database(db_path, create=False) as conn:
        scan_status = conn.execute(
            "SELECT status FROM scan_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
    assert scan_status == "partial"
