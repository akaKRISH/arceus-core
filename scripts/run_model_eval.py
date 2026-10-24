"""Model-arm evaluation runner for a frozen controller prompt/schema.

Usage::

    python scripts/run_model_eval.py --suite dev
    python scripts/run_model_eval.py --suite heldout --out reports/x.json

Runs the model arm sequentially over the ``controller-dev-v1`` prompt
development suite (default) or the ``controller-heldout-v1`` held-out suite.
It records, per task: the per-stage decision trace (parse/contract/
permission outcomes, rejection stage and reason, raw model text), explicit
``origin`` provenance, final action, and final task correctness.

Model-vs-fallback metrics come from the shared frozen ``summarize`` rules in
``evals/controller_tasks.py``. Resource notes: free RAM is checked before the
run and inference is strictly sequential (one task at a time, single model
load); Ollama reachability (loopback 127.0.0.1:11434) is verified first.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.controller_tasks import summarize  # noqa: E402
from evals.prompt_dev_tasks import (  # noqa: E402
    PROMPT_DEV_VERSION,  # noqa: E402
    dev_fixture,
    dev_tasks,
)
from evals.prompt_heldout_tasks import (  # noqa: E402
    PROMPT_HELDOUT_VERSION,
    heldout_fixture,
    heldout_tasks,
)
from scripts.run_controller_benchmark import config_identifier  # noqa: E402

from arceus_core.controller import CognitiveController  # noqa: E402
from arceus_core.database import open_database  # noqa: E402
from arceus_core.roots import add_root  # noqa: E402
from arceus_core.scanner import scan_root  # noqa: E402

REPORTS = Path(__file__).resolve().parents[1] / "reports"

# Free-RAM floor below which a model run is refused (Windows resource note).
MIN_FREE_RAM_BYTES = 800 * 1024 * 1024
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"

SUITES = {
    "dev": {
        "version": PROMPT_DEV_VERSION,
        "fixture": dev_fixture,
        "tasks": dev_tasks,
    },
    "heldout": {
        "version": PROMPT_HELDOUT_VERSION,
        "fixture": heldout_fixture,
        "tasks": heldout_tasks,
    },
}


def _free_ram_bytes() -> int:
    class _MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    stat = _MemoryStatusEx()
    stat.dwLength = ctypes.sizeof(_MemoryStatusEx)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
    return int(stat.ullAvailPhys)


def _ollama_alive() -> bool:
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def build_suite_fixture(tmp: Path, files: dict[str, str]):
    """Synthetic suite index, mirroring the ground truth in the task module."""

    root_dir = tmp / "work"
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


def run_suite(suite: str, out_path: Path | None = None, max_tasks: int | None = None) -> dict:
    info = SUITES[suite]
    tasks_all = info["tasks"]()
    tasks = tasks_all if max_tasks is None else tasks_all[:max_tasks]

    if not _ollama_alive():
        raise SystemExit(
            f"Ollama is not reachable at {OLLAMA_TAGS_URL} — refusing to start the model arm."
        )
    free = _free_ram_bytes()
    if free < MIN_FREE_RAM_BYTES:
        raise SystemExit(f"Refusing model run: only {free} bytes free RAM (< 800 MiB floor).")
    print(f"[resource] free RAM before run: {free / (1024 * 1024):.0f} MiB")

    from arceus_core.inference import LocalInference
    from arceus_core.inference_ollama import OllamaBackend

    backend = OllamaBackend(
        model="qwen3.5:4b",
        num_predict=256,
        num_ctx=2048,
        temperature=0.0,
        timeout_seconds=120.0,
    )

    proposals: dict[str, dict] = {}
    traces: dict[str, dict] = {}
    timings: dict[str, float] = {}
    with tempfile.TemporaryDirectory(prefix=f"arceus-{suite}-") as tmp_name:
        conn, db_cm = build_suite_fixture(Path(tmp_name), info["fixture"]())
        try:
            controller = CognitiveController(conn, inference=LocalInference(backend))
            for index, task in enumerate(tasks):  # strictly sequential
                started = time.perf_counter()
                proposal, trace = controller.decide_with_trace(task.controller_input())
                timings[task.id] = round(time.perf_counter() - started, 6)
                proposals[task.id] = proposal.to_dict()
                traces[task.id] = trace.to_dict()
                print(
                    f"[{index + 1}/{len(tasks)}] {task.id}: "
                    f"origin={proposal.origin} action={proposal.action} "
                    f"stage={trace.rejection_stage} {timings[task.id]}s"
                )
        finally:
            db_cm.__exit__(None, None, None)

    summary = summarize(tasks, proposals, arm="model", traces=traces)
    summary["version"] = info["version"]
    summary["wall_seconds_by_task"] = timings
    summary["total_seconds"] = round(sum(timings.values()), 6)
    summary["tuned_config_id"] = config_identifier()

    out = out_path or REPORTS / f"prompt-eval-{suite}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(
        f"score={summary['success_count']}/{summary['task_count']}"
        f" model_generated={summary['model_generated_proposals']}"
        f" fallback={summary['deterministic_fallback_proposals']}"
        f" valid_json={summary['model_valid_json_rate']}"
        f" contract={summary['model_contract_compliance_rate']}"
        f" permission={summary['model_permission_acceptance_rate']}"
        f" total_seconds={summary['total_seconds']}"
    )
    print(f"attribution_partitioned={summary['attribution_partitioned']}")
    print(f"config_id={summary['tuned_config_id']}")
    for task_id, entry in summary["tasks"].items():
        if not entry["success"]:
            print(f"  FAIL {task_id}: {entry['failures']}")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=sorted(SUITES), default="dev")
    parser.add_argument("--out", default=None)
    parser.add_argument("--max-tasks", type=int, default=None)
    args = parser.parse_args(argv)
    run_suite(args.suite, None if args.out is None else Path(args.out), args.max_tasks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
