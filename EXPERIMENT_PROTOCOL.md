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
