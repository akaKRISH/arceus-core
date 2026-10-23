# Phase 1 Prompt — Project Foundation

Read the project documents and the environment audit. Implement only the project scaffold—do not implement filesystem scanning or LLM logic in this phase.

Requirements:
- Inspect Git status/files first; preserve all existing work.
- Create `pyproject.toml` for Python 3.12 with a `src/arceus_core` package and `pytest` development dependency. Keep runtime dependencies to the standard library if practical.
- Add a small CLI command that prints the installed package version/help and exits cleanly.
- Add `.gitignore` rules for `.venv`, Python caches, databases, model weights/checkpoints, local config/secrets, and private logs/traces.
- Define a user data directory/config pattern without scanning it automatically.
- Add minimal tests for package import and CLI health/version behavior.
- Do not add FastAPI, Tauri, React, embeddings, a graph database, a local model runtime, or a GitHub remote.

Before coding, report the plan and exact files expected to change. After coding, run installation/tests using the available Python 3.12 environment and report exact command/results. Do not commit unless explicitly asked.
