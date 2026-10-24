"""SQLite connection management and schema migration.

All SQL in this package is parameterized. Connections enable foreign keys,
a busy timeout, and WAL journaling so a partially written index does not
corrupt previously committed data.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from arceus_core.config import ensure_data_dir
from arceus_core.errors import DatabaseError, SchemaVersionError
from arceus_core.schema import MIGRATIONS, SCHEMA_VERSION

_EXPECTED_TABLES = frozenset(
    {"roots", "scan_runs", "scan_errors", "entries", "episodes", "skills", "skill_runs"}
)


def connect(db_path: str | Path, *, create: bool = True) -> sqlite3.Connection:
    """Open a SQLite connection, refusing to create a database for read paths."""

    path = Path(db_path)
    if not create and not path.exists():
        raise DatabaseError(
            f"no ARCEUS database at {path}; register a root and run 'arceus scan' first"
        )
    if create:
        ensure_data_dir(path)

    try:
        connection = sqlite3.connect(str(path), timeout=10.0)
    except sqlite3.Error as exc:  # pragma: no cover - depends on OS-level failure
        raise DatabaseError(f"cannot open database {path}: {exc}") from exc

    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.Error as exc:
        connection.close()
        raise DatabaseError(f"cannot configure database {path}: {exc}") from exc
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    """Apply pending migrations, validating the recorded schema version."""

    try:
        current = int(connection.execute("PRAGMA user_version").fetchone()[0])
    except sqlite3.Error as exc:
        raise DatabaseError(
            "the database file is unreadable or is not a valid SQLite database "
            f"({exc}); move it aside or point --db elsewhere"
        ) from exc

    if current > SCHEMA_VERSION:
        raise SchemaVersionError(
            f"database schema version {current} is newer than supported version "
            f"{SCHEMA_VERSION}; upgrade arceus-core instead of downgrading the database"
        )

    for target, sql in MIGRATIONS:
        if current >= target:
            continue
        try:
            connection.executescript(sql)
            connection.execute(f"PRAGMA user_version = {target}")
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise DatabaseError(f"failed to apply schema migration {target}: {exc}") from exc
        current = target

    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    missing = _EXPECTED_TABLES - tables
    if missing:
        raise DatabaseError("database is missing expected tables: " + ", ".join(sorted(missing)))


@contextmanager
def open_database(db_path: str | Path, *, create: bool = True) -> Iterator[sqlite3.Connection]:
    """Context manager yielding a migrated connection, always closing it."""

    connection = connect(db_path, create=create)
    try:
        initialize(connection)
        yield connection
    finally:
        connection.close()


def schema_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def integrity_ok(connection: sqlite3.Connection) -> bool:
    """Run SQLite's structural integrity check (diagnostics only)."""

    try:
        rows = connection.execute("PRAGMA quick_check").fetchall()
    except sqlite3.Error:
        return False
    return len(rows) == 1 and str(rows[0][0]).lower() == "ok"


def database_size_bytes(db_path: str | Path) -> int:
    """Total size of the database and any WAL/SHM sidecar files."""

    total = 0
    base = Path(db_path)
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(base) + suffix)
        try:
            total += candidate.stat().st_size
        except OSError:
            continue
    return total
