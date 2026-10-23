# Architecture Decision Log

Record meaningful decisions here; add one entry per decision. Prefer reversible choices early.

## ADR-0001 — Python core and SQLite first

- **Status:** Accepted for prototype.
- **Decision:** Python 3.12, standard-library `sqlite3`, and `pytest` for the first core. A desktop/native stack is delayed.
- **Reason:** Lowest setup cost and easy testing; SQLite is embedded and does not require a database server.
- **Trade-off:** SQLite/metadata indexing may need redesign if measured graph/query/load requirements exceed its capabilities.
- **Review trigger:** Measured performance or concurrency limitations, not speculative scale.

## ADR-0002 — Metadata-only indexing by default

- **Status:** Accepted.
- **Decision:** Index names, relative paths, types, sizes, timestamps, and structural relationships; do not copy file contents by default.
- **Reason:** Keeps storage use down and limits exposure of private content.
- **Trade-off:** Content-based questions cannot be answered until an explicitly scoped, on-demand read or later opt-in content indexing is designed.
- **Review trigger:** User-approved use case with clear privacy/storage controls and benchmark evidence.

## ADR-0003 — Model is optional and later

- **Status:** Accepted.
- **Decision:** No model is required for first milestone. Inspect hardware before model selection; never auto-download weights.
- **Reason:** Deterministic baseline isolates filesystem correctness from model behavior and avoids premature VRAM/latency assumptions.
- **Review trigger:** Core is tested and a specific model-assisted task is defined.

## ADR-0004 — No autonomous write tools in early phases

- **Status:** Accepted.
- **Decision:** Start read-only. Model output cannot bypass an explicit tool/policy gateway.
- **Reason:** The system touches a user's computer and retrieved files may contain hostile instructions.
- **Review trigger:** Only after permission boundaries, logs, cancellation, postcondition checks, and approval UX are tested.

## ADR-0005 — Use Python 3.13 when 3.12 is unavailable

- **Status:** Accepted for this environment.
- **Decision:** `requires-python = ">=3.11"`; development and verification used Python 3.13.15 because 3.12 is not installed and no system-level interpreter change was justified. Ruff targets Python 3.11.
- **Reason:** Avoid blocking the milestone on a system-wide install; the code uses standard-library features available in 3.11+.
- **Trade-off:** The documented preference for 3.12 is not the tested interpreter here; 3.11 and 3.12 remain unexecuted.
- **Review trigger:** Add a CI matrix for 3.11–3.13 before claiming broad version support.

## ADR-0006 — Soft-delete entries and reconcile only with scan coverage evidence

- **Status:** Accepted.
- **Decision:** Entries are never deleted during scans; they transition to `missing`. Deletion evidence requires that the immediate parent directory was fully enumerated in the scan. Descendants of an observed-removed directory cascade to `missing`. Rows retain `first_seen_run_id` / `last_seen_run_id` and reappear as `present` when re-observed.
- **Reason:** An unreadable directory is not an empty directory. Preserving stale rows with an explicit status is safer and more explainable than destructive inference.
- **Trade-off:** The database accumulates stale rows until a root is removed; search defaults to `present` and exposes `--include-missing`.
- **Review trigger:** Revisit if stale-row volume becomes a measured storage problem.

## ADR-0007 — Relational structure graph with observed/inferred provenance

- **Status:** Accepted.
- **Decision:** Do not add a graph database. Parent-child and root-membership facts come from indexed rows and `parent_rel_key`. Ancestor chains are derived from the root-relative path, and each link reports whether it was observed in the index or inferred from the path string.
- **Reason:** The required relationships are representable in SQLite with indexed lookups; a separate store would add cost without measured benefit.
- **Trade-off:** No semantic edges (imports, similarity, project relationships) exist in this milestone; those require separate observed evidence.
- **Review trigger:** Phase 3 evaluation showing a structural query that SQLite cannot serve at acceptable latency.

## ADR-0008 — Case-folded keys and explainable LIKE ranking

- **Status:** Accepted.
- **Decision:** Store display paths and names alongside case-folded keys. On Windows, entry keys are case-folded; on POSIX they preserve case. Search uses Python case-folding plus escaped `LIKE` patterns and a fixed score ladder (exact name 100, prefix 80, name substring 60, path substring 30).
- **Reason:** SQLite's `LIKE` is ASCII-only case-insensitive; folding in Python gives consistent Unicode behavior, and escaping prevents user input from acting as wildcards.
- **Trade-off:** Substring search is a linear scan; the key choice is host-specific, so a database moved across platform families needs a rescan.
- **Review trigger:** A task benchmark where substring search latency blocks the workflow, or cross-platform database portability becomes a requirement. FTS5 is the first candidate.

## ADR-0009 — Batched writes, WAL, and interrupted-run recovery

- **Status:** Accepted.
- **Decision:** Upsert entries in batches inside transactions, keep a `scan_runs` row in `running` state, and mark stale `running` rows `interrupted` at the next scan of that root. A scan that raises finishes as `interrupted` before propagating.
- **Reason:** Bounded memory on large trees, no partial-commit corruption, and explicit reporting instead of silent state.
- **Trade-off:** A crash may leave a partially indexed tree; the run status communicates this and the next scan re-observes it. No automatic resume.
- **Review trigger:** Evidence that interrupted large scans are common enough to justify resume support.

## ADR-0010 — Deterministic UTF-8 CLI output

- **Status:** Accepted.
- **Decision:** Reconfigure stdout/stderr to UTF-8 with `backslashreplace` at CLI entry, and emit JSON with `ensure_ascii=False`.
- **Reason:** Redirected Windows stdout otherwise uses a legacy ANSI code page, which mangles Unicode paths in text and JSON output. Encoding failures must not crash a search result display.
- **Trade-off:** Redirected output is always UTF-8, regardless of the current console code page.
- **Review trigger:** A consumer that requires locale-encoded output.
