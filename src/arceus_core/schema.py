"""Versioned SQLite schema definitions.

Schema changes are additive migrations. ``PRAGMA user_version`` records the
applied version. Never edit an already-shipped migration in place; append a new
one and bump ``SCHEMA_VERSION``.
"""

from __future__ import annotations

SCHEMA_VERSION = 1

MIGRATION_1 = """
CREATE TABLE roots (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    path_key TEXT NOT NULL UNIQUE,
    label TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'missing')),
    last_scan_run_id INTEGER
);

CREATE TABLE scan_runs (
    id INTEGER PRIMARY KEY,
    root_id INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL
        CHECK (status IN ('running', 'completed', 'partial', 'failed', 'interrupted')),
    files_seen INTEGER NOT NULL DEFAULT 0,
    dirs_seen INTEGER NOT NULL DEFAULT 0,
    entries_marked_missing INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    skipped_symlinks INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);

CREATE INDEX ix_scan_runs_root_started ON scan_runs(root_id, started_at DESC);
CREATE INDEX ix_scan_runs_status ON scan_runs(status);

CREATE TABLE scan_errors (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    rel_path TEXT,
    kind TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE INDEX ix_scan_errors_run ON scan_errors(run_id);

CREATE TABLE entries (
    id INTEGER PRIMARY KEY,
    root_id INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
    rel_path TEXT NOT NULL,
    rel_key TEXT NOT NULL,
    parent_rel_path TEXT NOT NULL DEFAULT '',
    parent_rel_key TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    extension TEXT NOT NULL DEFAULT '',
    entry_type TEXT NOT NULL CHECK (entry_type IN ('file', 'dir')),
    size_bytes INTEGER,
    mtime_ns INTEGER,
    first_seen_run_id INTEGER REFERENCES scan_runs(id) ON DELETE SET NULL,
    last_seen_run_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'present' CHECK (status IN ('present', 'missing')),
    observed_at TEXT NOT NULL,
    UNIQUE (root_id, rel_key)
);

CREATE INDEX ix_entries_parent ON entries(root_id, parent_rel_key, status);
CREATE INDEX ix_entries_name ON entries(root_id, name_key, status);
CREATE INDEX ix_entries_rel ON entries(root_id, rel_key);
CREATE INDEX ix_entries_status ON entries(root_id, status);
CREATE INDEX ix_entries_extension ON entries(root_id, extension);
CREATE INDEX ix_entries_last_seen ON entries(last_seen_run_id);
"""

MIGRATIONS: tuple[tuple[int, str], ...] = ((1, MIGRATION_1),)
