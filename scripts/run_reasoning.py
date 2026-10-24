from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.model_tasks import (
    MODEL_RESPONSE_CONTRACT,
    MODEL_TASK_VERSION,
    MODEL_TASKS,
    score_model_suite,
)

from arceus_core.errors import InferenceError
from arceus_core.inference import InferenceRequest, LocalInference
from arceus_core.inference_ollama import OllamaBackend

BACKEND_CONFIG = {
    "base_url": "http://127.0.0.1:11434",
    "model": "qwen3.5:4b",
    # ponytail: 64 tokens truncated every response mid-JSON (see SPRINT_STATUS.md);
    # 256 emits the full 5-key object with reason. Raise only on new truncation.
    "num_predict": 256,
    "num_ctx": 2048,
    "temperature": 0.0,
    "top_p": 1.0,
    "timeout_seconds": 120.0,
}

OUTPUT = Path(f"eval_raw_np{BACKEND_CONFIG['num_predict']}.json")


def main() -> int:
    backend = OllamaBackend(**BACKEND_CONFIG)
    inference = LocalInference(backend)

    t0 = time.perf_counter()
    raw_map: dict[str, dict[str, object]] = {}
    for task in MODEL_TASKS:
        req = InferenceRequest(
            prompt=task.prompt_text(),
            contract=MODEL_RESPONSE_CONTRACT,
            timeout_seconds=90.0,
        )
        try:
            resp = inference.generate(req)
        except InferenceError as exc:
            raw_map[task.id] = {
                "raw": "",
                "parse_error": str(exc),
                "wall_seconds": None,
                "output_chars": 0,
            }
            continue
        raw_map[task.id] = {
            "raw": resp.text,
            "parse_error": None,
            "wall_seconds": resp.metrics.wall_time_seconds,
            "output_chars": resp.metrics.output_chars,
        }

    parsed_map: dict[str, object] = {}
    for task_id, row in raw_map.items():
        if row["parse_error"] is not None:
            parsed_map[task_id] = {"_parse_error": row["parse_error"], "_raw_preview": ""}
            continue
        try:
            parsed_map[task_id] = json.loads(str(row["raw"]))
        except Exception as exc:
            parsed_map[task_id] = {
                "_parse_error": repr(exc),
                "_body_preview": str(row["raw"])[:200],
            }

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
            "think": False,
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
    raise SystemExit(main())
