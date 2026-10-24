# Implementation Roadmap and Phase Gates

**Status (2026-10-24):** Phases 0–6.1 complete. Phase 2's metadata index, reconciliation, and search are implemented and tested on Windows. Phase 3 structural retrieval was evaluated on 5 development plus 10 held-out synthetic seeds (`graph-retrieval-v2`, 525 task instances): path-only search 64.4% overall / 64.9% held-out, graph-assisted and non-graph path-derived both 100% with identical evidence, so structural access is demonstrated but no graph-specific information advantage. Phase 4's offline inference boundary and Phase 5's bounded execute→verify workflow are implemented and measured with a real `qwen3.5:4b` (controller 12/12 end-to-end but only 1/12 model-generated; executor 10/12 model vs 12/12 deterministic). Phase 6/6.1 added episodic memory and verified skills with stale-revalidation and skill-servability benchmarks. **Phase 7 (BCR) is implemented and evaluated**: a frozen six-arm suite (`bcr-routing-v1`, 28 tasks) finds the no-model memory+skills stack at 14/14 on both splits versus 10/14 for BCR and 9/14 for model-first, with 0 unsafe executions and 0 false verified claims. The gate is therefore met for implementation and measurement, but **not** for a routing advantage over the no-model baseline. See `README.md`, `reports/arceus_phase7_budgeted_cognitive_routing.md`, and `reports/graph_retrieval_evaluation_v2.md`.

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

**Current evidence (2026-10-24):** delivered as `arceus_core.routing` with a frozen `bcr-policy-v1` and a six-arm `bcr-routing-v1` suite. The gate's fairness conditions hold: identical tasks, fixtures, seeds, budget, model config, and scoring across arms; capability-limited outcomes are separated from router mislabels; the per-task artifact and a preserved raw measurement file are checked in under `reports/`. The result is **negative for model use on metadata tasks**: memory+skills without a model 14/14 on both splits, BCR 10/14, model-first 9/14. Skill reuse is the only capability gain. Memory adoption and model-solved tasks are absent from the suite, so those claims remain untested.

**Next gate (Phase 7.1, not started):** pre-register an evidence-sufficiency gate (escalate to the model only on recoverable abstentions) and evaluate it on a NEW held-out suite that includes correct memory-adoption and model-solved tasks. Do not tune on the already-seen `bcr-routing-v1` held-out split; do not claim an improvement unless the gated router beats the no-model stack or ties it with a justified cost/latency trade-off.

## Phase 8 — Desktop application

**Deliverable:** UI for root permissions, search, source/provenance views, and task traces. Consider Tauri/React/Rust only now. Start with read-only actions and explicit roots.

**Gate:** UI uses documented core interfaces and does not introduce new powers that bypass policy. Test installer, permissions, paths, and update behavior separately by OS.

## Never bundle all phases into one agent task

At each gate: inspect status → propose plan → implement one goal → run tests → inspect diff → report exact results → commit only after human review.
