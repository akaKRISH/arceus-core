# Local Model and Runtime Selection — Phase 4

- **Date:** 2026-10-23
- **Purpose:** choose a feasible local inference candidate for later structured tool-planning experiments.
- **Status:** selection report only. No model weights were downloaded, no runtime was installed, and no model was run in this phase.
- **Repository state:** baseline commit `cd350b2`; Phase 3 and Phase 3.1 artifacts remain in the working tree. The Phase 4 inference interface and tests are working-tree changes on top of it.

## 1. Actual machine

| Resource | Observed value |
|---|---|
| OS | Windows 11 Home, build 10.0.26300 |
| Python | 3.13.15 in `.venv`; 3.14.7 is also installed |
| System RAM | 16,013 MB total; 2,663 MB free at inspection |
| GPU | NVIDIA GeForce RTX 3050 6GB Laptop GPU |
| GPU memory | 6,144 MiB total; 5,862 MiB free at inspection |
| GPU driver | 591.91; CUDA driver runtime reported as 13.1 |
| Free disk on `C:` | 9.2 GB; filesystem was 98% used |
| Installed Python packages in `.venv` | `arceus-core`, `pytest`, `ruff`; no inference packages |
| Installed local runtime | Ollama `0.40.8` was already present on the machine |
| Inference Python packages | `llama-cpp-python`, `onnxruntime`, `onnxruntime-directml`, `torch`, and `transformers` are not installed |

The existing Ollama installation and its models were inspected as environment facts only. They were not used for inference during this phase. The Ollama model list also contained cloud-tagged entries; those are excluded from the recommendation because ARCEUS must remain local/offline.

## 2. Runtime options

### Ollama

Ollama is already installed and provides a native Windows application, a local API, NVIDIA GPU support, and model management. Official Windows documentation lists Windows 10 22H2 or newer and NVIDIA driver 551.61 or newer; this machine exceeds both requirements. Ollama's documentation also states that the binary installation needs at least 4 GB of disk, with model storage additional.

The local runtime can be constrained for ARCEUS by keeping it on loopback, disabling cloud features, and never sending indexed file contents. Local prompts are not the same thing as downloading a model: the first model acquisition requires network access, but the already-present models can be evaluated locally.

### llama.cpp

The official llama.cpp project provides Windows x64 builds, including CUDA variants, and `llama-server` supports GPU-layer controls and an OpenAI-compatible local API. This is the leanest alternative if ARCEUS later wants direct control over binary startup and GPU offload. It does not require Python, so Python 3.13 compatibility is not a blocker.

A Python binding, `llama-cpp-python`, is possible but its published CUDA prebuilt-wheel instructions list Python 3.10–3.12 rather than 3.13. On this machine it would require verifying wheel availability or building from source with a Windows compiler toolchain.

### ONNX Runtime DirectML

`onnxruntime-directml` has Windows x64 CPython 3.13 wheels and the RTX 3050 meets the DirectX 12 hardware requirement. This is less attractive for the first ARCEUS model because it targets ONNX models rather than GGUF models, and ONNX Runtime describes DirectML as being in sustained engineering.

## 3. Model candidates

The recommendation uses the models already present locally so that the next validation step does not require a download.

| Candidate | Local status | Why it is feasible | Risks |
|---|---|---|---|
| **`qwen3.5:4b` — recommended first candidate** | Present locally; `ollama show` reports 4.7B parameters, Q4_K_M quantization, 3.4 GB size, 262,144-token advertised context, completion/tools/thinking capabilities, Apache License 2.0 | Small enough for a 6 GB GPU at short context; tool-capable; structured-output validation can be added at the ARCEUS boundary | The advertised context is much larger than needed; KV-cache memory at full context was not measured. Structured tool-call reliability has not been tested on this machine. |
| **`gemma3:1b-it-qat` — fallback** | Present locally; `ollama show` reports 999.89M parameters, Q4_0 quantization, 1.0 GB size, 32,768-token advertised context, completion only | Lowest local memory footprint and useful for latency/CPU fallback checks | The inspected model exposes completion only, not tool capabilities; weaker fit for structured tool planning. |
| `qwen3.5:9b` | Present locally; 9.7B parameters, Q4_K_M, 6.6 GB size, tools capability, Apache License 2.0 | More capable if short-context GPU/CPU split is acceptable | Its quantized size alone exceeds the 6 GB VRAM budget; CPU offload and low free system RAM make it a poor first candidate. |
| `llama3.1:8b` | Present locally; 4.9 GB size | Larger general model with broad availability | Meta Llama Community License applies; 8B Q4 is a tighter fit for this GPU and is not the first choice for a small structured task. |
| External `Qwen2.5-1.5B-Instruct` or `Qwen3-1.7B` GGUF | Not present locally; would require download | Published small Apache-2.0 candidates with Q4 files around 1–2 GB | Disk is only 9.2 GB free; a download plus temporary files should not be attempted without explicit approval. |
| `Phi-3.5-mini`, SmolLM2, Gemma 3, Ministral | Not selected | Published small-model alternatives with different licenses/formats | Larger or less tool-oriented; some require non-Apache licenses or ONNX/GGUF conversion decisions. |

