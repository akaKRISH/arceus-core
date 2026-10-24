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

## ADR-0011 — Evaluate graph retrieval on fixed, independently labeled tasks

- **Status:** Accepted; first experiment run.
- **Decision:** Version a deterministic synthetic fixture and a 35-task suite (`graph-retrieval-v1`) with ground truth derived from an independent fixture model. Compare the production search API (`path_only`) with search plus the production graph API (`graph_assisted`) on identical tasks. Score task-level exact set success, top-1 where one target exists, macro target/evidence precision/recall/F1, and no-result false positives; report latency separately and do not treat it as a performance claim.
- **Reason:** The Phase 3 gate requires explicit ground truth and a defined metric. Deriving labels from a separate model avoids grading the graph with its own algorithms, and task-level macro averaging prevents a task with many targets from dominating.
- **Trade-off:** The graph system receives structured relationship inputs while the baseline receives query text; the result measures a capability difference, not ranking quality. The suite is small, synthetic, single-seed, and Windows-only, and its evidence vocabulary matches graph output.
- **Review trigger:** Any claim beyond structural capability on this fixture requires multi-seed held-out tasks and a string-post-filter baseline before Phase 4.

## ADR-0012 — Treat structural access, not the graph API alone, as the measured advantage

- **Status:** Accepted; Phase 3.1 evidence.
- **Decision:** Keep the production graph API for direct structural queries and observed-parent provenance. Do not claim that it provides unique retrieval information over scalar metadata plus string path reconstruction. Add `path_derived` as a permanent control and evaluate development and held-out seeds separately.
- **Reason:** Across 15 synthetic seeds and 525 task instances, `graph_assisted` and `path_derived` both achieved 525/525 success and 1.0 target/evidence macro F1. Ordinary path-only search achieved 338/525 (64.4%) overall and 227/350 (64.9%) on held-out seeds; all improvements over path-only were in structural categories. The graph did not beat the path-derived control on any task.
- **Trade-off:** The graph layer remains more direct and uses the scanner's observed parent keys, but its advantage over string reconstruction is an interface/maintainability argument that this experiment did not measure. Path-derived reloads the full scalar index per query and was slower in this run; latency is exploratory.
- **Review trigger:** New evidence where observed parent rows differ from path-derived relations, a controlled maintainability study, or a scale test where the two implementations diverge. Phase 4 requires a separate model/hardware decision and must not claim a graph-information advantage from this result.

## ADR-0013 — Optional typed inference boundary before runtime installation

- **Status:** Accepted; boundary implemented, runtime/model selection remains pending.
- **Decision:** Add `arceus_core.inference` as a small, dependency-free, typed boundary for future optional local inference. Define requests, responses, capabilities, timeouts, cancellation, limits, measurements, structured-output contracts, and explicit error types. Reject non-offline capabilities. Provide `FakeInferenceBackend` as a deterministic test double only.
- **Reason:** The model runtime and weights are not selected yet, while failure behavior and offline guarantees can be specified now. Isolating the boundary first prevents the filesystem index and CLI from acquiring accidental model, cloud, or vector-database dependencies.
- **Trade-off:** No real model, runtime adapter, or measured latency is included. The fake backend validates the boundary but cannot estimate model quality, RAM/VRAM use, or speed.
- **Review trigger:** Before installing any runtime or downloading weights, approve a candidate from the hardware/model selection report and add a concrete backend adapter. Do not claim real-model reasoning until measurements exist on this machine.

## ADR-0014 — Read-only proposal-only controller v0 with deterministic fallback

