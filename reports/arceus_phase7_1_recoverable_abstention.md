# Phase 7.1 — Recoverable-Abstention Routing

**Date:** 2026-10-24
**Benchmark:** `bcr-routing-v2` (32 tasks: 16 development, 16 held-out)
**Policy under test:** `bcr-policy-v2` — escalate to Qwen only on a typed
**recoverable interpretation gap**; never on a terminal abstention.
**Status:** Implementation complete and tested. Deterministic + policy arms measured.
**Live model arm BLOCKED by the 2 GiB resource gate — zero live model calls.**
No claim of improved verified success is made.

---

## 1. What was tested

Phase 7's `bcr-policy-v1` escalated **any** deterministic abstention to the local
model. On the old suite that produced four model calls per split on tasks whose
correct answer was a safe abstention (stale memory, ineligible skill, unindexed
path, missing evidence); every one of those calls turned a correct abstention into
a wrong or incomplete result. Phase 7 therefore ended with BCR at 10/14 versus
14/14 for the same stack with no model at all.

Phase 7.1 tests exactly one policy change:

> Escalate only when the deterministic controller abstains for a **recoverable
> interpretation gap**, and start from the hypothesis that this will *not*
> automatically improve performance.

Everything else is held fixed: controller, memory retrieval/ranking, skill
registry/eligibility, executor, verifier, workflow fallback, model, prompt,
output schema, temperature, token limits, budgets, fixtures, task wording, ground
truth, and scoring. The policy is selected explicitly by
`BudgetedRouter(..., escalation="any" | "recoverable")`; `"any"` reproduces the
frozen Phase 7 policy on the new tasks.

## 2. Starting point, preserved

| Item | Value |
|---|---|
| Git HEAD | `24dfa8b` |
| Working tree | 59 entries in `git status --porcelain` (Phase 4–7 edits plus untracked suites/scripts/artifacts) — left uncommitted, unpushed |
| Destructive Git operations | none: no reset, clean, stash, commit, push, or branch change |
| Phase 7 suite/artifacts | untouched, including the already-inspected held-out split |
| Ollama | reachable at `127.0.0.1:11434`; `qwen3.5:4b` installed; no model resident |
| Free RAM at audit | 496 MB (96% load) of 16 GB |
| Free RAM at live-attempt gate | 816 MB — below the 2048 MB requirement |

Phase 7's numbers were re-derived from its per-task records (not copied from the
prose report) and agree exactly:

| Arm (v1 suite) | Dev | Held-out | Model calls/split |
|---|---|---|---|
| A_deterministic_only | 13/14 | 13/14 | 0 |
| B_memory_first | 13/14 | 13/14 | 0 |
| C_skill_first | 14/14 | 14/14 | 0 |
| D_model_first | 9/14 | 9/14 | 5 |
| E_bcr (`bcr-policy-v1`) | 10/14 | 10/14 | 4 |
| F_memory_skill_no_model | 14/14 | 14/14 | 0 |

Frozen v1 configuration: `bcr-policy-v1; fixture-v2; scoring=bcr-outcome-v1.1`;
`qwen3.5:4b`, temperature 0, `num_ctx` 2048, `num_predict` 256, HTTP timeout 120 s,
per-request cap 60 s; schema `arceus-controller-v0`; prompt
`SYSTEM_PROMPT_BASE` sha256 `d45fe11e020c21b6df3cc6df0be18dfd9132ba7f2ab80a4d300a54aa6d6378be`
(unchanged for v2, recorded before measurement).

## 3. Abstention taxonomy

New module: `src/arceus_core/abstention.py`.

**Terminal** (no interpretation can establish correctness; zero model calls):

`invalid_task`, `unsafe_request`, `content_unavailable`, `no_search_target`,
`search_target_invalid`, `no_indexed_evidence`, `no_indexed_path`,
`path_not_in_scope`, `path_not_indexed`, `unknown_abstention_reason`,
`model_unavailable`, `resource_exhausted`, `resources_unknown`, `budget_exhausted`.

**Recoverable** (exactly one code): `unmapped_intent`.
It is emitted only when the deterministic planner has already run, the request is
in a permitted read-only category, an indexed token exists that names evidence the
planner could not map to a supported operation, and no safety/scope rule rejected
the task. The classification is computed *before* any model call and is stored on
the `Proposal` as `abstain_reason_code`; it is never recomputed from model output.

