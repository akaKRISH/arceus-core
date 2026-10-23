# Architecture

## Architectural rule

Keep a deterministic, testable local core separate from probabilistic reasoning. The model proposes; policy validates; tools execute; validators verify; records preserve provenance.

## Layered design

### 1. CLI / future UI

The initial interface is a Python CLI. A future desktop interface calls stable core APIs and must not bypass policy checks.

### 2. Application services

- Root registry: stores explicitly approved indexing roots.
- Scanner: discovers entries without following symlinks by default.
- Reconciler: updates changed entries and safely marks/removes stale rows only when scan coverage is known.
- Search: exact/prefix/substring filename and path matching, with clearly documented ranking.
- Graph query service: retrieves parent-child and root-membership relationships.
- Model adapter: optional provider boundary for local inference.
- Agent controller: proposes, validates, executes, and verifies bounded steps.
- Memory/skill service: manages time-stamped, provenance-backed records with invalidation rules.
- Evaluator: runs reproducible task sets and records metrics.

### 3. Persistence

SQLite is the first storage engine; do not adopt a dedicated graph database without measured evidence that SQLite is insufficient. Use schema migrations, parameterized SQL, transactions, and explicit indexes.

Initial tables (adjust only with documented reasons):

- `roots`: root ID, user-approved path, status, creation/last-scan timestamps, scan metadata.
- `scan_runs`: run ID, root ID, start/end, status, counts, scan coverage/errors.
- `entries`: entry ID, root ID, relative path, name, extension, parent-relative path or parent ID, entry type, size, modification time, last-seen scan, availability/status.
- `relationships`: source ID, relation type, target ID, provenance, observation timestamp, confidence/status.
- Later only: `tasks`, `episodes`, `facts`, `skills`, `experiments`, `tool_runs`.

Avoid relying on inode/file ID as a universal cross-platform identity. Treat the path within a registered root as the initial index key and document rename detection limitations.

### 4. Filesystem metadata service

Record metadata needed to locate and reason about entries. Do not copy contents into SQLite. Store root-relative paths where possible and derive absolute paths from the approved root. Avoid following symlinks. Handle permission errors, files changing during scans, very long paths, Unicode, case behavior, and stale database rows.

A scan that failed to inspect a subtree must not blindly mark every previously indexed descendant as deleted. Reconciliation needs scan coverage/error information.

### 5. Retrieval and graph

Start with path/name matching. Then expose explicit relationships: root contains folder; folder contains entry; file has parent directory; later, source file imports module, project uses config, or task evidence points to file only when that relation was actually observed and its provenance is saved.

Do not add embeddings until a benchmark shows that graph/text retrieval has an important failure mode and the embedding approach improves the measured outcome enough to justify storage and inference cost.

### 6. Local model adapter

Define an interface for model identity/version, structured input/output, timeouts, cancellation, context/size limits when available, and timing/token/resource measurements. Model weights are optional and selected by the user. Do not auto-download weights or require an external API.

### 7. Agent controller

Suggested state machine:

`RECEIVED → PLAN_PROPOSED → VALIDATED → EXECUTING → VERIFYING → COMPLETED | FAILED | NEEDS_APPROVAL`

Every loop has a maximum step count, timeout, cancellation path, tool allowlist, and structured trace. Model outputs never bypass validation. Start with read-only tools. Writes, installs, shell commands, moves, and deletion require explicit approval and narrowly scoped operations.

### 8. Memory and skills

A stored fact needs source/provenance, observed-at time, valid-time where relevant, confidence/status, and invalidation/update behavior. A skill needs preconditions, steps, expected effects, allowed tools, validation, version, and a test fixture. A previous success does not imply future safety.

### 9. Desktop UI

Only after the core APIs stabilize: evaluate Tauri + React + TypeScript with Rust adapters for native functionality. The UI should provide root selection, search results, provenance, permission controls, task traces, cancel, and confirmation for sensitive actions.

## Data flow

User approves a root → scan → metadata index → search/graph retrieval → optional on-demand, permission-checked read of an original file → model proposes an answer/plan → policy validator checks it → allowlisted tool executes → deterministic evaluator verifies → provenance-backed trace is saved.

## Cross-platform boundaries

Use `pathlib` for path semantics; isolate file watchers and OS open-file behavior behind adapters. Test Windows, Linux, and macOS in CI when feasible. A Windows-only run is not proof of cross-platform support.