- **Status:** Accepted; implemented and tested.
- **Decision:** Add `arceus_core.controller.CognitiveController` as a strictly read-only proposal engine. Permitted actions are `search_files`, `graph_children`, `graph_ancestors`, `ask_clarification`, and `abstain`; there is deliberately no execute method. Planning may use the optional `LocalInference` boundary, but every model proposal is re-validated against a strict typed contract (`{action, arguments, rationale}`, known actions only, unknown keys rejected), path arguments must resolve inside registered roots, and the first line of a task is treated as the instruction while lines under an `Untrusted metadata:` marker are inert data. Any model failure falls back to an index-aware deterministic policy that queries the index for ambiguity (multiple matches → clarification) and missing evidence (no match → abstention).
- **Reason:** The frozen `model-reasoning-v0` measurement showed `qwen3.5:4b` fails strict JSON-contract compliance (1/12 vs a 2/12 deterministic baseline), so a v0 controller cannot rely on model output. A safe proposal engine with a deterministic fallback delivers usable value while the model-side failure mode is addressed.
- **Trade-off:** The controller is only as smart as its deterministic policy; the model arm of the frozen `controller-action-v0` benchmark scored 12/12 end-to-end but only 1/12 of proposals were actually model-generated, so the current system is effectively deterministic with optional decoration. Safety-task model behavior remains unverified because the model's proposals fail validation before reaching the safety policy.
- **Review trigger:** Re-evaluate when model-generated proposals pass the strict contract at a measured rate, when execution (Phase 5 state machine) is added, or when a task genuinely needs a permitted-but-unimplemented read-only action.

## ADR-0015 — Deterministic-first Budgeted Cognitive Routing with an enforced per-task budget

- **Status:** Accepted; implemented, frozen, and evaluated once (`bcr-routing-v1`).
- **Decision:** Add `arceus_core.routing.BudgetedRouter`, a thin deterministic policy over the existing controller, memory, skills, and optional inference. It returns an explicit typed `RoutingDecision` (strategy, reason, evidence, configured budget, measured resource snapshot, model eligibility, terminal flag, policy version, fallback reason) and executes nothing. Ordering is deterministic-first; the model is a last resort behind a budget/resource gate. Skill eligibility is evaluated for every task so rejections stay observable, but only abstentions may be replaced by reuse. `RoutingBudget` is enforced by `BudgetedInference` (call count, request-timeout cap) and by bounded candidate counts, one execution action, and an audited wall-time bound. Model proposals keep crossing the unchanged controller/permission/executor/verifier boundaries.
- **Reason:** The research question is verified success per unit of compute, so the router must be able to decline the model and must record why. A deterministic-first policy is inspectable, testable without a model, and satisfies the requirement that no routing decision rest on model-generated confidence.
- **Trade-off:** The first policy escalates to the model whenever the deterministic controller abstains, which conflates a correct terminal abstention with a coverage gap. On `bcr-routing-v1` this made the model-using router worse than the same stack without a model (10/14 vs 14/14 on both splits; 4 vs 0 calls) while model-first was 9/14 with 5 calls. The router still dominates model-first at lower cost, and the defect is a policy design issue, not an implementation one.
- **Review trigger:** Phase 7.1 must add an evidence-sufficiency gate (escalate only on recoverable abstentions) and evaluate it on a NEW held-out suite. Do not tune `bcr-policy-v1` on the already-seen `bcr-routing-v1` held-out split.

## ADR-0016 — Narrow false-verified metric and provenance-preserving re-scoring

- **Status:** Accepted; applied to `bcr-routing-v1` (`bcr-outcome-v1.1`).
- **Decision:** A false verified success requires `status=verified`, a real tool action, `task_completed=True`, and a task whose ground truth is abstain/clarify. A correct abstention is a completed task and is not a breach. When a metric DEFINITION is corrected, derived fields may be recomputed from preserved per-task measurements with `--rescore`, but the raw measurement artifact must be kept, the artifact must record `rescored_from`/`rescored_at`/`original_config`, the scoring component of the frozen configuration must change version, and no measured field may be altered.
- **Reason:** The first Phase 7 run reported 7 false verified claims per deterministic arm-split, all of which were correct abstentions. A metric that mislabels correct safety behaviour creates pressure to weaken abstention to improve scores. Re-measuring was impossible later in the session because free RAM fell below the serial model-run floor, while the per-task records contained everything needed to recompute a derived field.
- **Trade-off:** Phase 6.1's `false_verified_claims` uses the broader definition and reports 1 where Phase 7 reports 0 (an empty verified search with uncertain completion). Both artifacts are preserved; reports must state which definition is in force. Re-scoring cannot catch a bug that lives in the measurement itself, only in the derivation.
- **Review trigger:** Any further change to success or safety definitions must bump the scoring version and preserve the previous artifact; if a measurement field is found wrong, re-run rather than re-score.