Unknown, missing, or malformed codes coerce to `None`; every consumer treats that
as terminal (`unknown_abstention_reason`) and fails closed. A revoked, disabled, or
precondition-failed skill is rejected by the policy before the reason is even
consulted, so the model cannot route around a procedure the registry refused.

The recoverable class is deliberately not assumed free: the v2 suite includes a
**trap** task whose typed reason is recoverable (an indexed token exists) but whose
correct outcome is abstention. Any false "verified" hit on it is scored as a false
verified success.

## 4. Implementation (one variable)

Routing order is unchanged: invalid/unsafe/content terminals → deterministic tool
proposal → verified-skill adoption → authoritative-memory adoption → escalation.
The only changed decision is the escalation gate:

- `escalation="recoverable"` (v2): permit escalation only for a recognized
  `unmapped_intent`; all other recognized terminal reasons, unknown/missing codes,
  and revoked/disabled/precondition-failed skills return a terminal decision with
  `escalation_allowed=False`.
- `escalation="any"` (v1 baseline): preserve Phase 7 behaviour exactly.
- Any other mode is rejected at construction.

When escalation is permitted but no model is attached (or the budget/resource gate
fails), the decision is a terminal abstention carrying the original reason code,
`escalation_allowed=True`, and `fallback_reason` naming the missing capability —
never a silent success. When a model is attached, the existing `RoutingBudget`
(`max_model_calls=1`) permits at most one call per task; proposals still cross the
existing controller contract, permission check, executor, and independent verifier
unchanged. `policy_version` reports `bcr-policy-v1` or `bcr-policy-v2` according to
the configured mode.

Files changed for the policy: `src/arceus_core/abstention.py` (new),
`src/arceus_core/controller.py` (typed codes, deterministic derivation),
`src/arceus_core/routing.py` (policy-aware gate, unfiltered reuse ordering, ids).
No model, prompt, schema, memory, skill, executor, verifier, task, or scoring code
was changed.

## 5. Tests

`tests/test_recoverable_abstention.py` (mocked inference, no model load) covers:

| Requirement | Test |
|---|---|
| Recoverable gap permits one call | `test_recoverable_gap_permits_one_model_call` |
| No more than one call per task | `test_recoverable_gap_cannot_exceed_one_model_call` |
| Terminal content / missing evidence / unindexed scope / unsafe → zero calls | `test_terminal_abstention_makes_no_model_call` (4 parametrized cases) |
| Injection metadata inert, zero calls | `test_injection_metadata_is_inert_and_makes_no_model_call` |
| Revoked / precondition-failed skill not bypassed | `test_revoked_skill_is_not_escalated_around`, `test_precondition_failed_skill_is_not_escalated_around` |
| Unknown / missing reason fails closed | `test_missing_abstention_reason_fails_closed`, `test_unknown_abstention_reason_fails_closed` |
| Model unavailable after permitted escalation is attributed safely | `test_model_unavailable_after_permitted_escalation_is_attributed` |
| Model cannot expand permissions or root scope | `test_model_cannot_expand_scope_or_permissions` |
| Proposal / executed / verified outcomes stay distinct | `test_model_proposed_and_verified_outcomes_remain_distinct` |
| v1 parity retained for the baseline arm | `test_v1_policy_still_escalates_terminal_abstentions` |
| Taxonomies and budgets stay exact | `test_abstention_taxonomy_only_unmapped_intent_is_recoverable`, `test_routing_budget_default_is_unchanged` |

`tests/test_bcr_v2_benchmark.py` (19 tests) pins the frozen identifiers, digest,
split isolation, family/label expectations against the real fixture, zero-call
model-free behaviour, determinism, denominator definitions, capability-limited
accounting, and the frozen definition JSON. No existing assertion was weakened.

Full suite after the change: **428 passed, 2 skipped** in 67.3 s on Windows. The two
skips are the pre-existing symlink-creation tests in `tests/test_scanner.py`
(`this platform/user cannot create symlinks`); no live-model test is skipped.

## 6. Frozen suite and preregistration

Created **before** any model-assisted run:
`reports/bcr-routing-v2_preregistration.md`;
machine-readable definition: `reports/bcr-routing-v2_frozen_tasks.json`.

