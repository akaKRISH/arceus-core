# Contributing and Development Workflow

## Local setup

Use Python 3.12 and a project-local virtual environment. Once `pyproject.toml` exists:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pytest -q
```

Linux/macOS activation differs: `source .venv/bin/activate`.

## Change process

1. Define one behavior and its acceptance tests.
2. Use temporary directories/databases for filesystem tests.
3. Add/adjust documentation when behavior or risk changes.
4. Run focused tests and the full suite.
5. Run `git diff --check` and inspect `git status` and the complete diff.
6. Commit only after the owner reviews the change.

## Dependency policy

No runtime dependency without a concrete need, a compatibility check, and an explanation of maintenance/security implications. Avoid adding a graph database, vector database, model runtime, web framework, or UI framework before its milestone.

## Research integrity

Experiments must state their baseline, dataset/task version, model/runtime/configuration, hardware, metrics, and exclusions. Document negative results. Do not claim novelty or AGI because the project is named or designed around those goals.
