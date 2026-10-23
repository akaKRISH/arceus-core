"""Shared fixtures.

All tests operate on pytest temporary directories and temporary SQLite
databases. No test reads or indexes a real user directory.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from arceus_core.database import open_database
from arceus_core.roots import add_root


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "arceus.sqlite3"


@pytest.fixture
def conn(db_path: Path):
    with open_database(db_path) as connection:
        yield connection


@pytest.fixture
def root_dir(tmp_path: Path) -> Path:
    path = tmp_path / "fixture root ünïcode"
    path.mkdir()
    return path


@pytest.fixture
def root(conn, root_dir: Path):
    return add_root(conn, root_dir)


@pytest.fixture
def make_files() -> Callable[[Path, dict[str, str]], None]:
    def _make(base: Path, spec: dict[str, str]) -> None:
        for relative, content in spec.items():
            target = base / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    return _make
