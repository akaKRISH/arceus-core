# Product Vision and Scope

## Vision

Build a local cognitive agent that understands a useful, permission-scoped model of the computer environment, retrieves relevant evidence, remembers validated experience, uses tools within explicit limits, and checks whether its actions worked. The research question is whether these system components help a resource-constrained local model solve more tasks reliably without unacceptable latency, storage, or risk.

## Users and first use case

The first user is the developer running ARCEUS on their own machine. The initial use case is simple: “Find the original location of a file or project quickly and accurately without copying all files into an AI database.”

## User value

- Locate files and folders by path/name.
- Show why an item was retrieved and where it actually lives.
- Incrementally keep the index current.
- Later answer scoped questions across user-approved files, with sources.
- Later perform bounded actions and verify results.

## Non-goals for the first milestone

- Training a foundation model from scratch.
- AGI/ASI claims.
- Reading or uploading all user data.
- Copying full file contents to a new corpus.
- Autonomous shell or GUI actions.
- Multi-agent orchestration, continuous background surveillance, or automatic system startup.
- A polished desktop UI before the core behavior is correct.

## Product principles

1. **Source-grounded:** important answers link to files, paths, timestamps, or tool results.
2. **Minimal duplication:** index metadata first; content stays at its original path unless the user explicitly enables a later content-ingestion feature.
3. **Permission first:** every scope and every write-capable tool has an explicit policy.
4. **Predictable failure:** report missing permission, stale results, or uncertainty instead of inventing answers.
5. **Useful on modest hardware:** measure memory, storage, and latency; choose model sizes after inspecting actual hardware.
6. **Research transparency:** publish configurations, tasks, failures, and baselines.

## Initial acceptance target

A user can select one small test directory, run an index command, search a filename, receive the exact path of the original file, and rerun scanning after rename/delete without the database silently lying about stale data. Tests demonstrate that file contents were not copied into the index.

## Naming note

ARCEUS is a working codename inspired by a fictional Pokémon character; the project is unofficial and not affiliated with the rights holders. Review trademark/name considerations before using the name commercially.
