# Project ARCEUS

**Status:** Milestone 1 — metadata-only filesystem index, reconciliation, and search — is implemented and tested on Windows 11. Structural graph-aware retrieval (roadmap Phase 3) is implemented and has now been evaluated on 5 development plus 10 held-out synthetic seeds (525 task instances) against both ordinary path search and a strong non-graph path-derived baseline. Graph-assisted retrieval and path-derived structural filtering both scored 525/525; ordinary path-only search scored 338/525 (64.4%). The graph therefore provides a real structural-access capability over search, but **no measured information advantage over string reconstruction from the same indexed paths**. This is a narrow synthetic result, not a general intelligence or novelty claim. Phase 4 has added an optional, offline-only local inference boundary with a deterministic fake test backend and a frozen synthetic model-reasoning task suite; **no model backend has been installed, downloaded, or run by ARCEUS**. There is no embedding store or GUI.

ARCEUS investigates whether a local-first agent can improve task success per unit of compute by combining a compact local model with a structured representation of its environment, source-grounded retrieval, persistent memory, verified reusable skills, bounded planning, and outcome verification. None of that broader claim has been evaluated yet. This repository currently contains a deterministic, offline core plus a reproducible evaluation harness.

## What works today

- Register explicitly chosen directories; nothing outside a registered root is ever scanned.
- Scan file **metadata only** into SQLite: relative path, name, extension, type, size, modification time, first/last observation, scan coverage.
- Search filenames and root-relative paths with case-insensitive, Unicode-safe matching and a documented ranking.
- Incrementally rescan; additions, renames (as delete + add), modifications, and deletions reconcile without silently erasing index rows.
- Survive unreadable directories: partial scans are marked `partial`, and unobserved entries are retained rather than falsely deleted.
- Skip symbolic links and Windows junctions; never follow them.
- Query structural relationships: direct children, ancestor chains with observed-vs-inferred provenance, bounded subtree listings, per-root counts.
- Report health (`status`), errors, stale/partial scans, and interrupted runs.
- Emit JSON for machine consumption, including UTF-8 paths on Windows.
- Optional local inference boundary with typed requests/responses, model identity, offline enforcement, timeouts, cancellation, input/output limits, and structured-output contracts. Only a deterministic test backend is included; filesystem features do not depend on inference.
- **Cognitive Controller v0** (`arceus_core.controller`): a strictly read-only proposal engine. `CognitiveController(conn, inference=None).decide(task)` returns one validated `Proposal` (search_files, graph_children, graph_ancestors, ask_clarification, or abstain). There is no execute method; invalid, unsafe, or unavailable model output falls back to a deterministic index-aware policy. Reuses the existing search/graph/roots services; path proposals must resolve inside registered roots.
- **Bounded task workflow (Phase 5)** (`arceus_core.executor`/`verifier`/`workflow`): `TaskWorkflow.run(task)` takes one user task through proposal → re-validation → at most one allow-listed read-only tool execution → evidence collection → independent deterministic verification, returning an attributable `WorkflowResult` (origin, rejection stage, verification status, task completion). Content questions, unindexed paths, and unsafe requests terminate as explicit abstentions; model failures fall back with the stage recorded, never as model successes.

## Install

Python **3.11+** is required; the reference environment used 3.13 because 3.12 was not installed. The package uses only the standard library at runtime.

Windows PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest
```

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest
```

If PowerShell blocks activation, use `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` for that terminal only.

## Use

The database defaults to the per-user application-data directory:

| Platform | Default database |
|---|---|
| Windows | `%LOCALAPPDATA%\ARCEUS\arceus.sqlite3` |
| macOS | `~/Library/Application Support/ARCEUS/arceus.sqlite3` |
| Linux | `$XDG_DATA_HOME/arceus/arceus.sqlite3` or `~/.local/share/arceus/arceus.sqlite3` |

Override it with `--db PATH` (accepted before or after the subcommand) or `ARCEUS_DATA_DIR`. The database is never placed inside an indexed root by default, and database files are excluded even if a root contains them.

