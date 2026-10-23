# Phase 2 Prompt — Metadata-Only Filesystem Index

Implement the first real feature only: explicitly approved roots, metadata indexing to SQLite, and filename/path search. Read `ARCHITECTURE.md`, `SECURITY_PRIVACY.md`, and `FIRST_SPRINT_CHECKLIST.md` first.

Requirements:
- No content copies or content embeddings. Never silently scan the home directory or an entire drive.
- Use temporary directories/databases in tests; do not index personal folders during development.
- Store root-relative paths and enough metadata to reconstruct the original path. Document case sensitivity/platform behavior.
- Track scan runs and scan coverage/errors. A partially failed scan must not cause unobserved entries to be falsely deleted.
- Do not traverse symlinks by default.
- Handle permission errors, deleted/changing entries, Unicode/spaces/odd characters, empty directories, and reopening persistent SQLite DB.
- Use parameterized SQL and transactions. Keep migrations/schema versioning straightforward.
- Implement rename/delete reconciliation safely. If rename cannot be detected reliably, document that limitation instead of guessing.
- Add CLI commands such as `index <approved-root>`, `search <query>`, and `stats`; validate paths and avoid arbitrary command execution.
- No LLM, vector database, GUI, file watcher, cloud API, shell tool, or agent loop.

Before coding, produce a bounded plan and tests. After coding, run tests, inspect the diff, record fixture item counts/scan duration/search latency/database size, and state what is not yet cross-platform verified.
