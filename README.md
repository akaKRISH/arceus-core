# Project ARCEUS

**Status:** Milestone 1 — metadata-only filesystem index, reconciliation, and search — is implemented and tested on Windows 11. Structural graph-aware retrieval (roadmap Phase 3) is also implemented as a query layer over observed relationships. There is no LLM, no embedding store, and no GUI in this milestone.

ARCEUS investigates whether a local-first agent can improve task success per unit of compute by combining a compact local model with a structured representation of its environment, source-grounded retrieval, persistent memory, verified reusable skills, bounded planning, and outcome verification. None of that broader claim has been evaluated yet. This repository currently contains a deterministic, offline core.

## What works today

- Register explicitly chosen directories; nothing outside a registered root is ever scanned.
- Scan file **metadata only** into SQLite: relative path, name, extension, type, size, modification time, first/last observation, scan coverage.
- Search filenames and root-relative paths with case-insensitive, Unicode-safe matching and a documented ranking.
- Incrementally rescan; additions, renames (as delete + add), modifications, and deletions reconcile without silently erasing index rows.
- Survive unreadable directories: partial scans are marked `partial`, and unobserved entries are retained rather than falsely deleted.
- Skip symbolic links and Windows junctions; never follow them.
- Query structural relationships: direct children, ancestor chains with observed-vs-inferred provenance, bounded subtree listings, per-root counts.
- Report health (`status`), errors, stale/partial scans, and interrupted runs.
- Emit JSON for machine consumption, including UTF-8 paths on Windows.

## Install

Python **3.11+** is required; the reference environment used 3.13 because 3.12 was not installed. The package uses only the standard library at runtime.

Windows PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest
```

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest
```

If PowerShell blocks activation, use `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` for that terminal only.

## Use

The database defaults to the per-user application-data directory:

| Platform | Default database |
|---|---|
| Windows | `%LOCALAPPDATA%\ARCEUS\arceus.sqlite3` |
| macOS | `~/Library/Application Support/ARCEUS/arceus.sqlite3` |
| Linux | `$XDG_DATA_HOME/arceus/arceus.sqlite3` or `~/.local/share/arceus/arceus.sqlite3` |

Override it with `--db PATH` (accepted before or after the subcommand) or `ARCEUS_DATA_DIR`. The database is never placed inside an indexed root by default, and database files are excluded even if a root contains them.

```powershell
# Register one explicit directory (must already exist)
arceus roots add "C:\Users\me\Projects\demo" --label demo

# Scan every registered root
arceus scan

# Or scan one root only
arceus scan --root 1

# Search by filename or relative path
arceus search "report"
arceus search "main.py" --ext py --limit 5
arceus search "src" --type dir --json

# Inspect a result's provenance and ancestry
arceus show "C:\Users\me\Projects\demo\src\main.py"

# Structural queries
arceus graph children "C:\Users\me\Projects\demo"
arceus graph ancestors "C:\Users\me\Projects\demo\src\main.py"
arceus graph tree "C:\Users\me\Projects\demo" --depth 2
arceus graph stats

# Health and maintenance
arceus status
arceus status --json
arceus roots list
arceus roots remove 1 --yes   # deletes index rows only; original files untouched
```

`arceus index scan` is an alias for `arceus scan`. All commands support `--help`. Global options are `--db PATH` and `--json`; use `--` before a literal argument that begins with `--`.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Command completed (for `search`, zero matches is still success) |
| 1 | Operational error: unknown root, invalid path, database problem, failed scan |
| 2 | Usage error |

## What is stored, and what is not

Stored in SQLite under `roots`, `scan_runs`, `scan_errors`, and `entries`:

- the registered root path and its case-normalized key;
- root-relative paths with forward slashes, names, extensions, file-vs-directory type;
- size and modification time, first/last scan run IDs, observation timestamps;
- scan status, coverage-relevant errors, counts, and skipped-symlink counts.

**Never stored:** file contents, content hashes, embeddings, previews, or copies of indexed files. Tests assert that a unique marker written into a fixture file does not appear in the database bytes.

An entry that disappears is marked `missing`, not deleted. This preserves history and makes stale results explainable; `search --include-missing` exposes them. Removing a root explicitly deletes only its index rows.

## Safety behavior

- Only explicitly registered roots are scanned; there is no default home-directory or drive scan.
- Directory symlinks and junctions are skipped and never traversed.
- A directory that could not be enumerated is never treated as empty. Its previously indexed children stay `present` because the scan has no evidence of removal.
- Descendants of an observed-removed directory are marked `missing` only when the removal itself was observed under a fully enumerated parent.
- Scans are batched in transactions; a crash or Ctrl+C finishes the run as `interrupted`, and the next scan reports it.
- `--max-entries` provides an explicit guard for unexpectedly large roots.
- SQL is parameterized; foreign keys, WAL journaling, and a busy timeout are enabled.
- Nothing is transmitted over the network.