```powershell
# Register one explicit directory (must already exist)
arceus roots add "C:\Users\me\Projects\demo" --label demo

# Scan every registered root
arceus scan

# Or scan one root only
arceus scan --root 1

# Search by filename or relative path
arceus search "report"
arceus search "main.py" --ext py --limit 5
arceus search "src" --type dir --json

# Inspect a result's provenance and ancestry
arceus show "C:\Users\me\Projects\demo\src\main.py"

# Structural queries
arceus graph children "C:\Users\me\Projects\demo"
arceus graph ancestors "C:\Users\me\Projects\demo\src\main.py"
arceus graph tree "C:\Users\me\Projects\demo" --depth 2
arceus graph stats

# Health and maintenance
arceus status
arceus status --json
arceus roots list
arceus roots remove 1 --yes   # deletes index rows only; original files untouched
```

`arceus index scan` is an alias for `arceus scan`. All commands support `--help`. Global options are `--db PATH` and `--json`; use `--` before a literal argument that begins with `--`.

### Evaluate graph retrieval

The deterministic benchmark generates its own temporary fixture and index; it never indexes your files:

```powershell
python -m evals.run_benchmark
python -m evals.run_benchmark --suite development --repeats 1
python -m evals.run_benchmark --suite heldout
python -m evals.run_benchmark --help
```

The default command runs the complete frozen experiment: 5 development seeds and 10 held-out seeds, 35 tasks per seed, 3 repetitions. Results are written to `reports/graph_retrieval_evaluation_v2.json` and summarized on stdout.

### Evaluate the cognitive controller

A separate frozen 12-task controller benchmark (`controller-action-v0`) evaluates the deterministic policy and, optionally, a real local model against identical inputs. Ground truth never reaches the controller:

```powershell
python scripts/run_controller_benchmark.py                 # deterministic arm
python scripts/run_controller_benchmark.py --model         # adds the Qwen arm via LocalInference/Ollama
```

Artifacts: `reports/controller-action-v0_deterministic.json` (12/12, zero unsafe proposals) and `reports/controller-action-v0_model.json` (12/12 end-to-end, but only 1/12 proposals were model-generated; the deterministic fallback carried the rest). Task notes record why each model proposal was rejected.

### Evaluate the end-to-end task workflow

A separate frozen 12-task benchmark (`executor-workflow-v0`) scores the complete proposal → validation → execution → verification pipeline with explicit model/fallback attribution. The deterministic arm passes 12/12 (8 executed, all independently verified, zero unsafe actions); the real-model arm (qwen3.5:4b) scores 10/12 with all 12 proposals model-generated, zero fallback, and its 2 wrong actions caught by the independent verifier:

```powershell
python scripts/run_executor_benchmark.py                    # deterministic arm
python scripts/run_executor_benchmark.py --use-model       # adds the Qwen arm via LocalInference/Ollama
```

Artifacts: `reports/executor-workflow-v0_deterministic.json` (12/12) and `reports/executor-workflow-v0_model.json` (10/12, 47.5 s). A successful fallback never counts toward the model's score; full guarantees and limitations are in `reports/arceus_phase5_execution_verification.md`.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Command completed (for `search`, zero matches is still success) |
| 1 | Operational error: unknown root, invalid path, database problem, failed scan |
| 2 | Usage error |

## What is stored, and what is not

Stored in SQLite under `roots`, `scan_runs`, `scan_errors`, and `entries`:

- the registered root path and its case-normalized key;
- root-relative paths with forward slashes, names, extensions, file-vs-directory type;
- size and modification time, first/last scan run IDs, observation timestamps;
- scan status, coverage-relevant errors, counts, and skipped-symlink counts.

**Never stored:** file contents, content hashes, embeddings, previews, or copies of indexed files. Tests assert that a unique marker written into a fixture file does not appear in the database bytes.

An entry that disappears is marked `missing`, not deleted. This preserves history and makes stale results explainable; `search --include-missing` exposes them. Removing a root explicitly deletes only its index rows.

## Safety behavior

- Only explicitly registered roots are scanned; there is no default home-directory or drive scan.
- Directory symlinks and junctions are skipped and never traversed.
- A directory that could not be enumerated is never treated as empty. Its previously indexed children stay `present` because the scan has no evidence of removal.
- Descendants of an observed-removed directory are marked `missing` only when the removal itself was observed under a fully enumerated parent.
- Scans are batched in transactions; a crash or Ctrl+C finishes the run as `interrupted`, and the next scan reports it.
- `--max-entries` provides an explicit guard for unexpectedly large roots.
- SQL is parameterized; foreign keys, WAL journaling, and a busy timeout are enabled.
- Nothing is transmitted over the network.

## Architecture

