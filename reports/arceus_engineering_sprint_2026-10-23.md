# ARCEUS Engineering Sprint Report — 2026-10-23

Sprint: Local Intelligence Foundation and Cognitive Controller v0
Canonical tracker: [SPRINT_STATUS.md](../SPRINT_STATUS.md)

## A. Sprint outcome

| Stage | Status | Evidence | Unresolved limitations |
|---|---|---|---|
| 0 Workspace audit + baseline | PASSED | Git status inspected (no resets/cleans); env measured (16 GB RAM, 3.36 GB free at audit, 7.8 GB disk free); baseline suite green | None for this stage |
| 1 Inference foundation validation | PASSED | `tests/test_inference*.py` green; loopback-only endpoint, payload conservatism, strict contracts, bounded timeouts, no unsupported Ollama options verified; found and fixed a real eval-harness defect (contract was never passed to `LocalInference`) | Cancellation is cooperative only; documented |
| 2 Real Qwen smoke via `LocalInference` | PASSED (prior session, preserved) | `smoke.json`: real `qwen3.5:4b` response through `LocalInference`, parsed `{"status":"ok","value":7}`, 1.12 s wall | GPU/RAM metrics unavailable from Ollama (`null`) |
| 3 Frozen `model-reasoning-v0` eval | PASSED WITH LIMITATIONS | np=64 run: 0/12 (all truncated mid-JSON; artifact preserved). Controlled np=256 re-run: **1/12** vs **2/12** deterministic baseline, 53.1 s | Safety-task choices (07, 08, 09, 12) failed contract *validation* → model's actual safety behavior UNKNOWN, not verified-safe |
| 4 Cognitive controller v0 | PASSED | `src/arceus_core/controller.py`: 5 read-only actions, strict typed contract, root-scoped path validation, deterministic fallback; 31 controller tests | v0 is a proposal engine only; no execution |
| 5 Frozen controller benchmark | PASSED | `evals/controller_tasks.py` (12 tasks, frozen before model run) + `scripts/run_controller_benchmark.py`; deterministic arm **12/12**, 0 unsafe, 2.1 ms total; model arm 12/12 end-to-end but **only 1/12 proposals model-generated** (9 fallbacks), 37.2 s total | 12 tasks is too small for statistical claims; model arm effectively measures the fallback |
| 6 Quality + docs review | PASSED | pytest **205 passed, 2 skipped**; ruff check + format clean (49 files); `git diff --check` clean; 6 docs updated truthfully | Linux/macOS never executed; Windows-only verification |

## B. Implementation

Created or modified this sprint (all reviewable in the working tree; nothing committed):

- [src/arceus_core/controller.py](../src/arceus_core/controller.py) — `CognitiveController(conn, inference=None).decide(task) -> Proposal`. Entry point: the `decide` method. Permitted actions: `search_files`, `graph_children`, `graph_ancestors`, `ask_clarification`, `abstain`. No execute method exists by design.
- [tests/test_controller.py](../tests/test_controller.py) — 31 tests: valid proposals per action, unknown actions/fields, malformed JSON, missing fields, invalid path scopes, oversized arguments, prompt injection in task text, model unavailable/timeout, fake-backend integration, deterministic fallback, read-only/proposal-only enforcement, evidence provenance, reproducibility.
- [evals/controller_tasks.py](../evals/controller_tasks.py) — frozen 12-task `controller-action-v0` suite; ground truth derived from the synthetic fixture index, never rendered into controller input.
- [scripts/run_controller_benchmark.py](../scripts/run_controller_benchmark.py) — runner; `--model` adds the Qwen arm over identical inputs.
- [src/arceus_core/errors.py](../src/arceus_core/errors.py) — `ControllerError`, `ControllerValidationError` added.
- [SPRINT_STATUS.md](../SPRINT_STATUS.md) and this report; doc updates to README.md, ARCHITECTURE.md, START_HERE.md, SECURITY_PRIVACY.md, DECISIONS.md, REPOSITORY_STRUCTURE.md.

How to run without inference: `CognitiveController(conn)` (or `inference=None`) uses the deterministic policy only; no Ollama process or network connection is started.

## C. Model validation

`qwen3.5:4b` was invoked through the real ARCEUS `LocalInference` interface + `OllamaBackend` at `127.0.0.1:11434`. The integrated smoke test (prior session, artifact preserved) returned a real parsed response `{"status":"ok","value":7,...}` satisfying the contract in 1.12 s wall (`smoke.json`). No external endpoint was contacted; no model weights were downloaded by ARCEUS. Per-request GPU/RAM metrics are `null` (Ollama does not expose them via this path). The combined eval artifact `reports/model-reasoning-v0_qwen3.5-4b.json` records the same smoke result at 14.48 s wall on its own re-invocation, plus baseline_success=2 and model_success=1.

## D. Evaluation

Frozen configuration (no task/prompt/scoring change after any score was observed):

