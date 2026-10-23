# First Sprint Checklist — Environment + Filesystem MVP

Status as of 2026-10-23. Checked items were executed and verified on this machine; notes qualify what was or was not proven.

## Environment and repository

- [x] Confirm project path, OS, Git status, Python, and available disk space. (Windows 11 10.0.26300, no Git repo at audit, Python 3.14/3.13 available, 3.12 absent, 9.3 GB free.)
- [x] Record RAM/GPU/VRAM/driver if available; do not assume model compatibility. (16 GB RAM, RTX 3050 6 GB, driver 591.91; no model work attempted.)
- [x] Confirm root folder and any pre-existing files/repository before editing. (Starter documentation and `prompts/` preserved.)
- [x] Initialize Git only if needed; do not create a remote yet. (`git init -b main`; no remote, no commit unless requested.)
- [x] Add `.gitignore`, Python package scaffold, `pyproject.toml`, and test configuration.
- [x] Create `.venv` and verify install/tests. (Python 3.13; `pip install -e ".[dev]"`; 116 passed, 2 skipped.)

## Filesystem MVP

- [x] Root registration must be explicit. (`roots add` validates an existing directory; no implicit scan roots.)
- [x] Metadata scan of synthetic fixture tree. (Tests plus `experiments/bench_index.py`.)
- [x] SQLite schema/versioning and connection helper. (`PRAGMA user_version`, migration 1.)
- [x] Search by filename/path with parameterized SQL. (Ranked, case-insensitive, explained.)
- [x] Persistence after closing and reopening the database.
- [x] Nested folders and empty directories. (Empty dirs indexed; verified in tests.)
- [x] Rename/delete reconciliation and stale-entry behavior. (Soft `missing` status, history retained.)
- [x] Do not follow symlinks by default. (Windows junction skipping is verified end-to-end with a real junction; symlink-creation tests are skipped because this user cannot create symbolic links, and both link types go through the same detector.)
- [x] Permission errors and concurrently changing entries do not crash the entire scan. (Permission path simulated and tested; mid-scan mutation handled per-entry, not stress-tested.)
- [x] Partial scan errors cannot cause false mass deletion. (Coverage table; unobserved children retained; tested.)
- [x] File content is not copied into the database. (Test asserts a unique content marker is absent from database bytes and no content column exists.)

## Review / exit gate

- [x] `python -m pytest -q` result recorded. (116 passed, 2 skipped.)
- [x] `git diff --check` passes. (Working tree is new; diff check run as part of final review.)
- [x] `git status --short` reviewed; no DB, `.venv`, models, secrets, or private traces staged. (Not staged; `.gitignore` covers these.)
- [x] Scan time, query latency, index size, item count, and available memory recorded for a synthetic fixture. (See `reports/2026-10-23_filesystem_baseline.md`.)
- [x] Unsupported OS behavior is documented. (Windows-only verification; no Linux/macOS run.)
- [x] Research baseline and limitations are updated before moving to graph/model work. (`README.md`, this file, `DECISIONS.md`.)

## Deliberately not part of this sprint

- [ ] Local model adapter (requires hardware/model decision and explicit weight-download approval).
- [ ] Agent loop, memory, skills, BCR experiments.
- [ ] Desktop UI.
- [ ] CI across operating systems.
