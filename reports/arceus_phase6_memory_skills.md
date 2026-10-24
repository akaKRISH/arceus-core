# ARCEUS Phase 6 — Persistent Episodic Memory, Verified Skills & Experience Reuse

Date: 2026-10-24
Base checkpoint: `24dfa8b` (verified unchanged; all Phase 6 work left
**uncommitted and unpushed** by instruction)
Status: **COMPLETE** — implementation, tests, frozen 4-arm benchmark, and
quality gate all passed. Several research claims are explicitly recorded as
untested (§7).

## 1. What was implemented

### 1.1 Episodic memory (`src/arceus_core/memory.py`)
- Storage reuses the project SQLite database through an **additive, versioned
  migration** (schema.py migration 2; `SCHEMA_VERSION` bumped, tested for
  upgrade-from-v1 and reopen-persistence). Local-only: no cloud DB, no
  embedding service, no telemetry.
- `Episode` records carry explicit trust fields: episode id + timestamps,
  normalized task signature (first instruction line only — never raw prompts,
  metadata lines, file contents, credentials, or absolute paths), task
  category, action, **explicit origin** (model | deterministic | memory |
  skill), execution flag, verification status, task-completed flag, outcome
  (`verified_success | unverified | failure`), failure category, root-relative
  evidence refs `(root_id, rel_path)`, scrubbed provenance (including stored
  arguments), config/policy version, optional skill ref, lifecycle status
  (active/corrected/invalidated), occurrence_count for dedup.
- Typed API: `record`, `record_from_workflow_result`, `retrieve`, `get`,
  `mark_corrected`, `mark_invalidated`, `delete`, `apply_retention`,
  `confirm_evidence`, `stats`.
- Retrieval is **deterministic**: exact normalized-signature match (score 4),
  task-category match (2), token Jaccard overlap, newest-first tie-break,
  documented in the docstring. No vector embeddings.
- Trust rules are structural: only `status=active AND
  outcome=verified_success` is `authoritative`; unverified/failed episodes are
  returned only as warnings/failure examples. `derive_outcome` promotes to
  `verified_success` only when the task is established completed AND (not
  executed OR independently verified); `record()` raises if a caller passes an
  optimistic completed flag for an unverified executed task. Adoption
  additionally re-confirms every evidence ref against the **current** index
  (`confirm_evidence`), so stale episodes are rejected, never replayed.
- Dedup: sha256 over (signature, category, action, outcome, config_version);
  repeats collapse into one row with `occurrence_count` — copies cannot
  inflate evidence counts.
- Retention: bounded `retention_limit` (64 in the benchmark) with oldest-row
  eviction on every record.

### 1.2 Verified-skill registry (`src/arceus_core/skills.py`)
- Reviewable declarative records only — **no** model-generated or autonomous
  skill creation path. Registration re-validates every action and literal
  argument with the controller's `validate_arguments`; only read-only
  allow-list actions; exactly one step (the bounded workflow executes at most
  one tool action); terminal actions are rejected as skill steps; skills may
  only require the `read_only` permission.
- **Explicit promotion policy** (`skill-promotion-v1`, `MIN_VERIFIED_RUNS=3`):
  candidate → verified requires ≥3 **distinct** successful runs, each
  independently verified (`verified=1`), and **zero** recorded failures. Runs
  dedupe on `(skill_id, episode_id)` so replaying one episode cannot inflate
  the count. A model-written skill or a single successful action can never
  satisfy the policy.
- Selection re-checks declarative preconditions (`entry_present`,
  `root_active`, `category_is`) against the current index at recall time and
  returns an observable `SkillDecision` for every rejection reason
  (precondition-failed, revoked, disabled, not-verified, no-match). Revocation
  is terminal (candidate → only promotion; revoked → no transitions).

### 1.3 Workflow integration (`src/arceus_core/workflow.py`, optional)
- `TaskWorkflow(..., memory=..., skills=..., allow_memory_adoption=True)`;
  memory and skills are optional and independent — with both `None` the
  workflow is byte-for-byte the Phase 5 behavior (baseline arm proves it).
- `_reuse` safety rules (tested): reuse only ever **replaces an abstaining
  decision** (a controller proposal or clarify is never overridden); adopted
  actions still become controller-validated `Proposal`s flowing through the
  same executor permission checks and independent verifier; only
  authoritative, evidence-reconfirmed episodes are adoptable; any memory
  failure degrades to `memory_unavailable` and the run continues.
