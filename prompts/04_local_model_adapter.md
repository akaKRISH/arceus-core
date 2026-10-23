# Phase 4 Prompt — Optional Local Model Adapter

Only proceed after filesystem and graph APIs/tests pass and the owner has approved model selection and download.

1. Inspect actual system RAM, GPU model and VRAM, driver/runtime support, available disk, expected context length, and latency goals. Do not infer model fit from system RAM alone.
2. Present at least two candidate local model/runtime configurations, including expected trade-offs and storage requirements. Use current vendor/project documentation. Do not download anything until approved.
3. Implement a provider interface separate from core services. Include model ID/version, structured request/output, cancellation/timeout, clear error paths, and timing/resource metrics.
4. Validate model outputs against schemas before use. Model unavailability must not break deterministic search/index functions.
5. Add tests using a fake model adapter so most tests require neither network nor GPU.
6. Do not add a cloud model as a fallback unless separately requested and approved. No automatic fallback that leaks local paths or file content.

Report exact model/runtime version and configuration. Do not present one successful response as proof of reasoning or generalization.
