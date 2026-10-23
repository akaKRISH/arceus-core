# Repository Structure

Actual layout after the first milestone. Files are created only when needed; there are no empty placeholders for future phases.

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
│   └── test_cli.py
├── experiments/
│   └── bench_index.py
├── reports/
│   ├── 2026-10-23_filesystem_baseline.md
│   ├── baseline_filesystem_2000.json
│   └── baseline_filesystem_20000.json
├── evals/                 # not created yet: versioned task definitions (Phase 3 evaluation)
└── .venv/                 # local, ignored by Git
```

## Structure rules

- `src/` layout so tests exercise the installed package.
- `cli.py` validates and formats only; behavior lives in services.
- Tests use temporary directories and temporary SQLite databases exclusively.
- Runtime data lives in an OS-appropriate user-data directory or an explicit `--db` path, not in the repository.
- Schema changes use versioned migrations via `PRAGMA user_version`; shipped migrations are not edited in place.
- `.venv`, local databases, logs, model weights, environment files, and secrets stay out of Git. CSV/JSON measurement reports with synthetic data may be committed.
