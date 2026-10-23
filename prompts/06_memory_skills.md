# Phase 6 Prompt — Provenance-Aware Memory and Verified Skills

Add memory only for an evaluated repeated-task use case. Each memory entry needs provenance/source, timestamps, confidence/status, scope, and update/invalidation rules. Separate observations, inferred facts, and user-approved preferences. Do not turn all file contents into permanent memory.

A reusable skill must define: ID/version, purpose, preconditions, required permissions, parameter schema, steps, expected effects, postconditions, allowed tools, known failure modes, validation status, and invalidation conditions.

Build repeated-task fixtures and a no-memory/no-skill baseline. Measure success, retrieval mistakes, stale memory errors, latency, storage, and safety violations. Do not assume any saved fact is true forever or any prior successful skill is safe in a new context.
