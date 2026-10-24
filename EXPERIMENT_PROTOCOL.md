# Experiment Protocol

## Purpose

Separate engineering success from research evidence. A demo that appears to work is not by itself proof of improved generalization, efficiency, or novelty.

## Before a run

For every experiment, record:

- experiment ID, date, git commit, OS and hardware;
- task-suite version and exact task IDs;
- model/runtime/version, model quantization, context length, prompt/config hashes where relevant;
- which components are enabled: path search, graph retrieval, memory, skills, router, verification;
- timeouts, step limits, permissions, seed, and exclusions;
- expected success criterion before inspecting results.

## Baselines and ablations

At minimum compare variants under the same task conditions:

1. deterministic path/name search only;
2. fixed naive retrieval policy;
3. model with tool access but no persistent memory/skill reuse (when the model phase exists);
4. graph-assisted retrieval;
5. graph + memory;
6. graph + memory + verified skills;
7. full stack with BCR;
8. fixed-policy version with equivalent budget.

Ablate one component at a time where practical. Ensure variants use the same model and prompt versions if the question is about architecture. A different model version invalidates a clean component comparison unless explicitly studied.

## Metrics

- Task success rate and per-task failure category.
- Evidence precision/recall or source coverage for retrieval tasks.
- End-to-end latency: median, P95, and timeout rate.
- Peak resident memory and GPU memory if measurable.
- CPU/GPU time, model calls/tokens where available, and tool calls.
- Database/index disk footprint and change over time.
- Recovery rate after injected errors.
- Unsafe-action / permission-boundary violation count. Target is zero observed violations, but zero in a small test set is not proof of absolute safety.
- False success reports and unsupported factual claims.

## Repeatability and reporting

- Version task fixtures and expected outcomes.
- Run deterministic tasks repeatedly; for stochastic model behavior, use repeated trials and report uncertainty.
- Report task count, excluded tasks, and all metric definitions.
- Preserve raw machine-readable results, but do not commit private file paths or private content. Use synthetic fixtures for public reports.
- Publish negative results and failed cases alongside successful examples.
- Keep a model-free regression suite that must pass when the LLM is unavailable.

## First baseline

For the first filesystem milestone, use synthetic temporary directories. Measure scan duration, file/directory counts, search latency, SQLite file size, update/reconciliation correctness, and failure behavior. Never benchmark against a user's personal drive by default.

## Phase 3.1 — frozen held-out retrieval protocol

The graph evaluation now uses a frozen three-system protocol. Its full results are in
[`reports/graph_retrieval_evaluation_v2.md`](reports/graph_retrieval_evaluation_v2.md).

### Frozen split and versions

- Benchmark: `graph-retrieval-v2`; fixture: `synthetic-fs-v2`; configuration:
  `graph-retrieval-v2-frozen-1`.
- Development seeds (5): `20261023`–`20261027`.
- Held-out seeds (10): `731001`–`731010`.
- 35 tasks per seed; 525 task instances across all seeds; 3 timed repetitions per
  task/system.
- The held-out seed list and retrieval logic are frozen before the held-out run. Any
  change to either defines a new configuration and requires a new artifact.

### Systems

1. `path_only`: production search with documented filters only.
2. `path_derived`: a non-graph control that reads scalar index metadata through an
   explicit read-only view and reconstructs parents, depth, descendants, and
   directory scope from normalized relative paths. It must not import or call the
   graph API, must not copy the production graph implementation, and must not
   receive expected answers.
3. `graph_assisted`: production search plus the production graph queries.

All systems receive the same `RetrievalSpec` (task id, category, kind, query text, and
parameters). Expected targets and evidence are never passed to a retriever. The
harness scores them separately.

### Procedure per seed

1. Generate a fresh synthetic fixture from the seed and verify it against disk.
2. Register the fixture as an isolated root and scan it.
3. Apply the planned deletions and scan again; verify present/missing counts.
4. Validate every expected target and evidence label against the generated
   filesystem and the independent fixture model.
5. Run all three systems on identical task specs.
6. Warm up once, then time the configured repetitions. Compare targets, evidence, and
   notes across repetitions and report any nondeterminism.
7. Aggregate by task, category, seed, and split; clean up the temporary workspace.

### Metrics and denominators

- Success: exact set equality, all task instances.
- Macro target P/R/F1: tasks with non-empty expected targets; no-result tasks excluded.
- Macro evidence P/R/F1: tasks with non-empty expected evidence.
- Top-1: tasks with exactly one expected target.
- No-result false-positive rate: tasks whose kind is `no_result`, not every task with
  an empty expected set.
- Latency: median/p95 over timed repetitions; exploratory and not a performance claim.
- Paired comparisons: per `(seed, task)` success changes, reported as descriptive
  counts. Do not present repeated timings as independent evidence.

### Reporting requirements

Report development and held-out splits separately, per-category results, per-seed
success, failures and unsupported tasks, the frozen configuration, and the exact
artifact. A 100% score on a finite synthetic suite is never proof of generalization.
Distinguish a graph-vs-path-only capability result from a graph-vs-path-derived
information result; they answer different questions.
