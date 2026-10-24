"""Run the frozen model-reasoning-v0 tasks against the live Ollama qwen3.5:4b backend.

This entry point is intentionally separate from the graph retrieval benchmark CLI
because it depends on a local model being available and is not part of the offline
retrieval evaluation.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from arceus_core.inference import InferenceRequest, LocalInference
from arceus_core.inference_ollama import OllamaBackend
from evals.model_tasks import MODEL_TASK_VERSION, MODEL_TASKS, score_model_suite

BACKEND_CONFIG = {
    "base_url": "http://127.0.0.1:11434",
    "model": "qwen3.5:4b",
    "num_predict": 64,
    "num_ctx": 2048,
    "temperature": 0.0,
    "top_p": 1.0,
    "think": False,
    "timeout_seconds": 120.0,
}

OUTPUT = Path("eval_raw.json")


def first_json_object(text: str) -> tuple[str | None, str | None]:
    start = text.find("{")
    if start == -1:
        return None, "no_brace"
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1], None
        i += 1
    return None, "unterminated"


def raw_for(backend: OllamaBackend, task) -> tuple[str, float, int]:
    inf = LocalInference(backend)
    req = InferenceRequest(prompt=task.prompt_text(), system_prompt="", timeout_seconds=90.0)
    resp = inf.generate(req)
    return resp.text, resp.metrics.wall_time_seconds, resp.metrics.output_chars


def main() -> int:
    backend = OllamaBackend(**BACKEND_CONFIG)

    t0 = time.perf_counter()
    raw_map: dict[str, dict[str, object]] = {}
    for task in MODEL_TASKS:
        text, ws, oc = raw_for(backend, task)
        raw_map[task.id] = {"raw": text, "wall_seconds": ws, "output_chars": oc}

    parsed_map: dict[str, object] = {}
    for task in MODEL_TASKS:
        raw = raw_map[task.id]["raw"]
        body, err = first_json_object(raw)
        if err:
            parsed_map[task.id] = {"_parse_error": err, "_raw_preview": raw[:200]}
        else:
            try:
                parsed_map[task.id] = json.loads(body)
            except Exception as exc:
                parsed_map[task.id] = {"_parse_error": repr(exc), "_body_preview": body[:200]}

    score = score_model_suite(MODEL_TASKS, parsed_map)
    total_seconds = time.perf_counter() - t0

    artifact = {
        "version": MODEL_TASK_VERSION,
        "backend_id": "ollama",
        "model_id": BACKEND_CONFIG["model"],
        "backend_config": {
            "num_predict": BACKEND_CONFIG["num_predict"],
            "num_ctx": BACKEND_CONFIG["num_ctx"],
            "temperature": BACKEND_CONFIG["temperature"],
            "top_p": BACKEND_CONFIG["top_p"],
            "think": BACKEND_CONFIG["think"],
            "timeout_seconds": BACKEND_CONFIG["timeout_seconds"],
        },
        "tasks_run": len(MODEL_TASKS),
        "tasks_total": len(MODEL_TASKS),
        "total_seconds": round(total_seconds, 6),
        "score": score,
        "raw_map": raw_map,
        "parsed_map": parsed_map,
        "generated_at_utc": "2026-10-23T17:00:00+00:00",
    }
    OUTPUT.write_text(json.dumps(artifact, indent=2), encoding="utf-8")

    print("eval_raw_ok=true")
    print(f"tasks_run={len(MODEL_TASKS)}")
    print(f"tasks_total={len(MODEL_TASKS)}")
    print(f"total_seconds={round(total_seconds, 6)}")
    print(f"score={json.dumps(score, sort_keys=True)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