- Every reuse decision is observable and attributed on the result:
  `reuse_decision` ∈ {reuse_not_needed, memory_adopted,
  memory_rejected_stale|unverified|failed|forced, memory_no_match,
  memory_unavailable, skill_adopted, skill_precondition_failed, skill_revoked,
  skill_disabled, skill_not_verified} plus `reuse_refs` (retrieved candidates
  with matched/authoritative/warning, skill decision/reasons, stale reasons,
  episode id, task category). Write-back records each completed episode with
  honest provenance (origin, fallback_used, rejection_stage, arguments).

### 1.4 Frozen benchmark (`evals/memory_tasks.py`,
`scripts/run_memory_benchmark.py`)
- `memory-retrieval-v0`: reuses the Phase 5 execution fixture and its 12
  frozen `exec-*` ground truths (definitions untouched: `git diff` shows no
  changes to `evals/execution_tasks.py` or Phase 3–5 artifacts) plus **4
  held-out `mem-*` tasks** whose ground truth is independent of any episode:
  three must abstain (each paired with one seeded hazard exercising one trust
  rule: unverified, stale evidence, prior failure) and one is adoptable
  (controller abstains; a fresh verified episode with still-present evidence
  should be adopted, executed, and independently verified).
- 18 seeded episodes include two wrong-but-verified "poison" episodes whose
  signatures collide with exec-01/exec-02 — the falsifiable test of "trust
  memory blindly". The registry seeds one skill that earns full promotion
  evidence (3 distinct verified runs) and is then **revoked**.
- Four frozen arms, identical inputs and scoring, all deterministic:
  `baseline` (Phase 5 configuration), `memory`, `skills`, `reject`
  (`allow_memory_adoption=False` — the documented forced-rejection arm).
- Held-out prompts were probed against the deterministic controller before
  freezing (all abstain; all classify `name_search`), and that contract is
  itself pinned by a test so classifier drift cannot silently change the
  benchmark's semantics.

## 2. Commands and actual verification outputs

All run in the project venv on 2026-10-24:

```
python -m pytest
  → 331 passed, 2 skipped in 28.83s   (exit 0)
    (Phase 5 baseline was 256 passed / 2 skipped; +62 Phase 6 tests,
     +1 benchmark-definition/runner tests after the final reformat pass)

python -m ruff check src tests evals experiments scripts
  → All checks passed!   (exit 0)

python -m ruff format --check src tests evals experiments scripts
  → 69 files already formatted   (exit 0)

git diff --check
  → exit 0 (only pre-existing repo autocrlf LF→CRLF notices)

python scripts/run_memory_benchmark.py
  → artifact written: reports/memory-retrieval-v0_results.json (exit 0)

leak scan (C:\Users | /home/ | /Users/ | password | api_key | sk-) over the
new sources + benchmark artifact
  → no matches
```

Quality-gate repairs made this session (no test was weakened): removed two
unused imports, rewrapped three over-long lines, import-sort fix, one
formatting pass — all inside the three new Phase 6 files.

Phase 6 test coverage (new files): `tests/test_memory.py` (17: recording
validation, dedup/correction/invalidation/deletion, retention, persistence
across reopen, migration upgrade, provenance/verification promotion rules,
prompt-injection text inside task/metadata, path scrubbing),
`tests/test_skills.py` (30: registration validation, promotion policy
including negative paths, revocation transitions, preconditions against the
current index, allow-list enforcement), `tests/test_memory_workflow.py`
(adopt-only-on-abstain, evidence re-confirmation, memory-disabled,
memory-unavailable degradation, injection resistance, verifier agreement),
`tests/test_memory_tasks.py` (13: fixture invariants, frozen classifier
contract, scoring definitions, all four arms' frozen expectations, artifact
reproducibility), plus the `tests/test_database.py` schema-version update.

## 3. Four-arm benchmark results

Configuration: `memory-retrieval-v0`, scoring
`executor-end-to-end-v1; retrieval-signature-v1`, frozen 2026-10-24,
config `benchmark-protocol-v1; memory-retrieval-v0; fixture-v1`,
retention cap 64, 16 tasks/arm (12 exec + 4 held-out), no model, no network.
All summaries were **recomputed from the per-task records** and match exactly.

