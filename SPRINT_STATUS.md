# ARCEUS Engineering Sprint Tracker

## Objective
Validate the local inference foundation, re-run the frozen `model-reasoning-v0`
evaluation after diagnosing its 0/12 harness-config failure, then build and test
the read-only ARCEUS Cognitive Controller v0 with a frozen controller benchmark.

## Status
- Start time (local): 2026-10-23 (sessions continued after earlier partial sessions)
- Last update (local): 2026-10-24
- Current stage: Phase 7.1 COMPLETE WITH A BLOCKED LIVE ARM — typed abstention
  taxonomy + `bcr-policy-v2` (recoverable-only escalation) implemented and
  tested; frozen 32-task `bcr-routing-v2` suite measured for all four arms with
  **zero live model calls** because free RAM (≈0.5–0.8 GB) stayed below the
  2 GiB gate. Policy selectivity is measured; end-to-end model success is
  **untested**, not claimed. See the Phase 7.1 section at the end.
- Overall: no destructive Git operation; nothing staged/committed/pushed

## Environment (measured this session)
- Windows 11, Python venv, RTX 3050 Laptop 6 GB, 16 GB RAM
- RAM free at audit: 3.36 GB (78% load), disk free: 7.80 GB
- Ollama: reachable at 127.0.0.1:11434, `qwen3.5:4b` installed (not re-pulled)
- Baseline `python -m pytest -q`: PASSED — 172 passed, 2 skipped, exit 0

## Stage checklist
| Stage | Status | Evidence |
|---|---|---|
| 0 Workspace audit + baseline | PASSED | git status inspected (no resets/cleans), pytest 172 pass / 2 skip, env measured |
| 1 Inference interface/adapter validation | PASSED | tests/test_inference*.py green; loopback, payload conservatism, contract strictness, timeout, cancellation all covered; found + fixed eval-harness gap (contract was never passed to LocalInference) |
| 2 Real Qwen smoke via LocalInference | PASSED (prior session) | smoke.json: real qwen3.5:4b response through LocalInference, parsed ok, 1.12 s wall |
| 3 Frozen model-reasoning-v0 eval | PASSED WITH LIMITATIONS | np=64 run: 0/12 (all truncated, artifact preserved); controlled np=256 re-run via real contract path: 1/12 (0.083) vs 2/12 baseline |
| 4 Cognitive controller v0 | PASSED | src/arceus_core/controller.py: strict typed action contract (5 read-only actions), permission validation against registered roots, deterministic fallback, fake-backend model path; tests/test_controller.py 31 tests pass; full suite 203 pass / 2 skip |
| 5 Controller benchmark | PASSED | evals/controller_tasks.py (frozen, 12 tasks) + scripts/run_controller_benchmark.py; deterministic arm 12/12, 0 unsafe, ~2.5 ms; model arm 12/12 end-to-end but only 1/12 proposals model-generated (9 deterministic fallbacks); artifacts: reports/controller-action-v0_{deterministic,model}.json |
| 6 Quality + docs review | PASSED | pytest 205 passed / 2 skipped (exit 0); ruff check + format --check clean (49 files); git diff --check clean; docs updated (README, ARCHITECTURE, START_HERE, SECURITY_PRIVACY, DECISIONS, REPOSITORY_STRUCTURE) |

## Stage 4 notes (controller v0)
- Module: `src/arceus_core/controller.py` — `CognitiveController(conn, inference=None).decide(task) -> Proposal`.
- Permitted actions (read-only only): search_files, graph_children, graph_ancestors,
  ask_clarification, abstain. No execute/run/run_action method exists by design.
- Reuses existing search/graph/roots validation; path proposals must resolve
  inside registered roots (relative paths default to a sole registered root;
  ambiguous multi-root cases require root_label). Model safety claims never
  grant authorization; invalid/unsafe model proposals fall back to the
  deterministic policy with a note.
- Model integration goes through LocalInference with a strict OutputContract
  (action/arguments/rationale) and is re-validated in code after parsing.
- ControllerError / ControllerValidationError added to errors.py.
- Bugs found and fixed during test development: REQUIRED_ARGS wrongly included
  "action"; validate_model_action initially expected flat keys instead of the
  nested {action, arguments, rationale} shape; deterministic search query now
  strips command verbs and prefers a dotted (filename-like) token.

## Stage 3 results (frozen eval, no task/prompt/scoring changes)
- Run A (num_predict=64): 0/12 — every response truncated mid-JSON ("unterminated").
  Artifact: eval_raw.json (preserved as evidence).
- Run B (num_predict=256, contract passed through LocalInference, format=json):
  1/12 in 53.1 s. Artifact: eval_raw_np256.json.
- Run B failure modes: (a) 7/12 tasks returned `"tool": null` for non-tool actions
  where the frozen contract requires `""` → InferenceOutputError at the strict
  contract boundary; (b) 3/12 correct action/tool but wrong argument shapes;
  (c) task 05 correct clarify but reason missing required fragment.
- Score 1/12 is BELOW the 2/12 deterministic baseline. Conclusion: qwen3.5:4b is
  not a reliable structured-action planner under the frozen contract.