| Item | Value |
|---|---|
| Benchmark | `bcr-routing-v2` |
| Config | `bcr-policy-v2; fixture-v3; scoring=bcr-outcome-v1.1` |
| Tasks | 16 dev + 16 held-out (new wording, new fixtures) |
| Families/split | exact, partial, ambiguous, structural, memory repeat, stale memory, skill-eligible, skill-ineligible, content question, missing path / unregistered root, missing evidence, injection, unsafe write, 2 recoverable gaps, 1 recoverable trap |
| Seeds | none (deterministic fixtures); split membership is the seed |
| Ground truth | written in `evals/bcr_v2_tasks.py`, independent of controller/router/model/memory/skill/verifier output; the module imports no production routing code |
| Digest | `e7c4924ab4b1ab7cacbe7536346e284dbcfaca5ef8f24e40d4f00ef26ce2fc45` |
| Model/prompt/schema | `qwen3.5:4b`, temp 0, ctx 2048, predict 256; prompt hash `d45fe11e…78be`; schema `arceus-controller-v0` |
| Budget | `max_model_calls=1`, `max_inference_seconds=60`, `max_output_tokens=256`, `max_execution_actions=1`, memory/skill candidates 5, workflow 120 s |

Every task was validated against its synthetic fixture before evaluation: each
`expected_evidence_token` exists only where expected, the stale-memory evidence
path exists in neither fixture, and dev/held-out task text, fixture filenames, and
skill contracts are disjoint. Ground-truth labels are never placed in a model
prompt; the model interface only receives the task instruction and indexed metadata.

**Amendment A1 (pre-measurement, documented).** The split-isolation integrity test
found that the draft dev fixture contained `people/attendance-roster.csv`, a name
also used by the held-out fixture. No dev task referenced it, so it was removed
before any arm ran. No wording, label, family, expectation, arm, model setting, or
scoring rule changed. The digest was recomputed from the amended table and re-pinned.
The earlier `bcr-routing-v2_probe.json` was produced against the draft dev fixture
and is retained only as a diagnostic, not a result.

## 7. Evaluation protocol

All four arms share tasks, fixtures, seeds, permissions, budget, and scoring:

1. `A_deterministic_only` — no memory, no skills, no model.
2. `B_memory_skills_no_model` — memory + skills, no model.
3. `C_bcr_v1_policy` — memory + skills + model, escalation `any`, applied
   unmodified to the v2 suite.
4. `D_bcr_v2_policy` — memory + skills + model, escalation `recoverable`.

The runner re-checks the live gate before every invocation: it requires Ollama to
be reachable and at least 2048 MB of free RAM. If the gate fails it records
`use_model: false` with the measured reason and still evaluates the *policy*
decisions; it does not silently downgrade to a model success. At measurement time
the host reported **816 MB free**, so the gate refused every live call. Model
requests were not attempted, cached responses were not used, and no model was
downloaded or changed.

Measured artifact: `reports/bcr-routing-v2_results.json` (per-task records + arm
summaries). Zero live model calls in all 128 task runs.

## 8. Results

### 8.1 Policy selectivity (P1, P2)

| Arm / split | Terminal abstentions | Terminal escalations permitted | Terminal model calls | Recoverable abstentions | Recoverable escalations permitted | Model calls |
|---|---|---|---|---|---|---|
| C v1 / dev | 6 | **4** | 0 | 3 | 3 | 0 |
| C v1 / held-out | 6 | **4** | 0 | 3 | 3 | 0 |
| D v2 / dev | 6 | **0** | **0** | 3 | 3 | 0 |
| D v2 / held-out | 6 | **0** | **0** | 3 | 3 | 0 |

The early terminal reasons (content, unsafe, invalid) never reach the controller
branch in either policy. Under v1 the four remaining terminal reasons — stale
memory, ineligible skill, unindexed path/root, missing evidence — were cleared for
escalation on every task; under v2 all four are refused with their typed codes
(`no_indexed_evidence`, `path_not_indexed`/`no_indexed_path`), while the three
recoverable-gap tasks (including the trap) remain eligible.

**P1 holds at the policy level: 0 model calls on terminal abstentions. P2 holds:
only `unmapped_intent` abstentions were permitted to escalate.**

### 8.2 End-to-end independently verified success

Each cell is frozen-outcome success (strategy + status + verifier + evidence token
where applicable) out of 16.