```text
src/arceus_core/
├── cli.py          argparse CLI; formatting and dispatch only
├── config.py       data-directory and database-path resolution
├── database.py     SQLite connection, migrations, integrity checks
├── schema.py       versioned schema (PRAGMA user_version)
├── paths.py        platform-aware path/key normalization
├── models.py       typed records
├── roots.py        root registry and lookup
├── scanner.py      metadata-only traversal, batching, scan runs
├── reconcile.py    coverage-based conservative reconciliation
├── search.py       ranked name/path search with explanations
├── graph.py        children, ancestry, subtree, structural stats
├── inference.py    optional offline local-inference boundary and fake test backend
├── inference_ollama.py  loopback-only Ollama HTTP adapter (127.0.0.1:11434)
├── controller.py   read-only cognitive controller v0 (proposal engine)
├── status.py       health aggregation
└── errors.py       explicit exception hierarchy
```

```text
evals/
├── config.py            frozen seed split and benchmark versions
├── fixture.py           deterministic synthetic fixture and ground-truth model
├── tasks.py             versioned 35-task suite and ground-truth-free specs
├── retrieval.py         shared result type and retriever base
├── index_view.py        read-only scalar metadata view
├── path_derived.py      non-graph string-filtering structural baseline
├── retrievers.py        path-only, graph-assisted, and registry
├── metrics.py           task scoring, macro metrics, category summaries
├── harness.py           isolated multi-seed orchestration and JSON artifact
├── model_tasks.py       frozen synthetic model-reasoning task suite
├── controller_tasks.py  frozen 12-task controller benchmark (controller-action-v0)
└── run_benchmark.py     `python -m evals.run_benchmark` CLI
```

Tests live in `tests/` and use temporary directories and databases exclusively. `experiments/bench_index.py` builds a synthetic tree, scans it, and reports timings, database size, and Python-heap peak. The evaluation harness builds only synthetic temporary fixtures and cleans them up after each run.

## Verified results

Environment: Windows 11 Home (10.0.26300), Python 3.13.15, Git 2.56.0, 12 logical CPUs, 16,013 MB RAM, NVIDIA RTX 3050 6 GB (driver 591.91), 9.3 GB free disk. Python 3.12 was not installed; 3.13 was used and `requires-python` is `>=3.11`.

- Tests: **142 passed, 2 skipped** (`python -m pytest`). The two skips are symlink-creation tests that this Windows user cannot run without developer mode; Windows junction skipping is verified end-to-end with a real junction, and the link detector covers both link types.
- Lint: `ruff check src tests evals experiments` passes. Formatting: `ruff format --check src tests evals experiments` passes.
- Measured on synthetic fixtures (50 query repetitions each; full details in [reports/2026-10-23_filesystem_baseline.md](reports/2026-10-23_filesystem_baseline.md)):
  - 2,000 files / 40 dirs: first scan 0.126 s (~15,900 files/s), database 976 KiB, search median 0.5–1.3 ms.
  - 20,000 files / 200 dirs: first scan 1.746 s (~11,450 files/s), database 8.97 MiB (~466 bytes/entry), search median 12–23 ms.
  - Idempotent rescan and 10% deletion reconciliation produced the exact expected `missing` counts, with indices preserved.
- Graph evaluation (`graph-retrieval-v2`, 5 development + 10 held-out seeds, 525 task instances, 3 timed repetitions): **path-only 338/525 (64.4%), target F1 0.8215, evidence F1 0.5312; path-derived 525/525; graph-assisted 525/525, target/evidence F1 1.0; zero regressions.** On held-out seeds path-only scored 227/350 (64.9%) while both structural systems scored 350/350. The graph tied the path-derived baseline on every task, so the measured gain over search is structural access, not unique graph information. Full method, failure examples, and limits are in [reports/graph_retrieval_evaluation_v2.md](reports/graph_retrieval_evaluation_v2.md); raw results are in [reports/graph_retrieval_evaluation_v2.json](reports/graph_retrieval_evaluation_v2.json). The earlier single-seed experiment remains in [reports/graph_retrieval_evaluation.md](reports/graph_retrieval_evaluation.md).

These are single-machine measurements on fixed synthetic suites. The graph comparison gives structured relationship inputs to the graph system and natural query text to the path baseline; it demonstrates a capability difference on structural tasks, not improved search ranking or general navigation. The path-derived control used the same scalar metadata and reconstructed structure from strings, so the graph's lack of an advantage over it is a substantive negative result. Linux and macOS have not been executed.