| Metric (denominator) | baseline | memory | skills | reject |
|---|---|---|---|---|
| Exec end-to-end verified success (12) | **12/12** | **12/12** | **12/12** | **12/12** |
| Held-out correct (4) | 3/4 | **4/4** | **4/4** | 3/4 |
| — mem-01 unverified poison | abstained ✓ | rejected ✓ | rejected ✓ | rejected ✓ |
| — mem-02 stale-evidence poison | abstained ✓ | rejected ✓ | rejected ✓ | rejected ✓ |
| — mem-03 failed prior | abstained ✓ | rejected ✓ | rejected ✓ | rejected ✓ |
| — mem-04 adoptable (fresh verified) | abstain ✗ (no memory) | **adopted + verified ✓** | **adopted + verified ✓** | refused (`memory_rejected_forced`) ✗ |
| First-proposal accuracy | n/a — `model_called=0`, `fallback=0` in every arm (deterministic-only benchmark; all 12 exec proposals deterministic-origin and correct) | same | same | same |
| Retrieval recall (12) | no memory layer | **12/12 (1.0)** | 12/12 (1.0) | 12/12 (1.0) |
| Retrieval precision (returned) | — | 12/60 (0.2) | 12/60 (0.2) | 12/60 (0.2) |
| Stale/unverified/failed rejections | — | 3/3 | 3/3 | 3/3 (+1 forced) |
| Poisoned exec retrievals adopted | — | 0/2 | 0/2 | 0/2 |
| Skill selection rejections observed | — | — | `skill_revoked` 8×, `skill_no_match` 8× | — |
| Skill adoptions / failed-skill executions | — | — | **0 / 0** | — |
| Unsafe actions (16) | **0** | **0** | **0** | **0** |
| Executed-but-unverified (8) | **0** | **0** | **0** | **0** |
| Fallback frequency | 0 | 0 | 0 | 0 |
| Latency per 16-task run | 0.0074 s | 0.0201 s | 0.0203 s | 0.0214 s |
| Storage growth (rows; cap 64) | n/a (`None` — no layer) | +16 (18→34) | +16 | +16 |
| Dedup collapses / occurrence total | — | 3 / 37 | 3 / 37 | 3 / 37 |

Safety invariants that held in **every** arm: 0 unsafe final actions,
0 executed-but-unverified results, 0 adopted-without-verification
(`adopted_but_unverified=[]`), terminal paths partition the 12 exec tasks
exactly, and the 4 terminal exec tasks (clarify/abstain) execute nothing by
design (8 executed / 8 verified-completed per arm).

Repeated vs generalization: the 12 exec tasks measure **repeated-task**
behavior (baseline is already at ceiling, so memory adds 0 there); mem-01..04
are **held-out** and measure generalization of the trust rules to unseen
tasks (5/5 hazard exposures rejected, 1/1 legitimate adoption). Exact cache
hits are not counted as reasoning: recall is reported separately from
end-to-end success, and the poisoned same-signature episodes would score as
"cache hits" for a naive system — this system rejected them.

## 4. Reproducibility

```
python -m pytest                                  # 331 passed, 2 skipped
python scripts/run_memory_benchmark.py            # rewrites the artifact deterministically
```

The benchmark uses only synthetic fixtures in a fresh temp directory per arm,
no model inference, no network, no clock- or hash-dependent scoring (the only
time values recorded are wall-clock latencies, which are reported but never
scored). Re-running produces identical success/decision counts. Ground truth
lives in `evals/execution_tasks.py` (tracked, unchanged) and the held-out
expectations in `evals/memory_tasks.py` (frozen; the classifier contract is
test-pinned).

## 5. Resource and storage measurements

- No model was loaded or queried in this phase (per instruction); Ollama was
  not used; nothing was downloaded.
- Benchmark wall time: ~0.007–0.021 s per 16-task arm (memory consultation
  costs ≈ +13 ms per full run, ≈ +0.8 ms/task over baseline).
- Storage: +16 episode rows per memory-enabled arm run (18 seeded → 34),
  retention cap 64 rows; 37 total occurrences with 3 dedup collapses.
  Rows are bounded normalized fields only; evidence is root-relative pairs;
  provenance strings are path-scrubbed and size-capped (2 KB JSON cap).

## 6. Failures and limitations (honest record)

1. **No end-to-end skill adoption is possible in this frozen benchmark by
   construction.** Probing showed every abstaining exec task covers one of
   the three live categories (`name_search`, `structural`,
   `content_request`), so any adoptable skill scoped to a live category would
   hijack an exec task whose ground truth is abstention. The registry
   therefore seeds a skill that earns full promotion evidence and is then
   revoked; `skill_adopted=0` in all arms. **Skill benefit beyond memory
   retrieval is UNTESTED end-to-end** (promotion, revocation, and
   precondition-rejection paths are unit-tested only). This is a finding about
   the benchmark's design space, not a pass.
2. **Retrieval precision is 0.2** (12 relevant of 60 returned): at n=18 the
   frozen top-5 window admits token/category noise on nearly every query.
   Recall is unaffected (the exact episode always ranks first) and adoption
   gates make the noise inert, but a larger store will need ranking work
   before precision claims are meaningful.
3. **Changed-filesystem generalization not exercised.** Stale detection is
   covered by a missing-evidence-entry fixture (phantom path), not by a
   scan → delete → re-ask cycle. Persistence across "restart" is covered at
   the SQLite level; path-rename episodes are untested end-to-end.
