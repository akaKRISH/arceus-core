"""Run the frozen executor-workflow-v0 benchmark.

Arms:
  deterministic (default) — cognitive controller without an inference backend.
  model — full workflow with the local qwen3.5:4b backend (only after the
      deterministic arm passes and the machine is healthy).

Usage:
  python scripts/run_executor_benchmark.py            # deterministic arm
  python scripts/run_executor_benchmark.py --model    # + qwen arm

Every record is attributable: origin, fallback_used (model was called and an
earlier stage failed), verification, execution, and the final task score.
Fallback-assisted successes are reported separately, never inside the
model's score.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.controller_tasks import VALID_ACTIONS, score_controller_proposal
from evals.execution_tasks import (
    EXECUTION_BENCHMARK_VERSION,
    EXECUTION_FIXTURE_FILES,
    execution_tasks,
)
from scripts.run_controller_benchmark import config_identifier

from arceus_core.controller import CognitiveController
from arceus_core.database import open_database
from arceus_core.roots import add_root
from arceus_core.scanner import scan_root
from arceus_core.workflow import TaskWorkflow

REPORTS = Path(__file__).resolve().parents[1] / "reports"


def build_fixture(tmp: Path):
    """Synthetic index mirroring the ground truth in evals/execution_tasks.py."""

    root_dir = tmp / "work"
    for relative, content in EXECUTION_FIXTURE_FILES.items():
        target = root_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    db_cm = open_database(tmp / "arceus.sqlite3")
    conn = db_cm.__enter__()
    root = add_root(conn, root_dir, label="work")
    scan_root(conn, root)
    return conn, db_cm


def _terminal_path(result, success: bool) -> str:
    """Exactly one terminal path per task, derived from explicit fields.

    ``model_called`` and ``fallback_used`` are set in code from the decision
    trace; ``origin`` is a literal set by the controller. Nothing here is
    inferred from rationale or note strings.
    """

    if result is None:
        return "missing_result"
    if not result.model_called:
        return "deterministic_success" if success else "deterministic_failure"
    if result.origin == "model":
        return "model_success" if success else "model_wrong"
    return "fallback_success" if success else "fallback_failure"


def _final_success(task, result) -> bool:
    """End-to-end success against frozen ground truth (the FINAL validated
    proposal, after any explicit fallback), via the same frozen scoring rules
    as the controller benchmark."""

    if result is None:
        return False
    score = score_controller_proposal(
        task,
        {
            "action": result.proposed_action,
            "arguments": result.proposed_arguments,
            "status": result.status,
        },
    )
    # An executed tool action that the verifier could not confirm is NOT a
    # success even when the proposal matched: verification gates success.
    if result.executed and result.verification_status != "verified":
        return False
    return score.success


def summarize(tasks, results: dict) -> dict:
    """Score the suite with explicit model/fallback attribution.

    Seven measured dimensions (denominators stated per metric):
      1. first_proposal_accuracy — of tasks where the model was called, how
         often the model's OWN action (``model_action``, recorded before any
         fallback) matched the frozen expected action. A fallback success can
         never inflate this number.
      2. model_validation_rate — of model-called tasks, how often the
         proposal passed parse+contract+controller+permission stages
         (``rejection_stage is None`` and the origin is ``model``).
      3. model_originated_success_count — tasks whose final outcome came from
         a model-originated proposal AND satisfied the frozen ground truth
         AND (if executed) passed independent verification.
      4. fallback_frequency — of model-called tasks, how often a rejection at
         any stage handed the task to the deterministic policy.
      5. fallback_assisted_success_count — successes that ONLY the fallback
         produced. Reported separately; never added to model scores.
      6. end_to_end_success_count — final workflow outcome vs frozen ground
         truth, regardless of which component was responsible.
      7. safety — unsafe final actions (outside the allow-list), permission
         rejections, and executed-but-unverified results.
    """

    by_task: dict[str, dict] = {}
    category_counts: dict[str, dict[str, int]] = {}
    terminal_counts: dict[str, int] = {}

    # model-side counters (denominator: model_called_count)
    model_called_count = 0
    first_proposal_correct = 0
    model_valid_output = 0
    model_validated = 0
    model_originated_success = 0
    fallback_count = 0
    fallback_assisted_success = 0

    # workflow-side counters (denominator: task_count)
    end_to_end_success = 0
    executed_count = 0
    verified_completed = 0
    executed_unverified = 0
    permission_rejections = 0
    unsafe_actions = 0

    for task in tasks:
        result = results.get(task.id)
        success = _final_success(task, result)
        terminal = _terminal_path(result, success)
        terminal_counts[terminal] = terminal_counts.get(terminal, 0) + 1

        end_to_end_success += int(success)
        bucket = category_counts.setdefault(task.category, {"tasks": 0, "successes": 0})
        bucket["tasks"] += 1
        bucket["successes"] += int(success)

        record: dict = {
            "task": task.id,
            "category": task.category,
            "terminal_path": terminal,
            "success": success,
            "status": None,
            "proposed_action": None,
            "proposed_arguments": None,
            "origin": None,
            "fallback_used": None,
            "executed": None,
            "verification_status": None,
            "task_completed": None,
            "model_called": False,
            "model_action": None,
            "rejection_stage": None,
            "rejection_reason": None,
            "wall_seconds": 0.0,
            "explanation": "",
        }

        if result is not None:
            record.update(
                status=result.status,
                proposed_action=result.proposed_action,
                proposed_arguments=dict(result.proposed_arguments),
                origin=result.origin,
                fallback_used=result.fallback_used,
                executed=result.executed,
                verification_status=result.verification_status,
                task_completed=result.task_completed,
                model_called=result.model_called,
                model_action=result.model_action,
                rejection_stage=result.rejection_stage,
                rejection_reason=result.rejection_reason,
                wall_seconds=result.wall_seconds,
                explanation=result.explanation,
            )
            executed_count += int(result.executed)
            verified_completed += int(
                result.executed
                and result.verification_status == "verified"
                and result.task_completed is True
            )
            executed_unverified += int(result.executed and result.verification_status != "verified")
            if result.rejection_stage == "permission":
                permission_rejections += 1
            if result.proposed_action not in VALID_ACTIONS:
                unsafe_actions += 1

            if result.model_called:
                model_called_count += 1
                # First-proposal accuracy: the model's own action, captured
                # in the trace BEFORE any deterministic fallback.
                first_proposal_correct += int(result.model_action == task.expected_action)
                # valid output = parse+contract succeeded (a later stage
                # rejecting is an output-valid but permission-invalid case).
                if result.rejection_stage not in {
                    "json_parse",
                    "contract",
                    "model_unavailable",
                }:
                    model_valid_output += 1
                if result.origin == "model":
                    model_validated += 1
                    model_originated_success += int(success)
                elif result.fallback_used:
                    fallback_count += 1
                    fallback_assisted_success += int(success)

        by_task[task.id] = record

    # Partition sanity: every model-called task is exactly one of
    # (origin == model) or (fallback_used); both cannot hold.
    assert model_validated + fallback_count == model_called_count
    assert sum(terminal_counts.values()) == len(tasks)

    def _rate(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 4) if denominator else 0.0

    return {
        "version": EXECUTION_BENCHMARK_VERSION,
        "task_count": len(tasks),
        # 6. end-to-end
        "end_to_end_success_count": end_to_end_success,
        "success_rate": _rate(end_to_end_success, len(tasks)),
        "by_category": category_counts,
        "terminal_paths": terminal_counts,
        # 1-3. model side (denominator: model_called_count)
        "model_called_count": model_called_count,
        "first_proposal_correct_count": first_proposal_correct,
        "first_proposal_accuracy": _rate(first_proposal_correct, model_called_count),
        "model_valid_output_count": model_valid_output,
        "model_valid_output_rate": _rate(model_valid_output, model_called_count),
        "model_validated_count": model_validated,
        "model_validation_rate": _rate(model_validated, model_called_count),
        "model_originated_success_count": model_originated_success,
        # 4-5. fallback side (denominator: model_called_count)
        "fallback_count": fallback_count,
        "fallback_frequency": _rate(fallback_count, model_called_count),
        "fallback_assisted_success_count": fallback_assisted_success,
        # workflow integrity
        "executed_count": executed_count,
        "verified_completed_count": verified_completed,
        "executed_unverified_count": executed_unverified,
        # 7. safety
        "unsafe_actions": unsafe_actions,
        "permission_rejection_count": permission_rejections,
        "tasks": by_task,
    }


def run_arm(arm: str) -> dict:
    tasks = list(execution_tasks())
    with tempfile.TemporaryDirectory(prefix="arceus-exec-bench-") as tmp_name:
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

            workflow = TaskWorkflow(conn, controller, task_id_prefix=arm)
            results: dict[str, object] = {}
            for task in tasks:
                result = workflow.run(task.controller_input())
                results[task.id] = result
        finally:
            db_cm.__exit__(None, None, None)

    summary = summarize(tasks, results)
    summary["arm"] = arm
    summary["total_seconds"] = round(
        sum(float(entry.get("wall_seconds") or 0.0) for entry in summary["tasks"].values()),
        4,
    )
    summary["frozen_config_id"] = config_identifier()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="store_true", help="also run the qwen arm")
    args = parser.parse_args()

    REPORTS.mkdir(exist_ok=True)
    for arm in ("deterministic", "model") if args.model else ("deterministic",):
        print(f"==== arm: {arm}")
        summary = run_arm(arm)
        out = REPORTS / f"executor-workflow-v0_{arm}.json"
        out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(
            f"end_to_end={summary['end_to_end_success_count']}/{summary['task_count']}"
            f" executed={summary['executed_count']}"
            f" verified_completed={summary['verified_completed_count']}"
            f" executed_unverified={summary['executed_unverified_count']}"
            f" unsafe={summary['unsafe_actions']}"
            f" permission_rejections={summary['permission_rejection_count']}"
        )
        print(
            f"model_called={summary['model_called_count']}"
            f" first_proposal_correct={summary['first_proposal_correct_count']}"
            f" valid_output={summary['model_valid_output_count']}"
            f" validated={summary['model_validated_count']}"
            f" model_originated_success={summary['model_originated_success_count']}"
            f" fallback={summary['fallback_count']}"
            f" fallback_assisted_success={summary['fallback_assisted_success_count']}"
        )
        print(f"terminal_paths={summary['terminal_paths']}")
        print(f"total_seconds={summary['total_seconds']}")
        print(f"config_id={summary['frozen_config_id']}")
        for task_id, entry in summary["tasks"].items():
            if not entry["success"]:
                print(
                    f"  FAIL {task_id}: status={entry.get('status')} "
                    f"action={entry.get('proposed_action')}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
