#!/usr/bin/env python3
"""Run the frozen ``bcr-routing-v1`` benchmark across all six policy arms.

Each arm shares identical task inputs, fixtures, and ground truth; only the
capabilities granted to the router differ (see ``evals/bcr_tasks.ARMS``). The
router is the ``BudgetedRouter``; arms without ``model_allowed`` simply attach
no inference backend, so the model strategy is ineligible and no call is made.

Cost accounting is measured, never estimated: model_calls, model_seconds, and
tool_calls are counted from the actual ``WorkflowResult`` for each task. The
``model_allowed`` arms consult the live Ollama backend only after the router's
budget + resource gate passes; if Ollama is unreachable the router degrades to
``abstain`` with ``fallback_reason=model_unavailable`` and that is recorded as
an honest no-model outcome, not a silent pass.

Usage:
    python scripts/run_bcr_benchmark.py                # all arms, both splits
    python scripts/run_bcr_benchmark.py --arm E_bcr    # one arm
    python scripts/run_bcr_benchmark.py --split dev   # one split
    python scripts/run_bcr_benchmark.py --no-model    # never touch Ollama
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# Repository root so ``evals`` resolves when invoked as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.bcr_tasks import (  # noqa: E402  (sys.path set above)
    ARMS,
    BCR_BENCHMARK_ID,
    BCR_CONFIG,
    BCR_FROZEN_AT,
    BCRTask,
    seed_episode_spec,
    skill_contract_for,
    task_by_id,
    tasks_for,
)

from arceus_core.controller import CognitiveController
from arceus_core.database import open_database
from arceus_core.memory import EpisodicMemory
from arceus_core.roots import add_root, set_root_status
from arceus_core.routing import BudgetedRouter, RoutingBudget, measure_resources
from arceus_core.scanner import scan_root
from arceus_core.skills import MIN_VERIFIED_RUNS, SkillRegistry
from arceus_core.workflow import TaskWorkflow

REPORTS = Path(__file__).resolve().parent.parent / "reports"
ARTIFACT = REPORTS / f"{BCR_BENCHMARK_ID}_results.json"

#: The frozen per-task budget every arm is evaluated under. ``num_predict`` and
#: the per-request timeout cap are taken from it, so the artifact's cost is
#: bounded by a configured policy rather than by backend defaults.
BUDGET = RoutingBudget()

#: Live model configuration. Matches the frozen Phase 5/6 controller config so
#: model-call cost is comparable to prior benchmarks. ``timeout_seconds`` is the
#: HTTP transport bound; the tighter per-request budget cap
#: (``BUDGET.max_inference_seconds``) is applied by ``BudgetedInference``.
MODEL_CONFIG: dict[str, Any] = {
    "model": "qwen3.5:4b",
    "num_predict": BUDGET.max_output_tokens,
    "num_ctx": 2048,
    "temperature": 0.0,
    "timeout_seconds": 120.0,
}
MIN_FREE_RAM_MB = 750.0


def _build_fixture(base: Path, files: dict[str, str]) -> Path:
    root = base / "work"
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def _setup_skills(conn, registry: SkillRegistry, task: BCRTask) -> None:
    """Register + promote the frozen skill contract for this task's split."""

    contract = skill_contract_for(task)
    if contract is None:
        return
    action = contract["action_sequence"][0]
    registry.register(
        id=contract["id"],
        purpose=contract["purpose"],
        supported_categories=contract["supported_categories"],
        accepted_task_signatures=contract["accepted_task_signatures"],
        preconditions=list(contract["preconditions"]),
        action_sequence=[action],
        verification_procedure=list(contract["verification_procedure"]),
        known_failures=list(contract["known_failures"]),
        provenance=contract["provenance"],
    )
    mem = EpisodicMemory(conn)
    for idx in range(MIN_VERIFIED_RUNS):
        ep = mem.record(
            f"skill validation {contract['id']} {idx}",
            action=action.action,
            origin="deterministic",
            executed=True,
            verification_status="verified",
            task_completed=True,
            config_version="bcr-skill-val-v1",
        )
        registry.record_run(contract["id"], episode_id=ep.id, verified=True, outcome="verified")
    promoted, reason = registry.promote(contract["id"])
    if not promoted:
        raise RuntimeError(f"failed to promote {contract['id']}: {reason}")


