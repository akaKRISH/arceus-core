# Phase 3 Prompt — Graph-Aware Retrieval

Build on the tested metadata index. Do not add a graph database. Implement a small query interface over observed structural relationships: root membership, parent-child paths, directory ancestry, and explicit path lookup.

Tasks:
- Define relationship semantics and provenance in schema/docs.
- Add deterministic graph traversal queries with cycle/limit guards.
- Return evidence (paths and relationship chain), not just model-friendly summaries.
- Create a small versioned synthetic task set with exact expected paths/relationships.
- Compare graph-assisted retrieval to filename/path search-only on the same tasks.
- Report where graph retrieval helps, does not help, or regresses.

Do not invent semantic edges between files. Source imports/dependencies can be a separate later feature with language-specific parsers and explicit evidence. Do not add embeddings or a dedicated graph store unless results make the need clear.