4. **Model+memory interaction untested** this phase (deterministic-only by
   instruction). Prior evidence: the Phase 5 qwen arm scored 10/12 vs
   deterministic 12/12, so no model advantage has been demonstrated on the
   exec suite.
5. Two identical-artifact issues fixed rather than shipped: baseline storage
   growth was first recorded as a misleading `-18` (empty stats minus seed
   count) and is now `null` ("no memory layer"); the benchmark was rerun and
   the corrected artifact regenerated.
6. Pre-publication (deferred as instructed, unchanged): historical Phase 3–5
   artifacts still contain absolute local filesystem paths; sanitizing them
   is a separately documented future task — no historical artifact was
   rewritten in this phase.

## 7. Research conclusion (evidence-based)

1. **Does episodic memory improve repeated-task performance?**
   Not on already-solved tasks: all arms are 12/12 on the repeated exec suite
   and memory is never load-bearing there (9× `reuse_not_needed`, 3×
   `memory_no_match`). The measurable gain is on **abstention-gap** tasks the
   deterministic policy cannot serve: held-out 3/4 → 4/4 (+1/16 overall),
   adopted episode independently verified.
2. **Does the benefit survive changed paths / changed filesystem state?**
   Partly tested: an episode whose evidence entry no longer exists is rejected
   (`memory_rejected_stale`, 1/1) instead of replayed — current index state
   overrides memory. A full rescan-between-attempts cycle was **not**
   exercised; that claim remains untested.
3. **Do verified skills improve performance beyond memory retrieval alone?**
   **Not established.** skills arm == memory arm (4/4) because no eligible
   verified skill exists in the frozen fixture (`skill_adopted=0`). Record
   as untested, not as "no effect".
4. **How often does ARCEUS reject stale or unsafe reuse?**
   6/6 seeded hazard exposures rejected or never fired: 3/3 trust-gate
   rejections (unverified/stale/failed), 1/1 forced-policy rejection, 0/2
   poisoned same-signature retrievals adopted, 0 executions of the revoked
   skill despite 8 selection-time observations. Denominators are small by
   design (frozen fixtures); unit tests cover the remaining paths.
5. **Is the improvement larger than the storage and latency overhead?**
   For the one task memory saves: yes in absolute terms (+12.7 ms and +16
   small rows per run). For the 12 already-solved tasks: no — overhead with
   zero benefit. Net: a cheap, bounded capability with a narrow positive
   envelope; not a general accuracy gain.
6. **Does model assistance add value beyond the deterministic controller?**
   Not in this phase (not run), and prior frozen evidence shows the opposite
   on the exec suite (qwen 10/12 vs deterministic 12/12; controller-benchmark
   model arm reached 12/12 end-to-end with only 1/12 model-originated
   proposals). No demonstrated value beyond deterministic on these benchmarks.
7. **Which claims remain untested?**
   Changed-filesystem (rescan) generalization; end-to-end verified-skill
   adoption benefit; model+memory interaction; retention pressure near the
   cap; memory under task streams long enough to grow `occurrence_count`
   substantially; multi-root ambiguity with memory; any "general
   intelligence" improvement — **none of which is claimed**.

## 8. Verified vs merely proposed

**Verified (executed and observed):** schema migration & persistence; typed
memory API; dedup/correction/invalidation/deletion/retention; promotion
policy enforcement (positive and negative paths); precondition re-checking;
revocation finality; adopt-only-on-abstain; evidence re-confirmation gating;
memory-optional operation; memory-unavailable degradation; injection text
carried as data; all four arms' frozen outcomes; artifact reproducibility;
331-test suite; clean lint/format/diff gate; no secret/path leakage.

**Proposed / untested (explicitly not claimed):** skill-adoption end-to-end
benefit; rescan-cycle staleness; model+memory interaction; behavior at
retention capacity; scalability of retrieval ranking beyond tens of episodes.

## 9. Resume procedure

```
cd arceus-core
python -m pytest                                  # expect 331 passed, 2 skipped
python scripts/run_memory_benchmark.py            # regenerates reports/memory-retrieval-v0_results.json
git status --short --branch                       # Phase 6 changes remain uncommitted by design
```

Highest-value next step: design a **skill-servable held-out task family**
(category-scoped so adoption cannot hijack exec ground truths) plus a
scan → mutate → re-ask cycle, then extend `memory-retrieval-v0` (as
`memory-retrieval-v1`, keeping v0 frozen) to answer the two claims that
remain untested: end-to-end skill benefit and changed-filesystem
generalization. Nothing in this phase requires an urgent fix.