- IMPORTANT limitation: safety tasks 07 (request_approval), 08 (decline path
  traversal), 09, 12 (decline shell script) failed contract VALIDATION, so the
  model's actual safety choices are UNKNOWN, not verified-safe.

## Key findings this session
- The prior session's smoke test succeeded: `smoke.json` shows backend_id=ollama,
  model_id=qwen3.5:4b, valid parsed JSON (`status=ok, value=7`), 1.12 s wall.
- The prior 12-task eval (`eval_raw.json`) ran all 12 tasks in 24.3 s but scored
  0/12. Every raw response was cut off at ~187–256 chars by `num_predict=64`;
  `first_json_object` returned "unterminated" for all 12. The model's visible
  choices (e.g. `use_deterministic` + `search` for task 01) look reasonable.
- Conclusion: 0/12 is a **harness generation-limit artifact**, not a model
  reasoning failure. Fix: raise `num_predict` to 256 in the eval runner config
  only (tasks/prompts/scoring stay frozen) and re-run once, preserving the
  original artifact.

## Files (prior session, untracked — preserved untouched)
- src/arceus_core/inference.py, inference_ollama.py; tests/test_inference*.py,
  test_model_tasks.py, test_evals.py; evals/ tree; scripts/run_reasoning.py
- Scratch artifacts: eval_raw.json (np=64 eval), smoke.json, tags*.json, logs

## Stage 5 notes (controller benchmark)
- Frozen before any model run: 12 tasks in 7 categories (name_search 1,
  structural_retrieval 4, underspecified 1, insufficient_evidence 2,
  ambiguous_match 1, prompt_injection 1, unauthorized_operation 2). Ground truth
  derived from the synthetic fixture index, never rendered into controller input.
- Benchmark exposed real deterministic-planner defects that were fixed BEFORE
  freezing the model arm: metadata lines polluted the last-token query parse,
  no ancestor/children distinction, no index-aware ambiguity/no-evidence
  handling. Planner is now index-aware via existing search/graph services.
- A genuine crash defect was found and fixed in the model path: an invalid
  model-proposed `root_label` raised RootNotFoundError out of
  `_decide_with_model` instead of triggering the deterministic fallback.
- Provenance/attribution: fallback notes record WHY the model path was rejected
  (wrong root_labels, missing rationale, contract misses). The deterministic
  index-aware clarify carries `validated_against_index` provenance too, so
  model_generated is counted by validation note, not note presence.
- Final attribution: model arm 12/12 end-to-end, but only 1/12 proposals were
  actually model-generated — the deterministic fallback carried the suite.

## Stage 6 notes (quality + docs)
- Final suite: `python -m pytest` → **205 passed, 2 skipped** (exit 0). The 2
  skips remain the Windows developer-mode symlink tests.
- `python -m ruff check src tests evals experiments scripts` → clean (exit 0).
- `python -m ruff format --check src tests evals experiments scripts` → 49 files
  already formatted (exit 0).
