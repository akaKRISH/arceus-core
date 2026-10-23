# Phase 7 Prompt — Benchmark and Budgeted Cognitive Routing

First read `RESEARCH_PLAN.md`, `LITERATURE_REVIEW.md`, and `EXPERIMENT_PROTOCOL.md`. Do not claim novelty before related work is reviewed.

Create a reproducible evaluator and compare: deterministic search; fixed retrieval; graph retrieval; graph + memory; graph + validated skills; fixed-policy agent; heuristic Budgeted Cognitive Routing (BCR). Hold model/prompt/task/hardware constant when testing component effects. Use the same task IDs for variants.

The BCR working objective is:
`utility(action) = expected_success_gain - λ·latency - μ·memory_cost - ν·storage_cost - ρ·risk`

Initially make routing decisions using transparent rules and log why a route was selected. Do not call it learned/adaptive unless it is actually calibrated or trained and compared to fixed policies.

Record task success, evidence precision/recall where applicable, median/P95 latency, memory/GPU memory, disk size, calls/tokens/tool steps, false success reports, recovery rate, and unsafe actions. Preserve negative results. Use synthetic tasks for development and public benchmark datasets only after verifying fit, versions, licenses, and evaluation instructions.