- `model-reasoning-v0`: deterministic baseline **2/12**; model **1/12** (np=256, format=json, temp 0, think off), 53.1 s. Failure modes: 7/12 tasks returned `"tool": null` where the contract requires `""` → `InferenceOutputError`; 3/12 wrong argument shapes; 1/12 missing required reason fragment.
- `controller-action-v0` (`reports/controller-action-v0_deterministic.json`): **12/12**, valid_output_rate 1.0, unsafe proposals **0**, unnecessary abstentions 0, 2.1 ms total.
- `controller-action-v0` model arm (`reports/controller-action-v0_model.json`): **12/12 end-to-end**, unsafe proposals 0, but honest attribution: **1/12 model-generated, 9 deterministic fallbacks** (2 tasks handled by deterministic index-aware clarify; the remaining 2 counted under fallback), 37.2 s total. Task notes record why each model proposal was rejected: wrong `root_label`, missing rationale, contract misses. The deterministic arm carried the suite.

The benchmark's own value: it exposed and drove fixes for three real deterministic-planner defects (metadata-line query pollution, no ancestor/children distinction, no index-aware ambiguity/no-evidence handling) and one crash defect (invalid model `root_label` escaping to `RootNotFoundError` instead of triggering fallback). **With 12 tasks, no score here is a broad statistical claim.**

## E. Controller

- Supported actions (read-only only): `search_files`, `graph_children`, `graph_ancestors`, `ask_clarification`, `abstain`.
- Validation boundaries: strict typed contract `{action, arguments, rationale}`; unknown actions/fields rejected; path arguments must resolve inside registered roots (multi-root ambiguity requires `root_label`; the model's safety claims never grant authorization); first task line is the instruction, lines under `Untrusted metadata:` are inert data; oversized values rejected; re-validated in code after parsing.
- Read-only guarantees: proposal-only; there is no execute/run/run_action method; model output cannot execute shell commands, modify files, or reach the network.
- Model integration: through `LocalInference` with an `OutputContract`; any failure (unavailable, timeout, invalid JSON, contract miss, invalid root) falls back to the deterministic index-aware policy with a recorded reason.
- Test coverage: 31 controller tests (see B). Known limitation: safety-task *model* behavior is unverified because model proposals fail validation before the safety policy sees them.

## F. Quality

- `python -m pytest` → **205 passed, 2 skipped** (exit 0). Skips are the two Windows developer-mode symlink tests; junction skipping is verified end-to-end. No existing test was weakened or removed; one test asserting the old no-note fallback behavior was updated to match the intended new behavior (record why the model path failed).
- `python -m ruff check src tests evals experiments scripts` → clean (exit 0).
- `python -m ruff format --check src tests evals experiments scripts` → 49 files already formatted (exit 0).
- `git diff --check` → clean (exit 0; only CRLF/LF autocrlf warnings).
- Existing search/graph/inference benchmarks remain reproducible; new controller functionality is optional and never imported by deterministic paths.

## G. Resources

Measured at audit: 16,013 MB RAM with 3.36 GB free, ~7.8 GB disk free, RTX 3050 Laptop 6 GB, Ollama reachable on loopback with `qwen3.5:4b` already installed (never re-pulled). Deterministic benchmark total: 2.1 ms for 12 tasks. Model arm: 37.2 s for 12 tasks (sequential; no concurrent inference against the memory-constrained model). Per-request GPU/RAM figures unavailable (`null` from Ollama).

## H. Git integrity

- Branch: `main`. Stashes: 0. Nothing staged, committed, pushed, reset, or discarded.
- Modified tracked files: ARCHITECTURE.md, DECISIONS.md, EXPERIMENT_PROTOCOL.md, IMPLEMENTATION_ROADMAP.md, README.md, REPOSITORY_STRUCTURE.md, SECURITY_PRIVACY.md, START_HERE.md, src/arceus_core/errors.py.
- Untracked (preserved, not staged): `src/arceus_core/{controller,inference,inference_ollama}.py`, `tests/test_controller.py` + inference tests, `evals/`, `scripts/`, `reports/*` (new), `SPRINT_STATUS.md`, scratch artifacts (`eval_raw*.json`, `smoke.json`, `tags*.json`, logs), and two stray Temp scan scripts left untouched.
- No model weights or large binaries were added. No user files or personal data entered evaluation artifacts; all fixtures are synthetic temp directories.

## I. Research conclusions

Supported by evidence: (1) `qwen3.5:4b` on this machine does not reliably satisfy the strict JSON action contract (1/12 < 2/12 baseline) — a model-side failure at the contract boundary, not a harness artifact once np=256 was applied; (2) an index-aware deterministic controller can reach 12/12 on the frozen proposal suite with zero unsafe proposals and ~0.2 ms/task; (3) the current system is *effectively deterministic with optional model decoration* — 11/12 benchmark decisions came from the fallback. Contradicted: any claim that the local model improved decision quality this sprint. Untested: real safety-task model choices (blocked at contract validation), generalization beyond 12 synthetic tasks, model-generated proposals matching the fallback at any meaningful rate, and all memory/skill/routing hypotheses (future phases).

## J. Next checkpoint

Single highest-value next action: improve prompt/contract guidance until model-generated proposals stop failing the strict validation boundary, then re-run both frozen benchmarks **unchanged**.

```
python scripts/run_controller_benchmark.py --model
python scripts/run_reasoning.py
python -m pytest -q
```

Acceptance criterion: `model_generated_proposals` rises substantially above 1/12 without editing `evals/controller_tasks.py`, `evals/model_tasks.py`, or any scoring. Do not present model-assisted behavior as an improvement until model-generated proposals themselves match the fallback.
