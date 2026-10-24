# Repository Structure

Actual layout after the first milestone, Phase 3 graph-retrieval evaluation, and the Phase 4 inference-boundary foundation. Files are created only when needed; there are no empty placeholders for future phases.

```text
arceus-core/
├── START_HERE.md
├── README.md
├── PRODUCT_VISION.md
├── ARCHITECTURE.md
├── RESEARCH_PLAN.md
├── LITERATURE_REVIEW.md
├── IMPLEMENTATION_ROADMAP.md
├── REPOSITORY_STRUCTURE.md
├── SECURITY_PRIVACY.md
├── EXPERIMENT_PROTOCOL.md
├── AGENT_RULES.md
├── AGENT_START_PROMPT.md
├── FIRST_SPRINT_CHECKLIST.md
├── CONTRIBUTING.md
├── DECISIONS.md
├── ARCEUS_START_HERE.md              # duplicate of START_HERE.md, preserved as provided
├── ARCEUS_IMPLEMENTATION_ROADMAP.md  # duplicate of IMPLEMENTATION_ROADMAP.md, preserved
├── prompts/
├── pyproject.toml
├── .gitignore
├── src/
│   └── arceus_core/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py
│       ├── config.py
│       ├── database.py
│       ├── schema.py
│       ├── paths.py
│       ├── models.py
│       ├── roots.py
│       ├── scanner.py
│       ├── reconcile.py
│       ├── search.py
│       ├── graph.py
│       ├── inference.py     # optional offline inference boundary and test backend
│       ├── inference_ollama.py  # loopback-only Ollama HTTP adapter (127.0.0.1:11434)
│       ├── controller.py    # read-only cognitive controller v0 (proposal engine)
│       ├── status.py
│       └── errors.py
├── tests/
│   ├── conftest.py
│   ├── test_paths.py
│   ├── test_database.py
│   ├── test_roots.py
│   ├── test_scanner.py
│   ├── test_reconcile.py
│   ├── test_search.py
│   ├── test_graph.py
│   ├── test_inference.py
│   ├── test_inference_ollama.py
│   ├── test_model_tasks.py
│   ├── test_controller.py
│   ├── test_cli.py
│   └── test_evals.py
├── evals/
│   ├── __init__.py
│   ├── fixture.py         # deterministic synthetic fixture and ground truth
│   ├── config.py          # frozen seed split and benchmark versions
│   ├── tasks.py           # versioned 35-task suite and ground-truth-free specs
│   ├── retrieval.py       # shared result type and retriever base
│   ├── index_view.py      # read-only scalar metadata view
│   ├── path_derived.py    # non-graph string-filtering structural baseline
│   ├── retrievers.py      # path-only, graph-assisted, and registry
│   ├── model_tasks.py     # frozen model-reasoning task definitions
│   ├── controller_tasks.py  # frozen 12-task controller benchmark
│   ├── metrics.py         # scoring and macro aggregates
│   ├── harness.py         # isolated multi-seed orchestration and artifact
│   └── run_benchmark.py   # python -m evals.run_benchmark
├── scripts/
│   ├── run_reasoning.py           # frozen model-reasoning-v0 runner (ollama arm)
│   └── run_controller_benchmark.py  # frozen controller-action-v0 runner
├── experiments/
│   └── bench_index.py
├── reports/
│   ├── 2026-10-23_filesystem_baseline.md
│   ├── baseline_filesystem_2000.json
│   ├── baseline_filesystem_20000.json
│   ├── graph_retrieval_evaluation.md
│   ├── graph_retrieval_evaluation_v1.json
│   ├── graph_retrieval_evaluation_v2.md
│   ├── graph_retrieval_evaluation_v2.json
│   ├── 2026-10-23_local_model_selection.md
│   ├── 2026-10-23_model_reasoning_tasks.md
│   ├── model-reasoning-v0_qwen3.5-4b.json
│   ├── controller-action-v0_deterministic.json
│   └── controller-action-v0_model.json
└── .venv/                 # local, ignored by Git
```

## Structure rules

- `src/` layout so tests exercise the installed package.
- `cli.py` validates and formats only; behavior lives in services.
- Tests use temporary directories and temporary SQLite databases exclusively.
- Evaluation tasks and expected outcomes are versioned; the graph benchmark uses only synthetic temporary fixtures and writes a JSON artifact plus a human-readable report.
- Runtime data lives in an OS-appropriate user-data directory or an explicit `--db` path, not in the repository.
- Schema changes use versioned migrations via `PRAGMA user_version`; shipped migrations are not edited in place.
- `.venv`, local databases, logs, model weights, environment files, and secrets stay out of Git. CSV/JSON measurement reports with synthetic data may be committed.
