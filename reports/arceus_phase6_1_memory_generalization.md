# Project ARCEUS — Phase 6.1: Memory Generalization, Skill Utility & Environment Change

**Status:** Complete (uncommitted).  
**HEAD:** `24dfa8b` (unchanged)  
**Date:** 2026-10-24  

## 1) Baseline audit (verified)
From `reports/memory-retrieval-v0_results.json` and prior Phase 6 report:
| Arm | Exec (12) | Held-out (4) | Memory recall | Memory retrieval precision | Trust rejections | Verified memory adoption |
|---|---|---|---|---|---|---|
| baseline | 12/12 | 3/4 | — | — | 0 | 0 |
| memory | 12/12 | 4/4 | 12/12 | 12/60 (20%) | 3/3 | 1 |
| skills | 12/12 | 4/4 | — | — | — | 0 skill adoptions |
| reject | 12/12 | 3/4 | — | — | — | 0 |

**Precision diagnosis:** Candidates include many category/token-overlap rows; exact-signature matching is required for authoritative reuse and the adoption gate only considers `exact_signature` matches. The low precision reflects broad candidate retrieval rather than misapplied adoptions (stale/unverified/failed episodes rejected; no false verified claims observed). No retrieval ranking changes made in this pass (no demonstrated held-out gain justifying tuning).

## 2) Scan–mutate–re-ask (memory-lifecycle-v1)
**Fixture:** synthetic temp dirs, fixed scenarios. Each run: index → task1 verified → mutate → rescan/reconcile → task2. Ground truth from fixture mutation.

**Results** (`reports/phase61_benchmarks.json`):
| Arm | Success | Scenarios | False verified claims | Stale adoptions |
|---|---|---|---|---|
| baseline | 9/9 | 9 | 0 | n/a |
| memory | 9/9 | 9 | 0 | 0 |

**Observations:**
- Stale detection: 7/7 mutated/root-missing scenarios flagged stale via `confirm_evidence`; fresh control confirmed fresh.
- Removal/rename of original evidence → abstention preserved; changed-path variants (moved file/new name/relocated dir) resolved correctly.
- `root_missing` and `parent-directory-moved` (original path) → `insufficient_evidence` / incomplete with no false verified claims.
- Memory rejected stale candidates at the gate; no stale adoptions observed.

## 3) Genuinely skill-servable held-out task family (skills-servable-v1)
**Design:** tasks chosen where controller abstains and skills can legitimately fill gaps (name_search tasks like "find X on the network drive"). Explicit contracts per skill: purpose, supported categories, `accepted_task_signatures` (input-contract), preconditions, single read-only action, verification procedure, known failures, provenance. Skills registered as candidates, promoted only after `MIN_VERIFIED_RUNS` distinct verified runs with zero failures.

**4-arm comparison** (identical tasks, deterministic):
| Arm | Success | Tasks | Skill adoptions | Incorrect adoptions | Rejections | False verified claims |
|---|---|---|---|---|---|---|
| baseline | 5/12 | 12 | 0 | 0 | 0 | 0 |
| memory_only | 5/12 | 12 | 0 | 0 | 0 | 0 |
| skills_only | 11/12 | 12 | 4 | 0 | 5 | 0 |
| memory_skills | 11/12 | 12 | 4 | 0 | 5 | 0 |

**By family (skills_only):** eligible 4/4 (4 adopted, all verified), ineligible 3/3 (0 incorrect; input-contract/preconditions rejected), stale 1/2 (preconditions rejected), revoked 1/1 (revoked rejected), held_out 2/2 (0 incorrect). Skills adopted only on abstaining controller decisions, executed through validated executor, and passed independent verification. Input-contract enforcement prevented cross-task hijacking.

## 4) Safety & correctness
- No false verified claims in lifecycle or skills benchmarks.
- No incorrect skill adoptions; stale/revoked skills rejected observably.
- Skills use only read-only allow-list actions, single-action sequences, promotion policy enforced, preconditions re-checked against current index.
- Memory write-back non-fatal; provenance/verification preserved.
- No secrets/model weights/user paths in artifacts.

## 5) Reproduction
```bash
python scripts/run_phase61_benchmarks.py
python -m pytest -q
python -m ruff check src tests evals experiments scripts
python -m ruff format --check src tests evals experiments scripts
git diff --check
```

## 6) Limitations & untested claims
- Retrieval ranking not tuned (precision 20%) — tuning deferred until held-out generalization justifies changes without harming recall/correctness.
- Skill benefits demonstrated on controller-abstain task families with clear contracts; generalization to other categories not fully evaluated.
- Model+memory interaction not exhaustively tested in this pass.
- No end-to-end skill benefit shown for tasks controller already handles directly (skills only replace abstentions).

## Final state
**Git:** `24dfa8b` on branch `main`, changes uncommitted/unpushed. New files: `evals/lifecycle_tasks.py`, `evals/skill_tasks.py`, `scripts/run_phase61_benchmarks.py`, `reports/phase61_benchmarks.json`. `SPRINT_STATUS.md` updated.