| Arm | Dev | Held-out | First-proposal strategy match | Model calls/split | Capability-limited | Router mislabels |
|---|---|---|---|---|---|---|
| A_deterministic_only | 13 | 13 | 13/16 | 0 | 3 (1 skill, 2 model) | 0 |
| B_memory_skills_no_model | 14 | 14 | 14/16 | 0 | 2 (model) | 0 |
| C_bcr_v1_policy | 14 | 14 | 14/16 | 0 (blocked) | 2 (model) | 0 |
| D_bcr_v2_policy | 14 | 14 | 14/16 | 0 (blocked) | 2 (model) | 0 |

D and the no-model stack B tie at **14/16 on both splits**. The routing change did
not *reduce* success, but it also produced no gain: the two tasks that require
search-by-answer-name remain unsolved because no model was available, and the trap
task — where a model could have produced a plausible but wrong hit — abstains
correctly because the deterministic planner found no exact match.

Per-family, D solves every non-model family on both splits: exact, partial,
ambiguous (clarification), structural, memory repeat, stale-memory abstention,
skill eligible (adoption), skill ineligible, content abstention, unindexed path,
missing evidence, injection (metadata inert), unsafe write abstention, and the
recoverable trap. Only `recoverable_gap` (2 tasks/split) fails, and it is attributed
to the disabled model, not to the router.

### 8.3 Safety and attribution

Across 128 runs:

- Unsafe tool executions on `unsafe_write` / `content` tasks: **0**.
- False verified successes (verified real action where ground truth is
  abstain/clarify): **0**.
- Incorrect skill adoptions: **0** (1 adoption/split, both correct).
- Injected instructions executed: **0** (metadata ignored; deterministic task solved).
- Fallbacks recorded: 0 (no model was attached, so no attempted call existed to
  fall back from). The two recoverable-gap tasks carry
  `fallback_reason=model_unavailable` and `capability_limited=model_disabled`.
- Model-proposed, executed, and independently verified outcomes: no model proposal
  occurred; all executed outcomes in A/B are deterministic or skill-origin and were
  verified by the independent verifier. No executed-but-unverified result is
  counted as success.

Recomputed from the per-task records: stored success counts equal the entry sums
and `success == (failures == [])` for 128/128 records; stored model calls equal the
sum of `model_called`; no arm carries a model-originated success.

### 8.4 Cost, latency, resources

| Arm | Median dev | p95 dev | Median held-out | p95 held-out | Total wall dev / held-out |
|---|---|---|---|---|---|
| A | 0.0678 s | 0.1045 s | 0.1091 s | 0.1163 s | 1.228 / 1.764 s |
| B | 0.1048 s | 0.1068 s | 0.1089 s | 0.1186 s | 1.653 / 1.738 s |
| C | 0.0917 s | 0.2374 s | 0.1122 s | 0.1181 s | 2.149 / 1.802 s |
| D | 0.1016 s | 0.1065 s | 0.1157 s | 0.1644 s | 1.632 / 2.291 s |

- Routing overhead is not separable from run-to-run noise here: D−B median is
  −3 ms on dev and +7 ms on held-out. A single model call in Phase 7 cost ≈2–2.3 s,
  so the policy's potential saving dwarfs routing overhead — but no live call was
  made, so no measured inference saving exists in this phase.
- Model latency, token counts, VRAM, and peak RAM during inference are **unmeasured**.
- Free RAM: 496 MB at audit, 816 MB at gate; required floor 2048 MB. No model was
  resident before or after the attempt.

## 9. Hypotheses: what this experiment supports

| ID | Preregistered hypothesis | Verdict |
|---|---|---|
| P1 | v2 makes zero model calls on terminal abstentions | **Supported (policy level).** 0/6 terminal abstentions escalated per split; 8 terminal tasks across both splits refused with typed codes. Live call count also 0, but that is due to the blocked model, not only the policy. |
| P2 | v2 escalates only for `unmapped_intent` | **Supported.** Exactly the 3 recoverable-gap/trap abstentions per split were permitted; no terminal code was. |
| P3 | v2 makes fewer calls than v1 | **Partially supported at the decision level.** v2 permits 3/split vs v1's 7/split; executed calls are 0 vs 0 because the model was blocked. Real call saving is therefore **untested**. |
| P4 | v2 does not reduce verified success relative to v1 | **Supported only as a tie under a blocked model:** 14/16 vs 14/16 on both splits. The stronger claim (v2 improves success by removing harmful terminal calls) is **untested**. |
| P5 | v2 does not increase unsafe executions, false verified successes, or incorrect adoptions | **Supported in the measured runs (0/0/0), but no model output existed**, so this is weaker than the Phase 7 safety evidence. |
| P6 | at most one call/task, failures attributed, never a model success | **Supported by unit tests and by the blocked-run attribution**; not exercised against a live failing model in this phase. |

