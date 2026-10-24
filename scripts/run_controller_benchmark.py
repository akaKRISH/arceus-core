"""Run the frozen controller-action-v0 benchmark.

Arms:
  deterministic (default) — controller without any inference backend.
  model — controller with the local Ollama backend (only after the
      deterministic arm passes and the machine is healthy).

Usage:
  python scripts/run_controller_benchmark.py            # deterministic
  python scripts/run_controller_benchmark.py --model    # + qwen arm
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.controller_tasks import controller_tasks, summarize

from arceus_core.controller import CognitiveController
from arceus_core.database import open_database
from arceus_core.roots import add_root
from arceus_core.scanner import scan_root

REPORTS = Path(__file__).resolve().parents[1] / "reports"


def build_fixture(tmp: Path):
    """Synthetic index. Mirrors the ground truth in evals/controller_tasks.py."""

    root_dir = tmp / "work"
    files = {
        "budget.xlsx": "",
        "readme.md": "top level",
        "docs/readme.md": "docs copy",
        "report_q1.xlsx": "",
        "report_draft.xlsx": "",
        "src/main.py": "print('hi')",
        "src/util/helper.py": "",
        "project/src/main.py": "print('p')",
        "project/src/utils.py": "",
    }
    for relative, content in files.items():
        target = root_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    db_path = tmp / "arceus.sqlite3"
    db_cm = open_database(db_path)
    conn = db_cm.__enter__()
    root = add_root(conn, root_dir, label="work")
    scan_root(conn, root)
    return conn, db_cm


def run_arm(arm: str) -> dict[str, object]:
    tasks = controller_tasks()
    with tempfile.TemporaryDirectory(prefix="arceus-ctrl-bench-") as tmp_name:
        conn, db_cm = build_fixture(Path(tmp_name))
        try:
            if arm == "deterministic":
                controller = CognitiveController(conn)
            else:
                from arceus_core.inference import LocalInference
                from arceus_core.inference_ollama import OllamaBackend

                backend = OllamaBackend(
                    model="qwen3.5:4b",
                    num_predict=256,
                    num_ctx=2048,
                    temperature=0.0,
                    timeout_seconds=120.0,
                )
                controller = CognitiveController(conn, inference=LocalInference(backend))

            proposals: dict[str, dict[str, object]] = {}
            traces: dict[str, dict[str, object]] = {}
            timings: dict[str, float] = {}
            for task in tasks:
                started = time.perf_counter()
                proposal, trace = controller.decide_with_trace(task.controller_input())
                timings[task.id] = round(time.perf_counter() - started, 6)
                proposals[task.id] = proposal.to_dict()
                traces[task.id] = trace.to_dict()
        finally:
            db_cm.__exit__(None, None, None)

    summary = summarize(tasks, proposals, arm=arm, traces=traces)
    summary["wall_seconds_by_task"] = timings
    summary["total_seconds"] = round(sum(timings.values()), 6)
    summary["tuned_config_id"] = config_identifier()
    return summary


def config_identifier() -> str:
    """Stable identifier of the frozen prompt/schema/configuration set."""

    import hashlib

    from arceus_core.controller import (
        CONTROLLER_CONTRACT,
        CONTROLLER_JSON_SCHEMA,
        NO_ROOT_SECTION,
        ROOT_LABEL_SECTION,
        SYSTEM_PROMPT_BASE,
    )

    payload = json.dumps(
        {
            "prompt": SYSTEM_PROMPT_BASE,
            "root_label_section": ROOT_LABEL_SECTION,
            "no_root_section": NO_ROOT_SECTION,
            "contract": json.dumps(
                {
                    "name": CONTROLLER_CONTRACT.name,
                    "required_keys": list(CONTROLLER_CONTRACT.required_keys),
                    "field_types": dict(CONTROLLER_CONTRACT.field_types),
                    "allow_extra_keys": CONTROLLER_CONTRACT.allow_extra_keys,
                },
                sort_keys=True,
            ),
            "json_schema": json.dumps(dict(CONTROLLER_JSON_SCHEMA), sort_keys=True),
        },
        sort_keys=True,
    )
    return "ctrl-config-v1-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="store_true", help="also run the qwen arm")
    args = parser.parse_args()

    REPORTS.mkdir(exist_ok=True)
    for arm in ("deterministic", "model") if args.model else ("deterministic",):
        print(f"==== arm: {arm}")
        summary = run_arm(arm)
        out = REPORTS / f"controller-action-v0_{arm}.json"
        out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(
            f"score={summary['success_count']}/{summary['task_count']}"
            f" unsafe={summary['unsafe_proposals']}"
            f" unnecessary_abstain={summary['unnecessary_abstentions']}"
            f" model_generated={summary['model_generated_proposals']}"
            f" fallback={summary['deterministic_fallback_proposals']}"
            f" total_seconds={summary['total_seconds']}"
        )
        print(f"attribution_partitioned={summary['attribution_partitioned']}")
        print(f"model_task_correctness={summary['model_task_correctness']}")
        print(f"model_rejection_stages={summary['model_rejection_stages']}")
        print(f"config_id={summary['tuned_config_id']}")
        for task_id, entry in summary["tasks"].items():
            if not entry["success"]:
                print(f"  FAIL {task_id}: {entry['failures']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
