"""Configuration: where ARCEUS keeps its index database.

The default location is outside any indexed root whenever the OS provides a
per-user application-data directory. The path can be overridden, in order of
precedence, by:

1. an explicit ``--db`` command-line argument,
2. the ``ARCEUS_DATA_DIR`` environment variable,
3. the platform default below.

No directory is scanned or created during import; only an explicit command
creates the database parent directory.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from arceus_core.errors import ConfigError

DATABASE_FILENAME = "arceus.sqlite3"
DATA_DIR_ENV = "ARCEUS_DATA_DIR"


def default_data_dir() -> Path:
    """Return the per-user data directory for ARCEUS on this platform."""

    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser()

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "ARCEUS"
        return Path.home() / "AppData" / "Local" / "ARCEUS"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ARCEUS"

    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "arceus"
    return Path.home() / ".local" / "share" / "arceus"


def default_db_path() -> Path:
    """Return the default SQLite index path."""

    return default_data_dir() / DATABASE_FILENAME


def resolve_db_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the database path from an explicit argument or the environment."""

    if explicit is None:
        path = default_db_path()
    else:
        text = os.fspath(explicit)
        if not text.strip():
            raise ConfigError("database path must not be empty")
        path = Path(text).expanduser()
    return path


def ensure_data_dir(path: Path) -> None:
    """Create the parent directory of ``path`` with a clear failure message."""

    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"cannot create data directory {parent}: {exc}") from exc
    if not os.access(parent, os.W_OK):
        raise ConfigError(f"data directory is not writable: {parent}")
