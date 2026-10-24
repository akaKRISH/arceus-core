# Changelog — ARCEUS (research milestones)

This project has **no released software versions yet**. This file records
verified *research milestones* — implemented, tested, and evaluated work —
in the order they were completed. Each entry cites the frozen benchmark or
report that backs it. Negative and untested results are recorded as such.

## 2026-10-24 — Phase 7.1: recoverable-abstention routing (`bcr-routing-v2`)

- Implemented a typed abstention taxonomy (`src/arceus_core/abstention.py`):
  15 terminal reason codes + one recoverable code (`unmapped_intent`),
  derived from deterministic evidence only, with explicit fail-closed
  coercion for unknown/missing reasons.
- New routing policy `bcr-policy-v2`: escalate to the local model only on a
  recoverable interpretation gap; terminal abstentions and refused
  (revoked/disabled/precondition-failed) skills make zero model calls.
  `escalation="any"` reproduces the frozen Phase 7 policy unchanged.
- Frozen, pre-registered 32-task suite (16 dev + 16 held-out) with a
  deliberately adversarial "trap" task whose typed reason is recoverable but
  whose correct outcome is abstention.
- Measured (all four arms, zero live model calls — host below the 2 GiB
  free-RAM gate both at audit and at the gate): the v2 policy permits 3
  escalations/split vs 7/split under v1, with **0 terminal escalations**; the
  policy stack scores 14/16 per split, identical to the no-model baseline.
- **Untested end-to-end:** whether the live model converts the recoverable-gap
  tasks into verified successes and avoids the trap's false-hit risk.
  `reports/arceus_phase7_1_recoverable_abstention.md`.

## 2026-10-24 — Phase 7: budgeted cognitive routing (`bcr-routing-v1`)

- Implemented `BudgetedRouter` (`src/arceus_core/routing.py`): frozen-policy
  routing decisions over deterministic-first ordering (tool proposal →
  clarification → verified skill → fresh memory → gated local model → safe
  abstention), with measured resource snapshots and per-task `RoutingBudget`
  enforcement (`max_model_calls=1`, inference-time and token caps).
- First frozen evaluation on a new 28-task suite, six capability arms:
  - memory + skills without a model: **14/14 dev, 14/14 held-out**
  - verified-skill-first: 14/14 (the only measured capability gain, +1 task)
  - full BCR: 10/14 with 4 model calls/split
  - model-first: 9/14 with 5 calls/split
  - zero unsafe executions, zero false verified successes, zero incorrect
    skill adoptions across 168 runs.
- **Rejected hypothesis:** model escalation on *any* deterministic abstention
  reduced verified success; every model call replaced a correct abstention.
  `reports/arceus_phase7_budgeted_cognitive_routing.md`.

## 2026-10-24 — Phase 6 + 6.1: episodic memory and verified skills

- `src/arceus_core/memory.py`: provenance-backed episodic memory with dedupe,
  retention, authoritative/verified trust rules, and re-validation of stored
  evidence against the current index (stale evidence is rejected, never acted on).
- `src/arceus_core/skills.py`: skill registry that promotes a procedure only
  after 3 distinct verified runs, enforces input-signature contracts and
  preconditions, and supports revocation.
- Frozen evaluations: `memory-lifecycle-v1` 9/9 with 7/7 stale detections and
  0 stale adoptions; `skills-servable-v1` 11/12 for skills/memory+skills vs
  5/12 baseline; memory retrieval recall 12/12, precision 12/60.
  `reports/arceus_phase6_memory_skills.md`,
  `reports/arceus_phase6_1_memory_generalization.md`.

## 2026-10-24 — Phase 5: bounded execution and independent verification

- `executor.py` / `verifier.py` / `workflow.py`: one task → one validated
  proposal → at most one allow-listed read-only action → evidence collection →
  independent deterministic verification, with full model/fallback attribution.
- Frozen `executor-workflow-v0`: deterministic arm 12/12 (8 executed, all
  independently verified, 0 unsafe); real-model arm (qwen3.5:4b) 10/12 with
  both wrong proposals caught by the verifier, 0 fallbacks.
  `reports/arceus_phase5_execution_verification.md`.

## 2026-10-24 — Phase 4 + 4.1: cognitive controller and model-proposal reliability

- `CognitiveController` — strictly read-only proposal engine; path proposals
  must resolve inside registered roots; content under an `Untrusted metadata:`
  marker can never change the route; invalid/unsafe model output falls back to
  the deterministic policy with the stage recorded.
- Frozen `controller-action-v0`: deterministic arm 12/12 with 0 unsafe
  proposals; real-model arm 12/12 end-to-end but only 1/12 proposals actually
  model-generated (deterministic fallback carried the rest).
  `reports/2026-10-24_phase4_1_model_proposal_reliability.md`.

## 2026-10-23 — Phases 1–3: index, search, graph retrieval, inference boundary

- Metadata-only SQLite index of explicitly registered roots; conservative
  reconciliation; ranked name/path search; structural graph queries.
- Optional offline inference boundary (`LocalInference`) with a loopback-only
  Ollama adapter; filesystem features never require inference.
- Frozen `graph-retrieval-v2` (525 task instances): graph-assisted and a
  path-derived control both scored 525/525 vs 338/525 for name/path search —
  a capability difference on structural access, and a **substantive negative
  result**: the graph holds no unique structural information the strings
  don't already carry on these fixtures.
- `model-reasoning-v0`: qwen3.5:4b scored 1/12 vs 2/12 deterministic baseline
  under strict JSON output contracts. `reports/graph_retrieval_evaluation_v2.md`,
  `reports/2026-10-23_qwen_validation_report.md`.

## Supporting baseline

- Filesystem index benchmarks (2k/20k synthetic files): first scan 1.746 s at
  20k entries, search median 12–23 ms, ~466 bytes/entry in SQLite.
  `reports/2026-10-23_filesystem_baseline.md`.