## Known limitations

- **Windows-only verification.** The code uses `pathlib` abstraction and platform-specific keys, but no CI has run on Linux or macOS.
- **Metadata only.** Search cannot answer content questions; no text extraction or embeddings exist.
- **Renames are delete + add** unless the relative path is unchanged. File identity (inode/file ID) is deliberately not used as a cross-platform key.
- **Case semantics:** entry keys are case-folded on Windows and case-preserving on POSIX, chosen from the host at scan time. A database moved between platform families needs a rescan.
- **Substring search is a full scan.** There is no FTS index; measured 12–23 ms at 20k entries, but this grows linearly. FTS is deferred until a measured task needs it.
- **Symlinks/junctions are unconditionally skipped.** There is no opt-in to follow them.
- **Single-writer assumption.** WAL and a busy timeout are enabled, but concurrent scan/write behavior is untested.
- **Interrupted scans are reported, not resumed.** The next scan re-observes the root.
- **No capable model planner yet.** A loopback-only Ollama adapter exists (`inference_ollama.py`), and `qwen3.5:4b` was invoked through `LocalInference` (smoke test passed; frozen `model-reasoning-v0` scored 1/12 vs the 2/12 deterministic baseline, with all failures at the strict JSON-contract boundary). In the controller benchmark the model path was accepted for only 1 of 12 tasks; the deterministic fallback carried the rest. No model weights were downloaded by ARCEUS.
- **Graph evaluation is narrow.** 525 task instances from 15 synthetic seeds, one Windows machine, and a shared synthetic task grammar. It is not a real-world or population-level benchmark and does not establish generalization. The graph and path-derived systems are informationally equivalent on these tree-shaped fixtures; no inconsistent parent rows or real filesystem anomalies were tested.

## Research status

The long-term hypothesis (Budgeted Cognitive Routing over environment structure, memory, verified skills, and selective model reasoning) remains untested. The Phase 3.1 evaluation supports a narrow capability claim: structural access answers children, subtree, ancestor, and directory-scoped extension tasks that path-only search cannot express, and the gap persisted on 10 held-out seeds. It rejects the stronger claim that the graph API provides unique retrieval information: a non-graph path-derived baseline matched it on all 525 task instances. Phase 4 has prepared a typed offline inference boundary and a 12-task synthetic reasoning suite with a fixed deterministic baseline (2/12), but no real model has been evaluated. Nothing here supports claims about search ranking, real-world generalization, model-assisted behavior, memory, skills, or routing.

See `RESEARCH_PLAN.md`, `LITERATURE_REVIEW.md`, and `EXPERIMENT_PROTOCOL.md` for the designed experiments. See `DECISIONS.md` for architectural decisions and `reports/graph_retrieval_evaluation_v2.md` for the actual Phase 3.1 experiment.

## Next milestone

The controller v0 and both frozen evaluations now exist with real model measurements. The next step is improving prompt/contract guidance (or the model) until model-generated proposals stop failing the strict validation boundary, then re-measuring `model-reasoning-v0` and `controller-action-v0` without changing frozen task definitions. Keep the deterministic controller and `path_derived` as permanent controls, and do not present model-assisted behavior as an improvement until model-generated proposals themselves match the fallback.

## Documentation map

1. `START_HERE.md` — human setup guide and phase gates
2. `PRODUCT_VISION.md` — scope and users
3. `ARCHITECTURE.md` — layered design and intended boundaries
4. `IMPLEMENTATION_ROADMAP.md` — phase gates
5. `AGENT_RULES.md` — operating rules for coding agents
6. `FIRST_SPRINT_CHECKLIST.md` — verified milestone checklist
7. `reports/2026-10-23_filesystem_baseline.md` — index measurements
8. `reports/graph_retrieval_evaluation_v2.md` — Phase 3.1 held-out experiment and limits
9. `reports/graph_retrieval_evaluation.md` — earlier single-seed Phase 3 experiment
10. `reports/2026-10-23_local_model_selection.md` — local runtime/model candidate report
11. `reports/2026-10-23_model_reasoning_tasks.md` — frozen model-reasoning task design
12. `reports/2026-10-23_qwen_validation_report.md` — real-model validation through `LocalInference`
13. `REPOSITORY_STRUCTURE.md` — current layout
