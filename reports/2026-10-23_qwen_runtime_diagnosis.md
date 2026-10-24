# QWEN3.5:4B RUNTIME FAILURE DIAGNOSIS

## Summary
The earlier `llama-server` startup crash and `GGML_ASSERT(ctx->mem_buffer != NULL) failed` are **no longer reproducible** on this machine. A minimal Ollama `/api/chat` request to `qwen3.5:4b` now succeeds.

## Environment
- Ollama: `0.40.9` (latest per GitHub releases page)
- Model: `qwen3.5:4b` — 4.7B params, `Q4_K_M`, context 262144, quantization Q4_K_M, requires 0.17.1
- OS: Windows
- GPU: NVIDIA RTX 3050 6GB
- Driver/CUDA: NVIDIA-SMI 591.91, Driver 591.91, CUDA 13.1
- RAM: ~16 GB total, ~489 MB free at measurement time
- C: free: ~8.0 GB
- Ollama slot status shows qwen3.5:4b loaded at 100% GPU, ctx 2048, runner llamacpp

## Evidence of failure before this run
- Prior smoke test via direct Ollama API and adapter call failed.
- Error text from prior run: `llama-server startup failed after projector CPU offload retry: llama-server process no longer running: exit status 0xc0000409: The system detected an overrun of a stack-based buffer in this application... GGML_ASSERT(ctx->mem_buffer != NULL) failed`

## Current reproduction attempt
- Request: POST `http://127.0.0.1:11434/api/chat`
  - model: `qwen3.5:4b`
  - stream: false
  - think: false
  - format: json
  - options: num_ctx=2048, num_predict=64, temperature=0
  - prompt: synthetic one-shot JSON request
- Result: HTTP 200, valid JSON response returned
- Typical load/eval times observed in logs: prompt eval ~397.61 ms for 4 tokens, eval ~156.37 ms for 6 tokens

## Server log evidence
- Model loading path exists in the log, indicating prior load events.
- Earlier load attempt leaves lines:
  - `waiting for llama-server to become available` with `llm server error`, then `llm server loading model`
- Recent successful request leaves timing slots and successful `/api/chat` 200 entries.
- Repeated WARN lines: `invalid option provided` option=`kv_cache_type`, indicating an unsupported option was attempted then ignored.

## Interpretation
- The failure is most consistent with a transient model-loading/runtime failure in `llama-server`, likely related to CUDA/GPU offload or memory/allocator behavior at load time.
- Because the same model now loads and answers, the issue is not a permanent incompatibility between this Ollama version and `qwen3.5:4b`.
- The `invalid option: kv_cache_type` warning suggests some requests may carry unsupported options; this is independent of the crash but worth removing from adapter payloads.

## What is NOT evidenced
- No direct evidence that this is solely a VRAM limit, Ollama bug, driver bug, or project code bug.
- No crash dump or post-mortem log was available in `%LOCALAPPDATA%\Ollama`.
- No network or external dependency is involved; this is local only.

## Safest next action
- Do not re-run the same crash repeatedly as a diagnostic.
- Use the currently working local model path for the next validation/eval step, with a small low-context request first, and treat any future crash as a separate incident to capture with logs before retrying.

## Changes kept unmodified
- ARCEUS adapter, tests, and git state remain as previously modified but uncommitted.
- No model pull, deletion, driver change, or Ollama upgrade was performed.