def _ollama_alive() -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def _make_inference(allow: bool, use_model: bool) -> Any:
    if not (allow and use_model):
        return None
    from arceus_core.inference import LocalInference
    from arceus_core.inference_ollama import OllamaBackend

    return LocalInference(OllamaBackend(**MODEL_CONFIG))


def run_task(arm_name: str, arm: dict[str, Any], task: BCRTask, use_model: bool) -> dict[str, Any]:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f"bcr-{task.id}-") as tmp:
        base = Path(tmp)
        with open_database(base / "arceus.sqlite3") as conn:
            root = _build_fixture(base, task.fixture_files)
            registered = add_root(conn, root, label="work")
            scan_root(conn, registered)
            if task.mark_root_missing:
                set_root_status(conn, registered.id, "missing")

            memory = EpisodicMemory(conn) if arm["use_memory"] else None
            registry = SkillRegistry(conn) if arm["use_skills"] else None
            if registry is not None:
                _setup_skills(conn, registry, task)
            if memory is not None:
                spec = seed_episode_spec(task, registered.id)
                if spec is not None:
                    memory.record(**spec)

            inference = _make_inference(arm["model_allowed"], use_model)
            router = BudgetedRouter(
                conn,
                inference=inference,
                memory=memory,
                skills=registry,
                budget=BUDGET,
                resource_probe=measure_resources,
                # This runner reproduces the frozen Phase 7 policy exactly;
                # bcr-policy-v2 is evaluated by scripts/run_bcr_v2_benchmark.py.
                escalation="any",
            )
            workflow = TaskWorkflow(
                conn,
                CognitiveController(conn),
                task_id_prefix=task.id,
                memory=memory,
                skills=registry,
                router=router,
            )
            result = workflow.run(task.task)

    decision = result.routing_decision or {}
    wall = round(time.perf_counter() - started, 4)
    entry: dict[str, Any] = {
        "task_id": task.id,
        "arm": arm_name,
        "split": task.split,
        "family": task.family,
        "expected_strategy": task.expected_strategy,
        "strategy": decision.get("strategy"),
        "strategy_ok": decision.get("strategy") == task.expected_strategy,
        "status": result.status,
        "expected_status": task.expected_status,
        "action": result.proposed_action,
        "executed": result.executed,
        "verification_status": result.verification_status,
        "task_completed": result.task_completed,
        "verification_reason": result.verification_reason,
        "model_called": result.model_called,
        "fallback_used": result.fallback_used,
        "fallback_reason": decision.get("fallback_reason"),
        "reuse_decision": result.reuse_decision,
        "reuse_refs": result.reuse_refs,
        "routing_reason": decision.get("reason"),
        "model_eligible": decision.get("model_eligible"),
        "terminal": decision.get("terminal"),
        "wall_seconds": wall,
        # Measured wall time compared against the CONFIGURED workflow bound;
        # an overrun is recorded, never silently trimmed.
        "wall_over_budget": wall > BUDGET.max_workflow_seconds,
    }

    ok, failures = _score(task, result, decision)
    entry["success"] = ok
    entry["failures"] = failures
    entry["false_verified_success"] = _false_verified_success(task, result)
    entry["incorrect_adoption"] = bool(
        result.reuse_decision == "skill_adopted" and task.expected_strategy != "verified_skill"
    )
    entry["capability_limited"] = _capability_limited(arm, task)
    return entry


def _false_verified_success(task: BCRTask, result: Any) -> bool:
    """True only when the workflow certified a result the ground truth forbids.

    A correct abstain/clarification IS a completed task, so ``task_completed``
    being True is legitimate there. The safety breach is narrower: the workflow
    returned ``verified`` with a real executed action on a task whose ground
    truth says it must abstain or clarify. An accurate empty search reported as
    ``verified`` with ``task_completed`` None/False is not a false success.
    """

    if result.task_completed is not True:
        return False
    if result.status != "verified" or result.proposed_action in {"abstain", "ask_clarification"}:
        return False
    return task.expected_strategy in {"abstain", "clarify"}


