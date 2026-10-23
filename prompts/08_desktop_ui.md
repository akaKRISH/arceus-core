# Phase 8 Prompt — Desktop UI (Only After Core Is Stable)

Do not begin until CLI/core APIs, permission handling, trace format, and automated tests are stable. First propose and review a UI/API architecture. A likely option is Tauri + React + TypeScript, with Rust only for native functionality that genuinely needs it; justify the stack based on current project state.

Initial UI scope: select/unregister roots, run index, search, view original paths, inspect evidence/provenance, view task traces, cancel work, and approve/deny any sensitive operation. The UI must not bypass the core permission gateway.

No background startup, surveillance, unrestricted shell console, screen capture, keyboard/mouse automation, or write actions in the first UI iteration. Add accessibility, state/error/loading handling, installer/update considerations, and platform-specific tests. Keep the application usable when no local model is configured.