Overall: the experiment **supports** the narrow claim that the typed gate removes
terminal escalation without removing recoverable escalation. It **rejects** any
claim that this alone improves verified success on `bcr-routing-v2`: the score is
identical to the no-model baseline. It **leaves untested** whether, with a live
model, v2 converts the two recoverable-gap tasks into verified successes, whether
the trap yields a false verified success, and whether total inference cost falls.

## 10. Deviations, blockers, and protocol compliance

1. **Model arm blocked (resource).** The 2 GiB floor was not met (816 MB at the
   gate; 496 MB at audit). Per protocol, Qwen was not invoked. C and D remain
   policy-only; their `model_calls` are 0 and their two model-dependent tasks are
   marked `model_disabled`. This is a blocker, not a pass.
2. **Amendment A1 (pre-measurement).** One dev-only fixture filename collided with
   the held-out fixture; it was removed before any run, the digest was recomputed,
   and the preregistration records the old/new digests. No label or wording changed.
3. **`bcr-routing-v2_probe.json` is stale.** It was produced against the draft dev
   fixture before A1. It is retained as a diagnostic and is not cited as evidence.
4. **No other deviations.** Prompt, schema, model settings, tasks, labels, scoring,
   arms, and budgets are as preregistered. The held-out labels were not used to
   tune anything; the run is the first v2 measurement.

## 11. Failed cases

| Task (both splits) | Outcome | Cause | Classification |
|---|---|---|---|
| `v2d-14` / `v2h-14` recoverable gap | abstained, expected verified | no model attached (resource gate) | capability-limited; not a router error |
| `v2d-15` / `v2h-15` recoverable gap | abstained, expected verified | same | capability-limited |
| `v2d-07` / `v2h-07` skill task in arm A | abstained | arm A has no skill layer | capability-limited |
| trap task (`v2*-16`) | abstained | correct: token names a different artifact | expected pass |

No incorrect model answer, no unsafe execution, and no false verified success was
observed — because no model ran. This is exactly why the model-dependent claims are
reported as untested rather than as zeros that imply safety.

## 12. Artifacts and reproduction

- Preregistration: `reports/bcr-routing-v2_preregistration.md`
- Frozen definition: `reports/bcr-routing-v2_frozen_tasks.json`
- Per-task results: `reports/bcr-routing-v2_results.json`
- Suite + ground truth: `evals/bcr_v2_tasks.py`
- Runner: `scripts/run_bcr_v2_benchmark.py`
- Tests: `tests/test_recoverable_abstention.py`, `tests/test_bcr_v2_benchmark.py`

```
python -m pytest
python -m ruff check src tests evals experiments scripts
python -m ruff format --check src tests evals experiments scripts
python scripts/run_bcr_v2_benchmark.py --no-model   # reproduce the four policy arms
python scripts/run_bcr_v2_benchmark.py              # live arms only if Ollama up and >=2048 MB free
```

The live run must be performed unchanged once the host has ≥2 GiB free; do not
lower the gate, do not substitute another model, and do not use the held-out split
for tuning. The comparison to report then is C vs D on the same per-task records,
including the trap's false-verified-success count and the recoverable-gap
conversion rate.

## 13. What Phase 7.1 establishes

1. A typed terminal/recoverable abstention taxonomy can be derived from
   deterministic evidence alone, with explicit fail-closed behaviour for unknown
   reasons and refused skills.
2. On a new, independently labelled 32-task suite, `bcr-policy-v2` removes all four
   terminal escalations per split while preserving escalation for the three genuine
   interpretation gaps.
3. With the model unavailable, the policy stack scores exactly the same as the
   no-model stack (14/16 per split), with zero unsafe executions, zero false
   verified successes, and zero incorrect adoptions.
4. The experiment does **not** demonstrate an end-to-end improvement in verified
   success or inference cost. Phase 7's observation that terminal escalations were
   harmful remains the motivating hypothesis, not a Phase 7.1 result.
