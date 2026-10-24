from __future__ import annotations

from pathlib import Path

import pytest

from arceus_core.database import (
    database_size_bytes,
    integrity_ok,
    open_database,
    schema_version,
)
from arceus_core.errors import DatabaseError, SchemaVersionError
from arceus_core.schema import SCHEMA_VERSION


def test_migration_creates_expected_schema(db_path: Path) -> None:
    with open_database(db_path) as conn:
        assert schema_version(conn) == SCHEMA_VERSION
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert {"roots", "scan_runs", "scan_errors", "entries"} <= tables
    # Phase 6 additions arrive through the same versioned migration path.
    assert {"episodes", "skills", "skill_runs"} <= tables


def test_schema_survives_reopen(db_path: Path) -> None:
    with open_database(db_path) as conn:
        assert schema_version(conn) == SCHEMA_VERSION
    with open_database(db_path) as conn:
        assert schema_version(conn) == SCHEMA_VERSION


def test_newer_schema_is_rejected(db_path: Path) -> None:
    with open_database(db_path) as conn:
        conn.execute("PRAGMA user_version = 99")
        conn.commit()
    with pytest.raises(SchemaVersionError), open_database(db_path):
        pass


def test_corrupt_database_reports_clear_error(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"this is definitely not a sqlite database" * 8)
    with pytest.raises(DatabaseError) as excinfo, open_database(corrupt):
        pass
    message = str(excinfo.value).lower()
    assert "not a database" in message or "unreadable" in message or "not a valid" in message


def test_read_only_open_refuses_to_create_missing_database(tmp_path: Path) -> None:
    with (
        pytest.raises(DatabaseError) as excinfo,
        open_database(tmp_path / "missing.sqlite3", create=False),
    ):
        pass
    assert "no ARCEUS database" in str(excinfo.value)


def test_integrity_and_database_size(db_path: Path) -> None:
    with open_database(db_path) as conn:
        assert integrity_ok(conn)
    assert database_size_bytes(db_path) > 0


def test_foreign_keys_are_enabled(db_path: Path) -> None:
    with open_database(db_path) as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