## Architecture

```text
src/arceus_core/
├── cli.py          argparse CLI; formatting and dispatch only
├── config.py       data-directory and database-path resolution
├── database.py     SQLite connection, migrations, integrity checks
├── schema.py       versioned schema (PRAGMA user_version)
├── paths.py        platform-aware path/key normalization
├── models.py       typed records
├── roots.py        root registry and lookup
├── scanner.py      metadata-only traversal, batching, scan runs
├── reconcile.py    coverage-based conservative reconciliation
├── search.py       ranked name/path search with explanations
├── graph.py        children, ancestry, subtree, structural stats
├── status.py       health aggregation
└── errors.py       explicit exception hierarchy
```

Tests live in `tests/` and use temporary directories and databases exclusively. `experiments/bench_index.py` builds a synthetic tree, scans it, and reports timings, database size, and Python-heap peak.

## Verified results

Environment: Windows 11 Home (10.0.26300), Python 3.13.15, Git 2.56.0, 12 logical CPUs, 16,013 MB RAM, NVIDIA RTX 3050 6 GB (driver 591.91), 9.3 GB free disk. Python 3.12 was not installed; 3.13 was used and `requires-python` is `>=3.11`.

- Tests: **116 passed, 2 skipped** (`python -m pytest`). The two skips are symlink-creation tests that this Windows user cannot run without developer mode; Windows junction skipping is verified end-to-end with a real junction, and the link detector covers both link types.
- Lint: `ruff check src tests` passes. Formatting: `ruff format --check src tests` passes.
- Measured on synthetic fixtures (50 query repetitions each; full details in [reports/2026-10-23_filesystem_baseline.md](reports/2026-10-23_filesystem_baseline.md)):
  - 2,000 files / 40 dirs: first scan 0.126 s (~15,900 files/s), database 976 KiB, search median 0.5–1.3 ms.
  - 20,000 files / 200 dirs: first scan 1.746 s (~11,450 files/s), database 8.97 MiB (~466 bytes/entry), search median 12–23 ms.
  - Idempotent rescan and 10% deletion reconciliation produced the exact expected `missing` counts, with indices preserved.

These are single-machine baseline numbers, not cross-platform or scaling claims. Linux and macOS have not been executed.

## Known limitations

- **Windows-only verification.** The code uses `pathlib` abstraction and platform-specific keys, but no CI has run on Linux or macOS.
- **Metadata only.** Search cannot answer content questions; no text extraction or embeddings exist.
- **Renames are delete + add** unless the relative path is unchanged. File identity (inode/file ID) is deliberately not used as a cross-platform key.
- **Case semantics:** entry keys are case-folded on Windows and case-preserving on POSIX, chosen from the host at scan time. A database moved between platform families needs a rescan.
- **Substring search is a full scan.** There is no FTS index; measured 12–23 ms at 20k entries, but this grows linearly. FTS is deferred until a measured task needs it.
- **Symlinks/junctions are unconditionally skipped.** There is no opt-in to follow them.
- **Single-writer assumption.** WAL and a busy timeout are enabled, but concurrent scan/write behavior is untested.
- **Interrupted scans are reported, not resumed.** The next scan re-observes the root.
- **No model, memory, skills, agent loop, or UI yet.** Those are later roadmap gates, not hidden features.

## Research status

The long-term hypothesis (Budgeted Cognitive Routing over environment structure, memory, verified skills, and selective model reasoning) remains untested. This milestone supplies a deterministic baseline and provenance it can later be compared against. The structural graph layer exists, but no graph-versus-path retrieval benchmark has yet been run, so no improvement is claimed.

See `RESEARCH_PLAN.md`, `LITERATURE_REVIEW.md`, and `EXPERIMENT_PROTOCOL.md` for the designed experiments. See `DECISIONS.md` for architectural decisions and `FIRST_SPRINT_CHECKLIST.md` for the completed gate.

## Next milestone

Build the Phase 3 evaluation harness: a versioned synthetic navigation task suite with exact expected paths/relationships, and a comparison of path-only search versus graph-assisted retrieval on the same tasks. This needs no model weights and produces the evidence the roadmap gate requires.

## Documentation map

1. `START_HERE.md` — human setup guide and phase gates
2. `PRODUCT_VISION.md` — scope and users
3. `ARCHITECTURE.md` — layered design and intended boundaries
4. `IMPLEMENTATION_ROADMAP.md` — phase gates
5. `AGENT_RULES.md` — operating rules for coding agents
6. `FIRST_SPRINT_CHECKLIST.md` — verified milestone checklist
7. `reports/2026-10-23_filesystem_baseline.md` — measurements
8. `REPOSITORY_STRUCTURE.md` — current layout
