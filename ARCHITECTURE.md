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
- Model adapter: optional provider boundary for local inference, with typed requests/responses, offline enforcement, timeouts, cancellation, limits, and structured-output contracts.
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

`arceus_core.inference` defines the optional boundary: `InferenceRequest`/`InferenceResponse`, backend and model identity, capabilities, cooperative cancellation, timeout enforcement, input/output character limits, latency/resource metrics, and `OutputContract` validation. `LocalInference` rejects non-offline backends and wraps unexpected backend failures as explicit `InferenceError` subclasses. The deterministic `FakeInferenceBackend` is test-only and is marked as such; it is never presented as a real model.

No backend is registered or loaded implicitly. The filesystem index, search, graph, and CLI must remain fully functional when inference is disabled or unavailable. Model weights are optional and selected by the user; do not auto-download weights or require an external/cloud API.

### 7. Agent controller

**Implemented (v0, read-only):** `arceus_core.controller.CognitiveController` accepts a task and returns one validated `Proposal` from the permitted read-only set (`search_files`, `graph_children`, `graph_ancestors`, `ask_clarification`, `abstain`). It classifies the task deterministically, plans through the optional `LocalInference` boundary when a backend is provided, re-validates every model proposal against a strict typed contract, resolves all path arguments inside registered roots (model safety claims are never trusted), and falls back to an index-aware deterministic policy whenever the model is unavailable or invalid. It has no execute method; execution and verification are implemented separately (section 8).

Model outputs never bypass validation. Writes, installs, shell commands, moves, and deletion require explicit approval and narrowly scoped operations.

### 8. Bounded task workflow (implemented, Phase 5)

`arceus_core.executor.ProposalExecutor` executes an already validated proposal through the same application-level validation for every origin (model, deterministic policy, fallback), and only the three allow-listed read-only operations (`search_files`, `graph_children`, `graph_ancestors`) against the existing search/graph services; `ask_clarification`/`abstain` are terminal and never execute. `arceus_core.verifier.verify_execution` independently and deterministically checks the original task, the validated action, and the actual tool result (search hits must satisfy the query; children must be direct; ancestors must match the path-prefix chain; content questions can never be certified from metadata); uncertain evidence is downgraded to `unverified`/`incomplete` instead of forced success. `arceus_core.workflow.TaskWorkflow` performs at most one validated read-only tool action per task and returns a structured, attributable `WorkflowResult` with explicit origin, rejection stage, and verification status. The controller keeps its state machine implicit in these callers; every stage is bounded (single action, timeouts, evidence limits).

### 9. Memory and skills

A stored fact needs source/provenance, observed-at time, valid-time where relevant, confidence/status, and invalidation/update behavior. A skill needs preconditions, steps, expected effects, allowed tools, validation, version, and a test fixture. A previous success does not imply future safety.

### 10. Desktop UI

Only after the core APIs stabilize: evaluate Tauri + React + TypeScript with Rust adapters for native functionality. The UI should provide root selection, search results, provenance, permission controls, task traces, cancel, and confirmation for sensitive actions.

## Data flow

User approves a root → scan → metadata index → search/graph retrieval → optional on-demand, permission-checked read of an original file → model proposes an answer/plan → policy validator checks it → allowlisted tool executes → deterministic evaluator verifies → provenance-backed trace is saved.

## Cross-platform boundaries

Use `pathlib` for path semantics; isolate file watchers and OS open-file behavior behind adapters. Test Windows, Linux, and macOS in CI when feasible. A Windows-only run is not proof of cross-platform support.