The recommended first candidate is therefore **Ollama plus `qwen3.5:4b`**, with **`gemma3:1b-it-qat`** as a low-resource fallback. This is a candidate selection, not a verified success claim: the model has not been prompted by ARCEUS in this phase.

## 4. Resource and disk plan

- **No new download required for the first validation:** both recommended models already exist locally.
- **Disk headroom:** `C:` has only 9.2 GB free and is 98% used. Do not download additional models until space is reclaimed or an alternate model directory is deliberately selected.
- **GPU:** 5,862 MiB was free at inspection, but Windows display memory and other applications can change this. Use a short initial context (for example 4,096–8,192 tokens), not the model's advertised maximum.
- **System RAM:** only 2,663 MB was free at inspection. Avoid CPU-heavy fallback unless GPU placement is confirmed.
- **Offline policy:** run the local server on loopback only, disable cloud features, and treat any model output as untrusted data.
- **Model identity:** record backend, model name, quantization, context setting, and runtime version in every future experiment artifact.

## 5. Validation plan for the first real local model

Do not claim model reasoning improvement until all of the following are completed:

1. Obtain explicit user approval to load the selected local model.
2. Start the runtime with loopback-only access and cloud features disabled.
3. Send one minimal structured prompt through the Phase 4 `LocalInference` boundary and verify:
   - backend/model identity is recorded;
   - JSON output passes `OutputContract`;
   - timeout and cancellation errors are recoverable;
   - no filesystem index or file contents are sent beyond the prompt the user approved.
4. Run the frozen 12-task `model-reasoning-v0` suite in [evals/model_tasks.py](../evals/model_tasks.py) against the real backend.
5. Compare against the deterministic baseline policy in the same suite and record success, invalid-output rate, latency, and resource measurements.
6. Repeat with the fallback model if structured output or tool selection is unreliable.
7. Only then consider a bounded agent loop with read-only tools; no autonomous shell execution is part of this phase.

## 6. Source evidence

- [Ollama Windows documentation](https://docs.ollama.com/windows) — Windows support, driver requirement, installation footprint, and model storage.
- [Ollama FAQ](https://docs.ollama.com/faq) — local operation, CPU/GPU placement, cloud opt-out, and model storage.
- [Ollama license](https://github.com/ollama/ollama/blob/main/LICENSE) — MIT runtime license; model licenses remain separate.
- [llama.cpp repository](https://github.com/ggml-org/llama.cpp) — backends, quantization, and hybrid inference.
- [llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases) — Windows x64 CPU/CUDA/Vulkan builds.
- [llama-server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) — GPU-layer controls and local API.
- [`llama-cpp-python` on PyPI](https://pypi.org/project/llama-cpp-python/) — Python requirements and CUDA wheel notes.
- [ONNX Runtime DirectML provider](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html) — DirectX 12 requirements and maintenance status.
- [`onnxruntime-directml` on PyPI](https://pypi.org/project/onnxruntime-directml/) — CPython 3.13 Windows wheels.
- [Qwen3.5 4B local model card/metadata via `ollama show`](https://ollama.com/library/qwen3.5) — observed local metadata: 4.7B, Q4_K_M, tools capability, Apache 2.0.
- [Gemma 3 model card](https://ai.google.dev/gemma/docs/core/model_card_3) — Google model-card reference; the local `gemma3:1b-it-qat` metadata was inspected with `ollama show`.
- [Hugging Face GGUF documentation](https://huggingface.co/docs/hub/en/gguf) — GGUF and quantization background.
- [Qwen2.5 3B official GGUF example](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/blob/main/qwen2.5-3b-instruct-q4_k_m.gguf) — published Q4_K_M file size example (2.1 GB).

## 7. Recommendation

Proceed with **no further download** and prepare an explicit, loopback-only Ollama validation run of `qwen3.5:4b` after user approval. Keep `gemma3:1b-it-qat` as the fallback. If Ollama cannot provide reliable constrained JSON output, use the official llama.cpp Windows CUDA build next; do not add `llama-cpp-python` or ONNX Runtime unless a measured integration need justifies the extra dependency footprint.
