# Implementation Roadmap and Phase Gates

**Status (2026-10-23):** Phase 0 complete. Phase 1 complete. Phase 2 complete on Windows (metadata index, reconciliation, search, tests). Phase 3 structural layer implemented and evaluated on 5 development plus 10 held-out synthetic seeds (`graph-retrieval-v2`, 525 task instances). Path-only search scored 64.4% overall and 64.9% on held-out seeds; both graph-assisted retrieval and a non-graph path-derived baseline scored 100% with identical evidence. The gate is met for structural access, but not for a graph-specific information advantage; full evidence and limitations are in `reports/graph_retrieval_evaluation_v2.md`. Phase 4 boundary, test backend, synthetic model-task definitions, and candidate research are complete; no runtime or model weights have been installed. See `README.md`, `reports/2026-10-23_filesystem_baseline.md`, and `reports/2026-10-23_local_model_selection.md`.

Use the prompts in `prompts/` one at a time. The order matters: deterministic correctness first, model sophistication later. Estimated order is more reliable than calendar dates; do not rush through failed gates.

## Phase 0 — Environment audit

**Deliverable:** written report only. OS, Python, Git, current repo status, RAM, GPU/VRAM/driver if detectable, disk space, available commands. No code edits or installations.

**Gate:** actual environment known; no assumption that an NVIDIA GPU or runtime is configured.

## Phase 1 — Project foundation

**Deliverable:** Git-ready docs, `.gitignore`, Python 3.12 package scaffold, `pyproject.toml`, `pytest` setup, configuration/data paths, basic health/version command. Keep runtime dependencies minimal.

**Gate:** virtual environment can install the project and basic tests pass.

## Phase 2 — Filesystem index and search (MVP)

**Deliverable:** explicit roots, metadata scan, SQLite schema, path/name search, scan status/errors, change reconciliation, symlink-safe behavior, test fixtures.

**Gate:** re-open DB and find the same results; no contents copied; paths with spaces/Unicode work; errors do not corrupt or silently erase index state; tests pass.

## Phase 3 — Graph-aware retrieval

**Deliverable:** query API over roots, parent-child relationships, path ancestry, evidence labels, a frozen multi-seed evaluation, and a non-graph path-derived control.

**Gate:** graph queries have explicit ground truth and improve a defined metric over path search alone, or remain a simple structure without claims of improvement.

**Current evidence:** graph-assisted retrieval beats ordinary path-only search on structural categories, but ties the path-derived control on every task in the current suite. Any later claim that the graph adds unique retrieval information requires new evidence.

## Phase 4 — Local model adapter

**Deliverable:** optional adapter interface, model identity/config, timeouts, cancellation, structured output parsing, and resource logs. Select runtime/model only after inspecting GPU VRAM and testing a small model safely.

**Gate:** deterministic features still run without a model; no model weights download without user approval; model errors/timeouts degrade gracefully.

**Current evidence:** `arceus_core.inference` implements the typed boundary and offline enforcement; the deterministic fake backend covers success and failure paths in tests. The candidate is Ollama plus Qwen2.5 3B Instruct in Q4_K_M, with llama.cpp plus Qwen2.5 1.5B as the fallback. Neither has been installed or run, so neither is a verified implementation. Next gate: obtain user approval, install a runtime, validate the first small model offline, then run `model-reasoning-v1` with a real adapter.

## Phase 5 — Bounded agent loop

**Deliverable:** state machine for propose → validate → execute → verify; task budgets; maximum steps; cancellation; read-only tools only at first; structured trace.

**Gate:** model cannot invoke a non-allowlisted tool, escape the approved scope, or claim success without a validator result. Add adversarial prompt-injection tests using file content as untrusted input.

## Phase 6 — Memory and skills

**Deliverable:** time-stamped, provenance-aware episodes/facts; update and invalidation logic; versioned skills with preconditions, expected effects, and validation.

**Gate:** compare against a no-memory/no-skill baseline. If stale memory causes wrong actions, change/reject the memory design before expanding it.

## Phase 7 — Budgeted Cognitive Routing (BCR) experiments

**Deliverable:** a fixed-policy baseline, transparent heuristic router, task registry, repeatable experiment runner, JSONL/CSV logs, reports for success/latency/RAM/storage/safety.

**Gate:** comparison holds model, prompts, task set, and hardware constant where possible; test failures and negative results are recorded; no novelty claim before literature review.

## Phase 8 — Desktop application

**Deliverable:** UI for root permissions, search, source/provenance views, and task traces. Consider Tauri/React/Rust only now. Start with read-only actions and explicit roots.

**Gate:** UI uses documented core interfaces and does not introduce new powers that bypass policy. Test installer, permissions, paths, and update behavior separately by OS.

## Never bundle all phases into one agent task

At each gate: inspect status → propose plan → implement one goal → run tests → inspect diff → report exact results → commit only after human review.
