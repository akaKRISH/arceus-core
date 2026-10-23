# Phase 5 Prompt — Bounded Read-Only Agent Loop

Implement one minimal agent loop over approved, read-only tools only. Follow the explicit state machine in `ARCHITECTURE.md`.

Required: a proposed plan, schema validation, tool allowlist, root/path boundary enforcement, maximum tool-call/step/time budgets, cancellation, structured trace, exception recovery, and a postcondition verifier. Model output must never create a new permission or tool. Do not execute arbitrary generated code or shell commands.

Add adversarial tests where filenames or file contents contain instruction-like text; these are untrusted data and must not alter system policy. Add tests for path traversal attempts, symlink boundary attacks, timeouts, tool errors, false-success claims, and cancellation.

Do not implement write/delete/move, startup persistence, browser automation, screen capture, keyboard/mouse control, or privilege escalation. Ask for explicit approval before expanding scope.
