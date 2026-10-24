"""Versioned SQLite schema definitions.

Schema changes are additive migrations. ``PRAGMA user_version`` records the
applied version. Never edit an already-shipped migration in place; append a new
one and bump ``SCHEMA_VERSION``.
"""

from __future__ import annotations

SCHEMA_VERSION = 3

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

MIGRATION_2 = """
CREATE TABLE episodes (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    task_signature TEXT NOT NULL,
    task_category TEXT NOT NULL,
    action TEXT NOT NULL
        CHECK (action IN ('search_files','graph_children','graph_ancestors',
                          'ask_clarification','abstain')),
    origin TEXT NOT NULL
        CHECK (origin IN ('model','deterministic','memory','skill')),
    executed INTEGER NOT NULL CHECK (executed IN (0,1)),
    verification_status TEXT
        CHECK (verification_status IS NULL
               OR verification_status IN ('verified','unverified','incomplete')),
    task_completed INTEGER
        CHECK (task_completed IS NULL OR task_completed IN (0,1)),
    outcome TEXT NOT NULL
        CHECK (outcome IN ('verified_success','unverified','failure')),
    failure_category TEXT,
    evidence_refs TEXT NOT NULL DEFAULT '[]',
    provenance TEXT NOT NULL DEFAULT '{}',
    config_version TEXT NOT NULL,
    skill_id TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active','corrected','invalidated','expired')),
    correction_of INTEGER REFERENCES episodes(id) ON DELETE SET NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    dedupe_key TEXT NOT NULL
);

CREATE INDEX ix_episodes_dedupe ON episodes(dedupe_key, status);
CREATE INDEX ix_episodes_signature ON episodes(task_signature, status);
CREATE INDEX ix_episodes_category ON episodes(task_category, status);
CREATE INDEX ix_episodes_created ON episodes(created_at DESC);

CREATE TABLE skills (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    purpose TEXT NOT NULL,
    supported_categories TEXT NOT NULL,
    preconditions TEXT NOT NULL DEFAULT '[]',
    action_sequence TEXT NOT NULL,
    required_permissions TEXT NOT NULL,
    input_limits TEXT NOT NULL,
    expected_outputs TEXT NOT NULL,
    verification_procedure TEXT NOT NULL,
    evaluation_evidence TEXT NOT NULL DEFAULT '[]',
    known_failures TEXT NOT NULL DEFAULT '[]',
    provenance TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate','verified','disabled','revoked')),
    status_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE skill_runs (
    id INTEGER PRIMARY KEY,
    skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    episode_id INTEGER REFERENCES episodes(id) ON DELETE SET NULL,
    ran_at TEXT NOT NULL,
    verified INTEGER NOT NULL CHECK (verified IN (0,1)),
    outcome TEXT NOT NULL,
    UNIQUE (skill_id, episode_id)
);

CREATE INDEX ix_skill_runs_skill ON skill_runs(skill_id, verified);
"""

MIGRATION_3 = """
ALTER TABLE skills ADD COLUMN accepted_task_signatures TEXT NOT NULL DEFAULT '[]'
"""

MIGRATIONS: tuple[tuple[int, str], ...] = ((1, MIGRATION_1), (2, MIGRATION_2), (3, MIGRATION_3))