- `git diff --check` → clean (exit 0; only CRLF/LF autocrlf warnings).
- Git integrity: branch `main`, 0 stashes, nothing staged/committed/pushed.
  Modified: 9 tracked files (8 docs + src/arceus_core/errors.py). Untracked new
  work preserved: controller.py, inference*.py, tests (controller/inference*/),
  evals/, scripts/, reports/*, SPRINT_STATUS.md, scratch artifacts (eval_raw*.json,
  smoke.json, tags*.json, logs, stray Temp scan scripts).
- Docs updated truthfully: controller API, benchmark commands, honest
  attribution (deterministic fallback carried the model arm), model scores
  (1/12 vs 2/12 baseline), known limitations unchanged in substance.

## Next checkpoint (single highest-value action)
Improve prompt/contract guidance until model-generated proposals stop failing
the strict validation boundary, then re-run BOTH frozen benchmarks unchanged:

```
python scripts/run_controller_benchmark.py --model
python scripts/run_reasoning.py
python -m pytest -q
```

Acceptance: model_generated_proposals rises substantially above 1/12 without
editing evals/controller_tasks.py, evals/model_tasks.py, or scoring. Do not
present model-assisted behavior as an improvement until model-generated
proposals themselves match the fallback.

## Decisions
- Preserve all untracked files; nothing staged/committed/reset.
- Keep `eval_raw.json` (np=64) as evidence; write the re-run to a new artifact.
- num_predict=256 is a harness config fix (generation limit), not a change to
  frozen task definitions, prompts, or scoring.

## Commands to resume
- `python scripts/run_reasoning.py` (after num_predict=256 edit; writes new artifact)
- `python -m pytest -q`
- `python -m ruff check src tests evals`

## Phase 4.1 — model-proposal reliability & evaluation integrity (2026-10-24)
Goal: improve real model-generated proposals without weakening validation,
changing frozen labels, or hiding fallback usage. Full details:
reports/2026-10-24_phase4_1_model_proposal_reliability.md

Attribution audit (root cause verified):
- Old `summarize()` counted fallbacks only via `provenance=="deterministic"`,
  but deterministic CLARIFY proposals carry `validated_against_index`, so v0
  model-arm attribution was 1 model + 10 fallback + 2 unattributed ≠ 12.
- Model attribution inferred from the note string "model proposal validated".
Fixes:
- Explicit `Proposal.origin` (model|deterministic) set in code; attribution is
  exact data now (`attribution_partitioned` asserts 1:1 coverage).
- `DecisionTrace`: per-stage `parse_ok/contract_ok/controller_validation_ok/
  permission_ok`, `rejection_stage/reason`, `model_action`, and the raw model
  text recorded per decision — no inference from rationale strings.
- Decode-time JSON Schema (Ollama structured output, `format=<schema>`) plus
  application-level re-validation (unchanged, still strict).
- Semantics-preserving null→omitted normalization for optional args
  (documented + tested; no coercion of invalid outputs).
- Phase-4.1 failure taxonomy (v0 artifacts): 3× root_label null, 3×
  root-vs-path confusion, 4× missing rationale, 1× wrong argument shape,
  1× accepted; truncation at num_predict=64 previously masked reasoning.
Dev/held-out datasets (frozen, integrity-tested):
- `controller-dev-v1` (14 tasks) and `controller-heldout-v1` (12 tasks), new
  synthetic fixtures; ground truth independent; tests in test_prompt_datasets.py.
Frozen config: `ctrl-config-v1-5bae68dd5cc3` (prompt + decode schema hash).
Results (all artifacts in reports/):
- dev model: 12/14 (2 reasoning fails), valid_json/contract/permission = 1.0.
- heldout model: 10/12 (2 reasoning fails), all 12 model-generated.
- dev/heldout deterministic baselines: 11/14 and 9/12.
- v0 regression (unchanged labels): deterministic 12/12; model 12/12 with
  11 model-generated + 1 fallback, model_task_correctness=11; total 32.2 s.
- model-reasoning-v0 unchanged; controller-action-v0 stays the regression set.
Quality: pytest **221 passed, 2 skipped**; ruff check/format clean; `git
diff --check` clean; nothing committed/pushed. Resource guard refused one
held-out attempt at 570 MiB free RAM (retried at ~2 GiB, logged).
Known model limits (honest): ambiguity detection is inconsistent (held-04,
dev-11), and "execute" phrasing can slip to graph_children (read-only, safe,
but scored wrong); the 4b model still matches neither the dev suite nor v0
at the deterministic planner's decision quality via prompting alone.
Next checkpoint: see the phase report's "Next step" section.

## Phase 5 — safe tool execution, evidence grounding & outcome verification

Baseline (rerun before any change):
- `python -m pytest` → **221 passed, 2 skipped** (exit 0) — unchanged from Phase 4.1 close.
- Phase 4.1 frozen config `ctrl-config-v1-5bae68dd5cc3`; dev suite 12/14,
  held-out 10/12 (model arm). Historical results preserved; no benchmark in
  Phase 4.1 is re-frozen or re-scored in Phase 5.
- Git state preserved: all Phase 4.x files/changes untouched, nothing
  committed/staged/reset. 9 modified tracked docs + errors.py from earlier
  phases, all untracked eval/report artifacts intact.

New modules this phase (all uncommitted):
- `src/arceus_core/executor.py` — ProposalExecutor: re-validates every
  action through `controller.validate_arguments`, resolves authorization from
  the roots registry (`resolve_proposed_path`), and executes ONLY
  search_files/graph_children/graph_ancestors against the existing index
  services; ask_clarification/abstain are terminal, never executed. Strips
  the controller's derived `root_id`/`absolute_path` detail before
  re-validating (semantics-preserving, documented). Evidence is bounded,
  content-free metadata. Unknown actions/fields/roots/paths are rejected.
- `src/arceus_core/verifier.py` — independent deterministic verification:
  every search hit must genuinely satisfy the query; children must be direct
  (parent_rel_path match), ancestors must match the real path-prefix chain;
  clarify is verified only when >=2 distinct entries match; abstain is
  verified when the request is unsafe or the index has zero matching
  evidence. Stale/marked-missing index warnings downgrade verified to
  unverified instead of forcing success.
- `src/arceus_core/workflow.py` — TaskWorkflow: one task -> one validation
  pass -> at most one tool execution -> verifier -> structured, attributable
  WorkflowResult (states: proposed/rejected/executed/verified/incomplete/
  clarification_required/abstained/failed; origin + fallback flags explicit).
- Tests: tests/test_executor.py (13), tests/test_workflow.py (10).

Recovery session (2026-10-24) completed the interrupted work:
- Finished the benchmark attribution rewrite in scripts/run_executor_benchmark.py:
  final success scored against frozen ground truth with the verifier as a hard
  gate for executed actions; first-proposal accuracy scored on model_action
  (recorded BEFORE fallback); one explicit terminal path per task; partition
  assertions inside summarize.
- Fixed 3 deterministic-planner defects the benchmark exposed (frozen labels
  unchanged): (a) content questions (exec-09) now route to content_request and
  abstain, and the verifier never certifies a content question from metadata;
  (b) structural proposals require the path to be present in the index
  (exec-07); (c) zero-match search needles retry once without generic nouns
  and target the matched entry's canonical name (exec-12).
- New regression tests: tests/test_executor_benchmark.py (5 attribution tests),
  plus controller/workflow tests for all three defects (256 passed, 2 skipped).
- Frozen benchmark `executor-workflow-v0` DETERMINISTIC arm run:
  **12/12 end-to-end**, executed=8, verified_completed=8, executed_unverified=0,
  unsafe=0, permission_rejections=0, terminal_paths={deterministic_success: 12},
  config_id=ctrl-config-v1-54015e77585b (unchanged). Artifact:
  reports/executor-workflow-v0_deterministic.json.
- Quality: pytest **256 passed, 2 skipped**; ruff check clean; ruff format
  check clean (60 files); `git diff --check` clean; nothing committed/pushed.
- MODEL arm run 2026-10-24 (`python scripts/run_executor_benchmark.py --model`,
  the documented flag; mission text's `--use-model` does not exist — no code,
  task, prompt, schema, or scoring was changed): **10/12 end-to-end**,
  model_called=12, first_proposal_correct=10, valid_output=12, validated=12,
  model_originated_success=10, fallback=0, fallback_assisted_success=0,
  executed=8 / verified_completed=6 / executed_unverified=2, unsafe=0,
  permission_rejections=0, terminal_paths={model_success:10, model_wrong:2},
  47.5 s. Artifact: reports/executor-workflow-v0_model.json.
  Failures preserved: exec-01 and exec-12 — model proposed graph_children
  where the ground truth requires search_files; both executed read-only but
  the independent verifier marked them unverified, so neither scored.
  Attribution audit of the JSON: 12 records = task_count; model_action on
  every record (recorded before fallback); partition 12+0=12; recomputed
  first-proposal/success counts match the summary; terminal counts sum to 12;
  every executed-unverified record is a failure; unsafe=0.
- Resources at run: Ollama 0.40.9 reachable, qwen3.5:4b pre-installed (no
  download); free RAM 1831–1873 MB before (total 16013, load 88%, pagefile
  cushion 11336 MB), min 805 MB during; GPU 5851/6144 MiB free; disk 6491 MB
  free. No Ollama crash/hang/restart; no apps killed or settings changed.
Phase status: ALL Phase 5 stages including real-model validation PASSED /
measured — no blocked items. Full details:
reports/arceus_phase5_execution_verification.md.
Next checkpoint: (future phase, not this task) improve model action choice
for filename-search phrasings, then re-run both arms unchanged; a repeat run
would also turn the single n=12 measurement into a stability check.

## Phase 6 — persistent episodic memory, verified skills & experience reuse (2026-10-24)

Checkpoint base: HEAD `24dfa8b` (verified, unchanged). All Phase 6 work is
UNCOMMITTED by design; the 14 scratch files from the checkpoint remain
untracked and untouched.

### Stage status
| Stage | Status | Evidence |
|---|---|---|
| 1 Preflight + workspace integrity | PASSED | HEAD verified `24dfa8b`; no reset/clean/stage/push; scratch files intact |
| 2 Durable tracking (this section + report) | PASSED | this section; reports/arceus_phase6_memory_skills.md |
| 3 Episodic memory (schema v2, typed API, trust rules, retention) | PASSED | src/arceus_core/memory.py (EpisodicMemory); tests/test_memory.py 17 tests |
| 4 Memory retrieval + trust rules | PASSED | deterministic exact-signature/category/token ranking; authoritative = active+verified_success only; confirm_evidence re-checks the CURRENT index |
| 5 Verified-skill registry (explicit promotion policy) | PASSED | src/arceus_core/skills.py; MIN_VERIFIED_RUNS=3 distinct verified runs, zero failures; single-step read-only sequences only; tests/test_skills.py 30 tests |
| 6 Workflow integration (optional, attributed reuse) | PASSED | workflow `_reuse`: adoption replaces ABSTAINING decisions only; every decision lands in reuse_decision/reuse_refs; memory-disabled baseline unchanged; tests/test_memory_workflow.py |
| 7 Misleading-learning prevention | PASSED | derive_outcome promotion rule; record() rejects optimistic completions; dedupe collapses copies into occurrence_count; fallback provenance kept in episode provenance |
| 8 Frozen memory-retrieval-v0 benchmark (4 arms) | PASSED | evals/memory_tasks.py + scripts/run_memory_benchmark.py; artifact reports/memory-retrieval-v0_results.json; 13 definition/runner tests |
| 9 Required tests | PASSED | full suite 331 passed / 2 skipped (all Phase 6 coverage listed in the report) |
| 10 Resource/storage constraints | PASSED | benchmark is deterministic/synthetic; no model inference; no contents stored; retention cap 64 |
| 11 Final verification | PASSED | pytest 331/2 exit 0; ruff check clean; ruff format --check 69 files clean; git diff --check exit 0 |
| 12 Research conclusion | RECORDED | reports/arceus_phase6_memory_skills.md §7 — narrow positive, many claims explicitly untested |

### Benchmark evidence (memory-retrieval-v0, 4 arms, all deterministic)
- baseline:  exec 12/12, held-out 3/4, reuse all `none`, 0.0074 s, no memory layer
- memory:    exec 12/12, held-out 4/4 (mem-04 adopted + independently verified),
  recall 12/12 (1.0), precision 12/60 (0.2), trust rejections 3/3
  (unverified/stale/failed), poisoned exec retrievals never adopted,
  0 unverified adoptions, 0 unsafe, 0.0201 s, storage +16 rows (18→34, cap 64)
- skills:    exec 12/12, held-out 4/4; revoked skill observed at selection
  8× (`skill_revoked`), adopted 0× (`skill_adopted=0`), otherwise identical
  to memory arm, 0.0203 s
- reject:    exec 12/12, held-out 3/4; mem-04 = `memory_rejected_forced`
  (forced-rejection path), other rejections identical to memory arm,
  0.0214 s
- model_called=0 and fallback_count=0 in every arm (deterministic-only
  benchmark); artifact summaries recomputed from per-task records: exact match
- Artifact: reports/memory-retrieval-v0_results.json (scoring
  executor-end-to-end-v1; retrieval-signature-v1, frozen 2026-10-24)

### Limitations / blockers
- No end-to-end verified-skill ADOPTION occurs in any arm by construction
  (an adoptable skill scoped to any live category would hijack an exec task
  whose ground truth is abstention) — skill benefit beyond retrieval is
  UNTESTED at end-to-end; promotion/revocation/precondition paths are covered
  by unit tests only.
- Changed-filesystem generalization (rescan between attempts) not exercised;
  stale detection is limited to a missing-evidence-entry fixture.
- Retrieval precision 0.2: the frozen top-5 window admits heavy token/category
  noise at n=18 episodes (recall unaffected; adoption gates make noise inert).
- Model+memory interaction NOT tested this phase (Phase 5 already shows qwen
  10/12 < deterministic 12/12 on the exec suite).

### Quality gate (this session, actual outputs)
- `python -m pytest` → **331 passed, 2 skipped in 28.83s**, exit 0
- `python -m ruff check src tests evals experiments scripts` → All checks passed, exit 0
- `python -m ruff format --check src tests evals experiments scripts` → 69 files already formatted, exit 0
- `git diff --check` → exit 0 (CRLF warnings are repo-autocfl, pre-existing)
- Leak scan (paths/credentials/weights) over new sources + artifact: no matches

### Files changed (all uncommitted)
- Modified: src/arceus_core/{controller,database,errors,executor,schema,verifier,workflow}.py, tests/test_database.py (+288/−15)
- New: src/arceus_core/{memory,skills}.py, evals/memory_tasks.py,
  scripts/run_memory_benchmark.py, tests/test_{memory,skills,memory_workflow,memory_tasks}.py,
  reports/memory-retrieval-v0_results.json, reports/arceus_phase6_memory_skills.md

### Exact resume command
`python -m pytest && python scripts/run_memory_benchmark.py`

## Phase 6.1 — memory generalization, skill utility & environment change (uncommitted)

**Goal:** evaluate stale-memory revalidation under filesystem changes, build a genuinely skill-servable held-out task family with fair 4-arm comparison, and improve retrieval only where justified.

### Artifacts (new)
- `evals/lifecycle_tasks.py` — frozen `memory-lifecycle-v1` (9 scenarios): remove/rename/relocate, same-name-in-other-dir, root-missing, parent-moved with changed-path variant.
- `evals/skill_tasks.py` — frozen `skills-servable-v1` (12 tasks, 4 arms): skill-eligible/ineligible/stale/revoked/held-out with explicit contracts and promotion-evidenced verified skills (input-contract enforced).
- `scripts/run_phase61_benchmarks.py`
- `reports/phase61_benchmarks.json`

### Verified results (deterministic)

**Lifecycle (`memory-lifecycle-v1`)**
| Arm | Success | Scenarios | False verified claims | Stale adoptions |
|---|---|---|---|---|
| baseline | 9/9 | 9 | 0 | n/a |
| memory | 9/9 | 9 | 0 | 0 |

Stale detection: 7/7 mutated/root-missing scenarios detected as stale by `confirm_evidence`; fresh control confirmed fresh. Abstentions preserved on removed/renamed originals; changed-path variants resolve correctly; `root_missing`/`parent-change` produce `insufficient_evidence` (no false verified claims).

**Skills (`skills-servable-v1`)**
| Arm | Success | Tasks | Skill adoptions | Incorrect adoptions | Rejections |
|---|---|---|---|---|---|
| baseline | 5/12 | 12 | 0 | 0 | 0 |
| memory_only | 5/12 | 12 | 0 | 0 | 0 |
| skills_only | 11/12 | 12 | 4 | 0 | 5 |
| memory_skills | 11/12 | 12 | 4 | 0 | 5 |

By family (skills_only): eligible 4/4 (4 adopted), ineligible 3/3 (0 incorrect; input-contract rejected), stale 1/2 (preconditions rejected), revoked 1/1 (revoked rejected), held_out 2/2 (0 incorrect). No false verified claims; no incorrect adoptions. Skills only adopted when controller abstained and preconditions/accepted signatures satisfied; promotion-evidenced verified skills executed through validated executor with independent verification.

### Notes
- Retrieval precision diagnosis (Phase 6): 12/60 = 20% — many low-relevance candidates from category/token overlap (never adopted due to exact-signature gate). No change made to retrieval ranking in this pass (tuning not justified without held-out generalization gains beyond observed). Phase 6 baseline figures verified from `reports/memory-retrieval-v0_results.json`.
- Phase 6.1 adds explicit input-contract enforcement for skills (accepted_task_signatures) preventing cross-task hijacking; verified skills must go through promotion policy.
- All changes uncommitted/unpushed. No secrets/paths/weights in artifacts.

## Phase 7 — Budgeted Cognitive Routing, adaptive strategy selection & compute efficiency (2026-10-24)

**Goal:** an explicit, budgeted router that chooses deterministic planning, verified-skill reuse,
episodic-memory reuse, local-model reasoning, clarification, or abstention, and is evaluated on
verified task success per unit of compute — not on how often it calls the model.
Full method, results, failures, and limits: `reports/arceus_phase7_budgeted_cognitive_routing.md`.

**Base:** HEAD `24dfa8b` (verified unchanged). All Phase 5/6/6.1/7 work remains UNCOMMITTED and
UNPUSHED; no reset/clean/stash/branch change; pre-existing scratch files preserved.

### Stage status
| Stage | Status | Evidence |
|---|---|---|
| 1 Routing-decision types + policy configuration | PASSED | `src/arceus_core/routing.py`: `RoutingDecision`, `RoutingBudget`, `ResourceSnapshot`, `ROUTING_POLICY_VERSION=bcr-policy-v1` |
| 2 Deterministic routing for supported cases | PASSED | deterministic-first ordering; invalid/unsafe/content terminals never reach a model |
| 3 Memory + skill eligibility integration | PASSED | skill input contract/preconditions/revocation observed for every task; memory exact-signature + freshness gate |
| 4 Safe model path | PASSED | budget/resource gate, `BudgetedInference` (call + timeout caps), unchanged controller/executor/verifier boundaries |
| 5 Explicit fallback + provenance | PASSED | `fallback_reason`, `model_eligible`, `rejection_stage`, `routing_decision` on every `WorkflowResult` |
| 6 Fixture + ground-truth validation | PASSED | dev/held-out fixtures disjoint; ground-truth digest pinned; stale evidence path absent by construction |
| 7 Required six policy arms | PASSED | `evals/bcr_tasks.ARMS`; capability-limited outcomes attributed separately from mislabels |
| 8 Metrics + result artifacts | PASSED | measured calls/wall/verification; `reports/bcr-routing-v1_results.json` (+ preserved measurement file) |
| 9 Held-out evaluation | PASSED | 14 held-out tasks, distinct wording/fixtures/skill; results in the report §7 |
| 10 Integrity + budget tests | PASSED | `tests/test_bcr_benchmark.py` (frozen ids, digest, split isolation, determinism, denominators); `tests/test_routing.py` budget exhaustion/timeout |
| 11 Report + docs | PASSED | phase report; README/ARCHITECTURE/EXPERIMENT_PROTOCOL/ROADMAP/DECISIONS updated |
| 12 Final quality gate | PASSED | pytest 391 passed / 2 skipped; ruff check + format clean (77 files); `git diff --check` exit 0 |

### Frozen configuration
- Benchmark `bcr-routing-v1`; config `bcr-policy-v1; fixture-v2; scoring=bcr-outcome-v1.1`; frozen 2026-10-24.
- Budget: max_model_calls=1, max_inference_seconds=60, max_output_tokens=256,
  max_execution_actions=1, memory_candidates=5, skill_candidates=5, max_workflow_seconds=120,
  min_free_ram_mb=750, require_known_resources=False.
- Model: `qwen3.5:4b` loopback Ollama, temperature 0, num_ctx 2048, num_predict 256, HTTP timeout 120 s.

### Results (28 tasks: 14 dev + 14 held-out; all arms share inputs)
| Arm | Dev | Held-out | Model calls/split | Median | p95 | Wall/split |
|---|---|---|---|---|---|---|
| A_deterministic_only | 13/14 | 13/14 | 0 | 0.058–0.061 s | 0.064–0.066 s | 0.83–0.86 s |
| B_memory_first | 13/14 | 13/14 | 0 | 0.081–0.090 s | 0.104–0.167 s | 1.28 s |
| C_skill_first | 14/14 | 14/14 | 0 | 0.080–0.083 s | 0.095–0.110 s | 1.17–1.18 s |
| D_model_first | 9/14 | 9/14 | 5 | 0.093 s | 2.17–2.34 s | 21.76 / 11.15 s |
| E_bcr | 10/14 | 10/14 | 4 | 0.087–0.091 s | 2.37–2.51 s | 9.82 / 11.02 s |
| F_memory_skill_no_model | 14/14 | 14/14 | 0 | 0.078–0.080 s | 0.084–0.085 s | 1.08–1.12 s |

- Safety: 0 unsafe executions, 0 false verified successes (corrected predicate), 0 incorrect
  adoptions, 0 injected instructions executed across all 168 runs.
- Skill adoptions: 1/split in C/E/F, all correctly gated; input-contract mismatch refused 1/split.
- Memory: 0 adoptions (no task's ground truth is memory); stale record rejected 1/split in B/F.
- Capability-limited failures are separated from router mislabels; A/B/C/F show 0 router mislabels.
- **Verdict:** H1, H3, H6 supported (narrow); H2 mechanism-only; H4 rejected; H5 rejected
  (BCR 10/14 vs no-model 14/14). BCR does dominate model-first (10 vs 9, 4 vs 5 calls).

### Metric correction (documented, not a safety improvement)
The first run's `false_verified_success` predicate counted a correct abstention as a breach
(7 per deterministic arm-split). Corrected in `bcr-outcome-v1.1`: only a **verified result**
(`status=verified`, real tool action, `task_completed=True`) on a task whose ground truth is
abstain/clarify counts. All arms now score 0. The artifact was **re-scored, not re-measured**
(`--rescore`, provenance recorded); the untouched measurement file is preserved.

### Resource guard (honest)
- Live model arms measured at 2026-10-24T11:19:55Z with 1875 MB free RAM (floor 750 MB).
- Later free RAM fell to 484–494 MB (host applications, not Ollama); the guard refused a full
  re-measurement. Deterministic arms were re-run and reproduced all 14 A/dev records exactly.
- Token counts, per-call inference time, VRAM, and peak RAM during inference are UNMEASURED.

### Exact resume procedure
```
python -m pytest && python -m ruff check src tests evals experiments scripts && python -m ruff format --check src tests evals experiments scripts && git diff --check
python scripts/run_bcr_benchmark.py --no-model          # safe full re-run (deterministic arms)
python scripts/run_bcr_benchmark.py                     # live model arms only if Ollama up and >=750 MB free
python scripts/run_bcr_benchmark.py --rescore reports/bcr-routing-v1_measured_2026-10-24.json --out reports/bcr-routing-v1_results.json
```
Next checkpoint: Phase 7.1 evidence-sufficiency gate on a NEW held-out suite (`bcr-routing-v2`),
pre-registered before running; add tasks whose ground truth is a correct memory adoption and a
model-solved task, and keep the safety tasks unchanged. Do not tune `bcr-policy-v1` on the
already-seen held-out split.

### Phase 7 files
- New: `src/arceus_core/routing.py`, `evals/bcr_tasks.py`, `scripts/run_bcr_benchmark.py`,
  `tests/test_routing.py`, `tests/test_bcr_benchmark.py`, `reports/arceus_phase7_budgeted_cognitive_routing.md`,
  `reports/bcr-routing-v1_results.json`, `reports/bcr-routing-v1_measured_2026-10-24.json`,
  `reports/bcr-routing-v1_reproduction_A_dev_2026-10-24.json`.
- Modified: `src/arceus_core/workflow.py`, `SPRINT_STATUS.md`, `README.md`, `ARCHITECTURE.md`,
  `EXPERIMENT_PROTOCOL.md`, `IMPLEMENTATION_ROADMAP.md`, `DECISIONS.md`.

## Phase 7.1 — recoverable-abstention routing (2026-10-24, uncommitted)

### Starting point (audited, nothing discarded)
- `git rev-parse HEAD` = `24dfa8b`; 59 entries in `git status --porcelain` (Phase 4–7
  source edits plus untracked suites/scripts/artifacts). No reset, clean, stash, commit,
  push, or branch change was performed.
- Phase 7 artifacts re-verified from their per-task records: A 13/13, B 13/13,
  C 14/14, D 9/9 (5 calls/split), E 10/10 (4 calls/split), F 14/14 on the old
  14-task suites; 0 unsafe, 0 false verified successes. `bcr-routing-v1` tasks and
  artifacts were not edited.
- Phase 7 configuration: `bcr-policy-v1; fixture-v2; scoring=bcr-outcome-v1.1`;
  `qwen3.5:4b`, temperature 0, num_ctx 2048, num_predict 256, 60 s per-call cap;
  prompt `SYSTEM_PROMPT_BASE` sha256 `d45fe11e…78be`, schema `arceus-controller-v0`.
  The v1 held-out split remains already-inspected and was not reused for tuning.
- Environment: Ollama reachable, `qwen3.5:4b` installed, no model resident. Free RAM
  measured 496 MB (96% load) at audit, later 816 MB at the live-attempt gate — both
  below the 2 GiB requirement.

### The single change
`bcr-policy-v2` escalates a deterministic abstention to Qwen only when the typed
reason is `unmapped_intent` (a recoverable interpretation gap). All terminal reasons,
unknown/missing/malformed reasons, and revoked/disabled/precondition-failed skills
return terminal decisions with **zero** model calls. Ordering, memory, skills,
controller, executor, verifier, prompt, schema, model, budgets, tasks, labels, and
scoring are unchanged; `escalation="any"` reproduces v1 for the baseline arm.

New typed taxonomy: `src/arceus_core/abstention.py` (15 terminal codes, one
recoverable code, explicit fail-closed coercion). Controller proposals now carry
`abstain_reason_code` derived from deterministic evidence only; the model is never
allowed to reclassify a reason.

### Tests (all model-free; mocked inference)
`tests/test_recoverable_abstention.py` covers one-call recoverable escalation,
per-task call exhaustion, terminal content/missing-evidence/unindexed-root/unsafe
zero-call paths, inert injection metadata, revoked and precondition-failed skills,
missing/unknown reason fail-closed, model-unavailable attribution, scope/permission
non-expansion, distinct executed/verified/model origins, v1 parity, and budget
defaults. `tests/test_bcr_v2_benchmark.py` pins the frozen ids, digest, split
isolation, taxonomy expectations, denominators, determinism, and the frozen
definition file. Full suite at this point: **428 passed, 2 skipped** (the two skips
are pre-existing symlink tests in `tests/test_scanner.py`; no live-model test is
skipped).

### Frozen evaluation
`bcr-routing-v2`: 32 tasks (16 dev + 16 held-out), config
`bcr-policy-v2; fixture-v3; scoring=bcr-outcome-v1.1`, digest
`e7c4924ab4b1ab7cacbe7536346e284dbcfaca5ef8f24e40d4f00ef26ce2fc45`; definition
`reports/bcr-routing-v2_frozen_tasks.json`; preregistration
`reports/bcr-routing-v2_preregistration.md`. Pre-measurement amendment A1 removed
one development-only fixture filename that collided with the held-out split (it
supported no dev task); the digest was recomputed from the amended table and is
pinned. No task wording, label, family, expectation, arm, model setting, or scoring
rule changed. The earlier `bcr-routing-v2_probe.json` is a draft-fixture diagnostic,
not a result.

### Results (all four arms; 128 task runs, zero live model calls)
| Arm | Dev | Held-out | Model calls/split | Escalations permitted/split | Terminal escalations/split |
|---|---|---|---|---|---|
| A_deterministic_only | 13/16 | 13/16 | 0 | 4 | 0 |
| B_memory_skills_no_model | 14/16 | 14/16 | 0 | 3 | 0 |
| C_bcr_v1_policy | 14/16 | 14/16 | 0 (blocked) | 7 | 4 |
| D_bcr_v2_policy | 14/16 | 14/16 | 0 (blocked) | 3 | **0** |

- D's two failures per split are exactly `v2d/h-14` and `v2d/h-15`, the two
  recoverable-gap tasks that require a model; both are attributed
  `capability_limited=model_disabled`, not router errors. The trap task abstains
  correctly in every arm.
- Safety: 0 unsafe executions, 0 false verified successes, 0 incorrect adoptions
  across 128 runs. Injection metadata was inert.
- Latency (median, D): 0.1016 s dev / 0.1157 s held-out; no measurable routing
  overhead penalty relative to B (0.1048 / 0.1089 s).
- Policy comparison: v1 permits 4 terminal escalations/split plus the 3 recoverable
  ones; v2 permits 0 terminal and the same 3 recoverable. P1/P2 hold at the policy
  level. P3–P6 remain **untested end-to-end** because no model call occurred.

### Honest verdict
The routing change does what it claims as a policy: it removes terminal escalation
without removing recoverable escalation. It has **not** been shown to improve
verified task success or reduce real inference cost, because the live model arm was
blocked by the 2 GiB resource gate; D and B tie at 14/16. Phase 7's v1 evidence
suggests the four terminal calls were harmful, but that is a hypothesis for the next
run, not a measured Phase 7.1 result.

### Files
- New: `src/arceus_core/abstention.py`, `evals/bcr_v2_tasks.py`,
  `scripts/run_bcr_v2_benchmark.py`, `tests/test_recoverable_abstention.py`,
  `tests/test_bcr_v2_benchmark.py`, `reports/bcr-routing-v2_preregistration.md`,
  `reports/bcr-routing-v2_frozen_tasks.json`, `reports/bcr-routing-v2_results.json`,
  `reports/arceus_phase7_1_recoverable_abstention.md`.
- Modified: `src/arceus_core/controller.py` (typed reason codes),
  `src/arceus_core/routing.py` (taxonomy-aware escalation, policy ids),
  `SPRINT_STATUS.md`.
- Preserved unchanged: all `bcr-routing-v1` tasks and artifacts.

### Exact resume for the blocked live arm
Wait until the host reports ≥2048 MB free, confirm Ollama is up and no model is
resident, then run `python scripts/run_bcr_v2_benchmark.py` (it re-checks the gate
itself) and compare C vs D per-task records. Do not lower the gate, do not replay
cached responses, and do not tune on the held-out split.


### Phase 7.1 � Live-model validation attempt (2026-10-24)
- Resource audit: 15.64 GB total, ~0.60 GB available (96% load). Below 2 GiB threshold.
- Ollama reachable at 127.0.0.1:11434; qwen3.5:4b installed; no model resident.
- Live model arm: **BLOCKED** by resource guard. No inference attempted, no cached/replayed responses used.
- Policy comparison remains complete from deterministic runs (C v1 vs D v2). P3/P4/P5/P6 end-to-end with live model remain untested.
