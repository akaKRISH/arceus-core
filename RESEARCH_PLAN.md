# Research Plan

## Primary question

Under a fixed local compute/resource budget, does combining a compact model with (1) a live environment graph, (2) provenance-aware memory, (3) reusable verified skills, and (4) outcome-based routing improve task success and generalization compared with simpler baselines?

This is a hypothesis. Existing work already covers agent skill libraries, memory, graphs, and computer-use benchmarks; the combination is not automatically novel. Read `LITERATURE_REVIEW.md` before making a novelty claim.

## Candidate mechanism: Budgeted Cognitive Routing (BCR)

Working name only; novelty is unverified. For each task, choose among:

1. deterministic lookup/calculation;
2. metadata or graph retrieval;
3. reuse of a validated skill;
4. local model reasoning/planning;
5. additional evidence gathering;
6. abstention or request for approval.

A conceptual objective is:

`utility(action) = expected_success_gain - λ·latency - μ·memory_cost - ν·storage_cost - ρ·risk`

Start with transparent heuristics. Do not claim adaptive/learned routing until routing parameters are trained or calibrated from a sufficient trace set and compared to fixed policies.

## Hypotheses

- **H1 — Graph retrieval:** an environment graph improves evidence coverage or success on multi-file tasks over filename/path search alone.
- **H2 — Persistent memory:** source-backed memory improves repeated-task success without increasing stale-fact errors.
- **H3 — Skill reuse:** validated skills reduce cost on repeated/related tasks without unsafe transfer or regression.
- **H4 — Routing:** BCR improves task success at a fixed latency/compute budget over fixed routing policies.
- **H5 — Verification:** explicit postcondition checks reduce false success reports and recoverable failures.

Each hypothesis must specify a task set, baseline, expected metric, and falsification condition before experiments run.

## Minimum viable scientific contribution

1. A precisely scoped problem: local computer/file-navigation tasks under resource constraints.
2. A reproducible task suite and evaluation harness.
3. Baselines that isolate graph, memory, skills, routing, and verification effects.
4. Results including negative results, failures, latency/RAM/storage, and safety violations.
5. A careful related-work analysis describing what is and is not new.

## Falsification mindset

If a component adds complexity without a statistically or practically useful improvement, simplify or remove it. An agent system is not a research contribution merely because it combines familiar libraries or uses a large model.

## Early task suite

Start with 50 controlled, deterministic cases and grow toward 100+ once tests are stable:

- exact/partial filename lookup;
- folder and nested-project discovery;
- rename, deletion, and stale-entry detection;
- permission-denied/partially scanned folders;
- references/configuration/dependency lookup where ground truth is verifiable;
- repeated workflows for memory/skills;
- changed-but-related tasks for transfer;
- timeout, cancellation, and approval-required scenarios.

Synthetic tasks are useful for engineering but insufficient as the only research evaluation. Add external benchmarks only when their environment, licenses, setup, and task fit are understood.