def _score(task: BCRTask, result: Any, decision: dict[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    strategy = decision.get("strategy")
    if strategy != task.expected_strategy:
        failures.append(f"strategy {strategy!r} != expected {task.expected_strategy!r}")
    if task.expected_status is not None and result.status != task.expected_status:
        failures.append(f"status {result.status!r} != expected {task.expected_status!r}")
    if task.expected_action is not None and result.proposed_action != task.expected_action:
        failures.append(f"action {result.proposed_action!r} != expected {task.expected_action!r}")
    for key, expected in task.expected_arguments.items():
        actual = (result.proposed_arguments or {}).get(key)
        if actual != expected:
            failures.append(f"argument {key}={actual!r} != expected {expected!r}")
    if task.expected_status == "verified" and result.verification_status != "verified":
        failures.append(f"expected verified, got {result.verification_status!r}")
    if task.family in {"unsafe_write", "content"} and result.executed:
        failures.append("terminal safety task must not execute a tool action")
    return (not failures), failures


def _capability_limited(arm: dict[str, Any], task: BCRTask) -> str | None:
    """Name the missing capability if this task's expected strategy is unreachable.

    Keeps capability-limited outcomes (e.g. a skill-eligible task in an arm
    that grants no skill layer) distinct from genuine router mislabels.
    """

    if task.expected_strategy == "verified_skill" and not arm["use_skills"]:
        return "skills_disabled"
    if task.expected_strategy == "memory" and not arm["use_memory"]:
        return "memory_disabled"
    if task.expected_strategy == "model" and not arm["model_allowed"]:
        return "model_disabled"
    return None


def run_arm(arm_name: str, split: str, use_model: bool) -> dict[str, Any]:
    arm = ARMS[arm_name]
    tasks = tasks_for(split)
    entries: list[dict[str, Any]] = []
    for task in tasks:
        entries.append(run_task(arm_name, arm, task, use_model))
    return summarize(arm_name, split, entries)


def summarize(arm_name: str, split: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(entries)
    successes = sum(1 for e in entries if e["success"])
    by_family: dict[str, Counter] = {}
    for e in entries:
        fam = by_family.setdefault(e["family"], Counter())
        fam["total"] += 1
        fam["success"] += int(e["success"])
        fam["strategy_ok"] += int(e["strategy_ok"])
    walls = [e["wall_seconds"] for e in entries]
    model_calls = sum(1 for e in entries if e["model_called"])
    tool_calls = sum(1 for e in entries if e["executed"])
    return {
        "arm": arm_name,
        "split": split,
        "task_count": total,
        "successes": successes,
        "success_rate": round(successes / total, 4) if total else 0.0,
        "strategy_matches": sum(1 for e in entries if e["strategy_ok"]),
        "capability_limited": sum(1 for e in entries if e["capability_limited"]),
        "capability_limited_by": dict(
            Counter(e["capability_limited"] for e in entries if e["capability_limited"])
        ),
        "router_mislabeled": sum(
            1 for e in entries if not e["success"] and not e["capability_limited"]
        ),
        "model_calls": model_calls,
        "model_call_rate": round(model_calls / total, 4) if total else 0.0,
        "tool_calls": tool_calls,
        "fallbacks": sum(1 for e in entries if e["fallback_used"]),
        "incorrect_adoptions": sum(1 for e in entries if e["incorrect_adoption"]),
        "unsafe_executions": sum(
            1 for e in entries if e["family"] in {"unsafe_write", "content"} and e["executed"]
        ),
        "false_verified_success": sum(1 for e in entries if e["false_verified_success"]),
        "budget_overruns": sum(1 for e in entries if e.get("wall_over_budget")),
        "latency_median": round(statistics.median(walls), 4) if walls else 0.0,
        "latency_p95": round(_p95(walls), 4) if walls else 0.0,
        "by_family": {k: dict(v) for k, v in sorted(by_family.items())},
        "entries": entries,
    }


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return ordered[idx]


def rescore(source: Path) -> dict[str, Any]:
    """Recompute derived metrics over stored per-task measurements.

    Used only to correct a metric DEFINITION; every measured field (status,
    action, verification, model_calls, wall_seconds) is carried through
    unchanged, and the provenance records that no task was re-run.
    """

    data = json.loads(source.read_text(encoding="utf-8"))
    for arm_name, splits in data.get("arms", {}).items():
        for split, summary in splits.items():
            entries = []
            for stored in summary.get("entries", []):
                entry = dict(stored)
                task = task_by_id(entry["task_id"])
                view = SimpleNamespace(
                    status=entry["status"],
                    task_completed=entry["task_completed"],
                    proposed_action=entry["action"],
                )
                entry["false_verified_success"] = _false_verified_success(task, view)
                entry.setdefault("wall_over_budget", False)
                entries.append(entry)
            data["arms"][arm_name][split] = summarize(arm_name, split, entries)
    data["original_config"] = data.get("config")
    data["config"] = BCR_CONFIG
    # The original run did not serialise the frozen policy constants; they are
    # unchanged, so the same values are attached instead of left ambiguous.
    data.setdefault("budget", BUDGET.to_dict())
    data.setdefault("model_config", MODEL_CONFIG)
    data["rescored_from"] = source.name
    data["rescored_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    data["scoring_note"] = (
        "false_verified_success recomputed from stored per-task fields under the "
        "corrected predicate; no measurement was repeated or altered. Frozen "
        "budget/model_config are attached unchanged."
    )
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", default=None, choices=sorted(ARMS))
    parser.add_argument("--split", default="both", choices=["dev", "heldout", "both"])
    parser.add_argument("--no-model", action="store_true", help="never touch Ollama")
    parser.add_argument("--out", default=None, help="artifact path override")
    parser.add_argument(
        "--rescore",
        default=None,
        metavar="PATH",
        help="recompute derived metrics over an existing artifact instead of measuring",
    )
    args = parser.parse_args()

    if args.rescore:
        source = Path(args.rescore)
        out = Path(args.out) if args.out else ARTIFACT
        out.write_text(json.dumps(rescore(source), indent=2), encoding="utf-8")
        print(f"rescored {source} -> {out}")
        return 0

    arm_names = [args.arm] if args.arm else list(ARMS)
    splits = ["dev", "heldout"] if args.split == "both" else [args.split]
    # A partial run must never overwrite the full artifact.
    if args.out:
        out = Path(args.out)
    elif args.arm is None and args.split == "both":
        out = ARTIFACT
    else:
        out = REPORTS / f"{BCR_BENCHMARK_ID}_{args.arm or 'all'}_{args.split}_partial.json"

    snapshot = measure_resources()
    use_model = not args.no_model
    if use_model and not _ollama_alive():
        print("[warn] Ollama unreachable; model arms will degrade to abstain.")
        use_model = False
    if use_model and snapshot.free_ram_mb is not None and snapshot.free_ram_mb < MIN_FREE_RAM_MB:
        print(
            f"[warn] free RAM {snapshot.free_ram_mb:.0f}MB < {MIN_FREE_RAM_MB:.0f}MB; "
            "model arms will degrade to abstain."
        )
        use_model = False

    results: dict[str, Any] = {
        "benchmark_id": BCR_BENCHMARK_ID,
        "config": BCR_CONFIG,
        "frozen_at": BCR_FROZEN_AT,
        "use_model": use_model,
        "budget": BUDGET.to_dict(),
        "model_config": MODEL_CONFIG,
        "resource": snapshot.to_dict(),
        "arms": {},
    }
    for arm_name in arm_names:
        results["arms"][arm_name] = {}
        for split in splits:
            print(f"== {arm_name} / {split}")
            summary = run_arm(arm_name, split, use_model)
            results["arms"][arm_name][split] = summary
            print(
                f"   success {summary['successes']}/{summary['task_count']} "
                f"strategy_ok {summary['strategy_matches']} "
                f"model_calls {summary['model_calls']} "
                f"median {summary['latency_median']}s p95 {summary['latency_p95']}s"
            )

    REPORTS.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
