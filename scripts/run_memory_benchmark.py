"""Run the frozen ``memory-retrieval-v0`` benchmark (four arms).

Arms (identical frozen inputs and scoring, all deterministic):

* ``baseline`` — no memory, no skills (the Phase 5 configuration).
* ``memory``   — verified-episode retrieval, conservative adoption.
* ``skills``   — memory + the verified-skill registry (seeded skill is
  revoked and must never fire).
* ``reject``   — memory present, ``allow_memory_adoption=False``.

Writes ``reports/memory-retrieval-v0_results.json``. Never calls a model,
never touches the network, never modifies any frozen Phase 3-5 artifact.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from evals.execution_tasks import execution_tasks  # noqa: E402
from evals.memory_tasks import (  # noqa: E402
    ARM_NAMES,
    BENCHMARK_ID,
    CONFIG_IDENTIFIER,
    FROZEN_AT,
    MEM_TASKS,
    MEMORY_LIMIT,
    PRECISION_CANDIDATES,
    SCORING_VERSION,
    TASK_COUNT,
    build_memory_benchmark_fixture,
    mem_task_score,
    precision_score,
    recall_score,
    retrieval_relevance,
)
from run_executor_benchmark import summarize  # noqa: E402

from arceus_core.controller import READ_ONLY_ACTIONS, CognitiveController  # noqa: E402
from arceus_core.memory import EpisodicMemory  # noqa: E402
from arceus_core.skills import SkillRegistry  # noqa: E402
from arceus_core.workflow import TaskWorkflow  # noqa: E402

REPORTS = Path(__file__).resolve().parents[1] / "reports"
ARTIFACT_PATH = REPORTS / "memory-retrieval-v0_results.json"


def _tally(values: list[str | None]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = value if value is not None else "none"
        counts[key] = counts.get(key, 0) + 1
    return counts


def run_arm(arm: str) -> dict[str, Any]:
    tasks = list(execution_tasks())

    with tempfile.TemporaryDirectory(prefix="arceus-mem-bench-") as tmp_name:
        conn, db_cm, seed_state = build_memory_benchmark_fixture(Path(tmp_name))
        try:
            controller = CognitiveController(conn)
            memory: EpisodicMemory | None = None
            skills: SkillRegistry | None = None
            if arm != "baseline":
                memory = EpisodicMemory(conn, retention_limit=MEMORY_LIMIT)
            if arm == "skills":
                skills = SkillRegistry(conn)

            # Retrieval relevance is measured directly (frozen queries,
            # before any run-time write-back can perturb the store).
            retrieval = retrieval_relevance(memory) if memory is not None else {}

            workflow = TaskWorkflow(
                conn,
                controller,
                task_id_prefix=arm,
                memory=memory,
                skills=skills,
                allow_memory_adoption=arm != "reject",
            )

            results: dict[str, Any] = {}
            started = time.perf_counter()
            for task in tasks:
                results[task.id] = workflow.run(task.controller_input())
            for mem_task in MEM_TASKS:
                results[mem_task.id] = workflow.run(mem_task.controller_input())
            wall_seconds = round(time.perf_counter() - started, 4)

            end_stats = memory.stats() if memory is not None else {}
        finally:
            db_cm.__exit__(None, None, None)

    summary = summarize(tasks, results)  # frozen exec-* scoring (12 tasks)
    summary["arm"] = arm
    summary["task_count"] = TASK_COUNT
    summary["wall_seconds"] = wall_seconds
    summary["unsafe_actions_total"] = sum(
        1 for result in results.values() if result.proposed_action not in READ_ONLY_ACTIONS
    )

    # Held-out abstention/adoption tasks against independent ground truth.
    mem_scores: dict[str, Any] = {}
    mem_success = 0
    for mem_task in MEM_TASKS:
        result = results[mem_task.id]
        ok, failures = mem_task_score(mem_task, result.to_dict())
        mem_scores[mem_task.id] = {
            "success": ok,
            "expected_action": mem_task.expected_action,
            "status": result.status,
            "proposed_action": result.proposed_action,
            "executed": result.executed,
            "verification_status": result.verification_status,
            "reuse_decision": result.reuse_decision,
            "skill_decision": (result.reuse_refs or {}).get("skill_decision"),
            "failures": list(failures),
        }
        mem_success += int(ok)
    summary["held_out"] = {
        "total": len(MEM_TASKS),
        "correct": mem_success,
        "memory_benefit_vs_abstention": mem_success,  # baseline arm cannot adopt
        "tasks": mem_scores,
    }

    # Reuse attribution across every task (exec + held-out).
    summary["reuse_decisions"] = _tally([r.reuse_decision for r in results.values()])
    summary["skill_decisions"] = _tally(
        [
            (r.reuse_refs or {}).get("skill_decision") if r.reuse_decision else None
            for r in results.values()
        ]
    )
    summary["memory_adopted_tasks"] = sorted(
        r.task_id for r in results.values() if r.reuse_decision == "memory_adopted"
    )
    summary["adopted_but_unverified"] = sorted(
        r.task_id
        for r in results.values()
        if r.reuse_decision == "memory_adopted" and r.verification_status != "verified"
    )

    # Frozen retrieval-relevance measurement.
    if memory is not None:
        recall = recall_score(retrieval)
        precision = precision_score(retrieval)
        summary["retrieval"] = {
            "recall": {**recall, "denominator_constant": 12},
            "precision": {**precision, "candidates_constant": PRECISION_CANDIDATES},
            "measured_tasks": len(retrieval),
        }
    else:
        summary["retrieval"] = {"recall": {"retrieved": 0}, "precision": {"returned": 0}}

    summary["seed_state"] = seed_state
    summary["memory_end"] = {
        "episode_count": end_stats.get("episode_count"),
        "occurrence_total": end_stats.get("occurrence_total"),
        "deduplicated_duplicates": end_stats.get("deduplicated_duplicates"),
        "retention_limit": MEMORY_LIMIT,
        # Baseline has no live memory layer: growth is undefined, not zero.
        "storage_growth_records": (
            end_stats.get("episode_count", 0) - seed_state["memory_entries_at_start"]
            if end_stats
            else None
        ),
    }
    return summary


def run_benchmark() -> dict[str, Any]:
    arms: dict[str, Any] = {}
    for arm in ARM_NAMES:
        print(f"==== arm: {arm}", flush=True)
        summary = run_arm(arm)
        arms[arm] = summary
        held = summary["held_out"]
        print(
            f"exec_success={summary['end_to_end_success_count']}/12"
            f" held_out={held['correct']}/{held['total']}"
            f" adopted={len(summary['memory_adopted_tasks'])}"
            f" unsafe={summary['unsafe_actions_total']}"
            f" unverified_adoptions={len(summary['adopted_but_unverified'])}"
            f" seconds={summary['wall_seconds']}",
            flush=True,
        )
        print(f"reuse_decisions={summary['reuse_decisions']}", flush=True)
    return {
        "benchmark_id": BENCHMARK_ID,
        "config_identifier": CONFIG_IDENTIFIER,
        "scoring_version": SCORING_VERSION,
        "frozen_at": FROZEN_AT,
        "memory_limit": MEMORY_LIMIT,
        "arms": arms,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    REPORTS.mkdir(exist_ok=True)
    artifact = run_benchmark()
    ARTIFACT_PATH.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(f"artifact={ARTIFACT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
