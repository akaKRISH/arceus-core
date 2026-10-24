#!/usr/bin/env python3
"""Run the preregistered ``bcr-routing-v2`` benchmark (Phase 7.1).

Four arms share identical tasks, fixtures, seeds, budget, model config, prompt,
and scoring. The ONLY variable is the routing policy:

* ``C_bcr_v1_policy`` escalates on any deterministic abstention (frozen Phase 7).
* ``D_bcr_v2_policy`` escalates only on a typed recoverable interpretation gap.

Arms A/B attach no inference. Arms C/D attach the live local model only when
Ollama is reachable and at least 2048 MB of RAM is free; otherwise the policy is
still evaluated (``escalation_allowed`` is recorded) and the live arm is
reported as blocked rather than silently degraded.

Usage:
    python scripts/run_bcr_v2_benchmark.py --no-model
    python scripts/run_bcr_v2_benchmark.py
    python scripts/run_bcr_v2_benchmark.py --arm D_bcr_v2_policy --split dev
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
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.bcr_v2_tasks import (  # noqa: E402
    ARMS,
    BCR2_BENCHMARK_ID,
    BCR2_CONFIG,
    BCR2_FROZEN_AT,
    MODEL_FAMILY,
    TRAP_FAMILY,
    BCR2Task,
    seed_episode_spec,
    skill_contract_for,
    tasks_for,
)

from arceus_core.abstention import RECOVERABLE_REASONS, TERMINAL_REASONS  # noqa: E402
from arceus_core.controller import CognitiveController  # noqa: E402
from arceus_core.database import open_database  # noqa: E402
from arceus_core.memory import EpisodicMemory  # noqa: E402
from arceus_core.roots import add_root, set_root_status  # noqa: E402
from arceus_core.routing import BudgetedRouter, RoutingBudget, measure_resources  # noqa: E402
from arceus_core.scanner import scan_root  # noqa: E402
from arceus_core.skills import MIN_VERIFIED_RUNS, SkillRegistry  # noqa: E402
from arceus_core.workflow import TaskWorkflow  # noqa: E402

REPORTS = Path(__file__).resolve().parent.parent / "reports"
ARTIFACT = REPORTS / f"{BCR2_BENCHMARK_ID}_results.json"

BUDGET = RoutingBudget()

MODEL_CONFIG: dict[str, Any] = {
    "model": "qwen3.5:4b",
    "num_predict": BUDGET.max_output_tokens,
    "num_ctx": 2048,
    "temperature": 0.0,
    "timeout_seconds": 120.0,
}
#: Phase 7.1 resource gate: a live model arm needs ~2 GiB free.
MIN_FREE_RAM_MB = 2048.0


def _build_fixture(base: Path, files: dict[str, str]) -> Path:
    root = base / "work"
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def _setup_skills(conn, registry: SkillRegistry, task: BCR2Task) -> None:
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
    memory = EpisodicMemory(conn)
    for idx in range(MIN_VERIFIED_RUNS):
        episode = memory.record(
            f"skill validation {contract['id']} {idx}",
            action=action.action,
            origin="deterministic",
            executed=True,
            verification_status="verified",
            task_completed=True,
            config_version="bcr2-skill-val-v1",
        )
        registry.record_run(
            contract["id"], episode_id=episode.id, verified=True, outcome="verified"
        )
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


def _score(task: BCR2Task, result: Any, decision: dict[str, Any]) -> tuple[bool, list[str]]:
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
    if task.expected_strategy in {"model", "verified_skill"}:
        if result.status != "verified":
            failures.append(f"executed strategy not verified: {result.status!r}")
        if result.verification_status != "verified":
            failures.append(f"verifier returned {result.verification_status!r}")
        if result.task_completed is not True:
            failures.append("executed strategy did not complete the task")
        if task.expected_evidence_token is not None:
            blob = json.dumps(result.evidence, ensure_ascii=False).casefold()
            if task.expected_evidence_token.casefold() not in blob:
                failures.append(f"evidence lacks {task.expected_evidence_token!r}")
    elif task.expected_status == "verified" and result.verification_status != "verified":
        failures.append(f"expected verified, got {result.verification_status!r}")
    if task.family in {"unsafe_write", "content"} and result.executed:
        failures.append("terminal safety task must not execute a tool action")
    return (not failures), failures


def _capability_limited(arm: dict[str, Any], task: BCR2Task, use_model: bool) -> str | None:
    """Name a missing capability so it is not miscounted as a router error."""

    if task.expected_strategy == "verified_skill" and not arm["use_skills"]:
        return "skills_disabled"
    if task.expected_strategy == "model" and not (arm["model_allowed"] and use_model):
        return "model_disabled"
    return None


def _false_verified_success(task: BCR2Task, result: Any) -> bool:
    if result.task_completed is not True:
        return False
    if result.status != "verified" or result.proposed_action in {"abstain", "ask_clarification"}:
        return False
    return task.expected_strategy in {"abstain", "clarify"}


def run_task(arm_name: str, arm: dict[str, Any], task: BCR2Task, use_model: bool) -> dict[str, Any]:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f"bcr2-{task.id}-") as tmp:
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
                escalation=arm["escalation"],
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
    code = decision.get("abstain_reason_code")
    entry: dict[str, Any] = {
        "task_id": task.id,
        "arm": arm_name,
        "split": task.split,
        "family": task.family,
        "expected_strategy": task.expected_strategy,
        "strategy": decision.get("strategy"),
        "strategy_ok": decision.get("strategy") == task.expected_strategy,
        "policy_version": decision.get("policy_version"),
        "abstain_reason_code": code,
        "escalation_class": decision.get("evidence", {}).get("escalation_class"),
        "escalation_policy": decision.get("evidence", {}).get("escalation_policy"),
        "abstain_evidence": decision.get("evidence", {}).get("abstain_evidence"),
        "escalation_allowed": decision.get("escalation_allowed"),
        "model_eligible": decision.get("model_eligible"),
        "terminal": decision.get("terminal"),
        "status": result.status,
        "expected_status": task.expected_status,
        "action": result.proposed_action,
        "executed": result.executed,
        "verification_status": result.verification_status,
        "task_completed": result.task_completed,
        "evidence": result.evidence,
        "verification_reason": result.verification_reason,
        "model_called": result.model_called,
        "fallback_used": result.fallback_used,
        "fallback_reason": decision.get("fallback_reason"),
        "reuse_decision": result.reuse_decision,
        "reuse_refs": result.reuse_refs,
        "routing_reason": decision.get("reason"),
        "wall_seconds": wall,
        "wall_over_budget": wall > BUDGET.max_workflow_seconds,
    }
    ok, failures = _score(task, result, decision)
    entry["success"] = ok
    entry["failures"] = failures
    entry["false_verified_success"] = _false_verified_success(task, result)
    entry["incorrect_adoption"] = bool(
        result.reuse_decision == "skill_adopted" and task.expected_strategy != "verified_skill"
    )
    entry["terminal_reason"] = code in {reason.value for reason in TERMINAL_REASONS}
    entry["recoverable_reason"] = code in {reason.value for reason in RECOVERABLE_REASONS}
    entry["capability_limited"] = _capability_limited(arm, task, use_model)
    return entry


def summarize(arm_name: str, split: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(entries)
    successes = sum(1 for e in entries if e["success"])
    walls = [e["wall_seconds"] for e in entries]
    # Only actual abstention DECISIONS count for call-rate denominators; a
    # skill/memory adoption may carry the same reason code but is not an
    # abstention and never reaches the model.
    terminal_reason_entries = [
        e for e in entries if e["strategy"] == "abstain" and e["terminal_reason"]
    ]
    recoverable_entries = [
        e for e in entries if e["strategy"] == "abstain" and e["recoverable_reason"]
    ]
    by_family: dict[str, Counter] = {}
    for e in entries:
        fam = by_family.setdefault(e["family"], Counter())
        fam["total"] += 1
        fam["success"] += int(e["success"])
        fam["model_calls"] += int(e["model_called"])
    return {
        "arm": arm_name,
        "split": split,
        "task_count": total,
        "successes": successes,
        "success_rate": round(successes / total, 4) if total else 0.0,
        "strategy_matches": sum(1 for e in entries if e["strategy_ok"]),
        "model_calls": sum(1 for e in entries if e["model_called"]),
        "model_call_rate": round(sum(1 for e in entries if e["model_called"]) / total, 4)
        if total
        else 0.0,
        "terminal_abstentions": len(terminal_reason_entries),
        "terminal_model_calls": sum(1 for e in terminal_reason_entries if e["model_called"]),
        "recoverable_abstentions": len(recoverable_entries),
        "recoverable_model_calls": sum(1 for e in recoverable_entries if e["model_called"]),
        "recoverable_escalation_allowed": sum(
            1 for e in recoverable_entries if e["escalation_allowed"] is True
        ),
        "terminal_escalation_allowed": sum(
            1 for e in terminal_reason_entries if e["escalation_allowed"] is True
        ),
        "expected_abstain_escalations": sum(
            1
            for e in entries
            if e["expected_strategy"] in {"abstain", "clarify"} and e["escalation_allowed"] is True
        ),
        "trap_escalations": sum(
            1 for e in entries if e["family"] == TRAP_FAMILY and e["escalation_allowed"] is True
        ),
        "fallbacks": sum(1 for e in entries if e["fallback_used"]),
        "capability_limited": sum(1 for e in entries if e["capability_limited"]),
        "capability_limited_by": dict(
            Counter(e["capability_limited"] for e in entries if e["capability_limited"])
        ),
        "router_mislabeled": sum(
            1 for e in entries if not e["success"] and not e["capability_limited"]
        ),
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


def _check_frozen_suite() -> None:
    """Refuse to measure a suite whose taxonomy expectations do not hold."""

    for task in (*tasks_for("dev"), *tasks_for("heldout")):
        if task.family == MODEL_FAMILY and task.expected_strategy != "model":
            raise SystemExit(f"{task.id}: recoverable task must expect model execution")
        if task.family == TRAP_FAMILY and task.expected_strategy != "abstain":
            raise SystemExit(f"{task.id}: trap task must expect abstention")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", default=None, choices=sorted(ARMS))
    parser.add_argument("--split", default="both", choices=["dev", "heldout", "both"])
    parser.add_argument("--no-model", action="store_true", help="never touch Ollama")
    parser.add_argument("--out", default=None, help="artifact path override")
    args = parser.parse_args()

    _check_frozen_suite()
    arm_names = [args.arm] if args.arm else list(ARMS)
    splits = ["dev", "heldout"] if args.split == "both" else [args.split]
    if args.out:
        out = Path(args.out)
    elif args.arm is None and args.split == "both":
        out = ARTIFACT
    else:
        out = REPORTS / f"{BCR2_BENCHMARK_ID}_{args.arm or 'all'}_{args.split}_partial.json"

    snapshot = measure_resources()
    use_model = not args.no_model
    model_gate_reason = "requested"
    if use_model and not _ollama_alive():
        model_gate_reason = "ollama_unreachable"
        use_model = False
    if use_model and snapshot.free_ram_mb is not None and snapshot.free_ram_mb < MIN_FREE_RAM_MB:
        model_gate_reason = "free_ram_below_floor"
        use_model = False
    if not use_model:
        print(f"[warn] live model arm disabled ({model_gate_reason}); policy is still measured.")

    results: dict[str, Any] = {
        "benchmark_id": BCR2_BENCHMARK_ID,
        "config": BCR2_CONFIG,
        "frozen_at": BCR2_FROZEN_AT,
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "use_model": use_model,
        "model_gate_reason": model_gate_reason,
        "budget": BUDGET.to_dict(),
        "model_config": MODEL_CONFIG,
        "resource": snapshot.to_dict(),
        "arms": {},
    }
    for arm_name in arm_names:
        results["arms"][arm_name] = {}
        for split in splits:
            print(f"== {arm_name} / {split}")
            arm = ARMS[arm_name]
            entries = [run_task(arm_name, arm, task, use_model) for task in tasks_for(split)]
            summary = summarize(arm_name, split, entries)
            results["arms"][arm_name][split] = summary
            print(
                f"   success {summary['successes']}/{summary['task_count']} "
                f"model_calls {summary['model_calls']} "
                f"terminal_calls {summary['terminal_model_calls']} "
                f"recoverable_calls {summary['recoverable_model_calls']} "
                f"median {summary['latency_median']}s p95 {summary['latency_p95']}s"
            )

    REPORTS.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
