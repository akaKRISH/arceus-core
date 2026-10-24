"""Benchmark-integrity tests for the frozen ``bcr-routing-v1`` suite.

These tests do not judge router accuracy (the runner does). They pin the
things that would silently invalidate a result: the frozen identifiers, the
ground-truth table, dev/held-out isolation, measurement determinism, and the
cost/safety denominators. Every test here is offline and model-free.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_bcr_benchmark as runner  # noqa: E402
from evals.bcr_tasks import (  # noqa: E402
    ALL_TASKS,
    ARMS,
    BCR_BENCHMARK_ID,
    BCR_CONFIG,
    BCR_FROZEN_AT,
    DEV_TASKS,
    HELDOUT_TASKS,
    seed_episode_spec,
    skill_contract_for,
    task_by_id,
    tasks_for,
)

#: sha256 of the frozen ground-truth table (ids, task text, fixtures, expected
#: strategy/status/action/arguments, skill and memory flags). Any edit — even a
#: comment-free reordering of expected arguments — changes this digest, which
#: is exactly what the freeze rule forbids.
GROUND_TRUTH_DIGEST = "6e8014a23e95c478264d9134edf7bd418c5e02418e7e36f26bd719a225864a48"

EXPECTED_ARMS: dict[str, dict[str, bool]] = {
    "A_deterministic_only": {"use_memory": False, "use_skills": False, "model_allowed": False},
    "B_memory_first": {"use_memory": True, "use_skills": False, "model_allowed": False},
    "C_skill_first": {"use_memory": False, "use_skills": True, "model_allowed": False},
    "D_model_first": {"use_memory": False, "use_skills": False, "model_allowed": True},
    "E_bcr": {"use_memory": True, "use_skills": True, "model_allowed": True},
    "F_memory_skill_no_model": {"use_memory": True, "use_skills": True, "model_allowed": False},
}


def _ground_truth_payload() -> list[dict[str, object]]:
    return [
        {
            "id": task.id,
            "split": task.split,
            "family": task.family,
            "task": task.task,
            "expected_strategy": task.expected_strategy,
            "expected_status": task.expected_status,
            "expected_action": task.expected_action,
            "expected_arguments": task.expected_arguments,
            "skill_servable": task.skill_servable,
            "skill_id": task.skill_id,
            "memory_seed": task.memory_seed,
            "memory_stale": task.memory_stale,
            "mark_root_missing": task.mark_root_missing,
            "fixture_files": sorted(task.fixture_files),
        }
        for task in ALL_TASKS
    ]


# -- frozen identity ----------------------------------------------------------


def test_frozen_identifiers_are_pinned() -> None:
    assert BCR_BENCHMARK_ID == "bcr-routing-v1"
    assert BCR_CONFIG == "bcr-policy-v1; fixture-v2; scoring=bcr-outcome-v1.1"
    assert BCR_FROZEN_AT == "2026-10-24"


def test_arms_are_exactly_the_six_frozen_policies() -> None:
    assert ARMS == EXPECTED_ARMS


def test_ground_truth_table_is_frozen() -> None:
    blob = json.dumps(_ground_truth_payload(), sort_keys=True, ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()
    assert digest == GROUND_TRUTH_DIGEST, (
        "the frozen bcr-routing-v1 task table changed; a new benchmark version "
        f"is required (new digest {digest})"
    )


def test_every_task_has_a_consistent_ground_truth() -> None:
    for task in ALL_TASKS:
        assert task.expected_strategy in {
            "deterministic",
            "verified_skill",
            "abstain",
            "clarify",
        }, task.id
        if task.expected_strategy == "abstain":
            assert task.expected_status == "abstained", task.id
            assert task.expected_action is None, task.id
        elif task.expected_strategy == "clarify":
            assert task.expected_status == "clarification_required", task.id
            assert task.expected_action is None, task.id
        else:
            assert task.expected_status == "verified", task.id
            assert task.expected_action in {"search_files", "graph_children"}, task.id
        assert task.skill_servable == (task.skill_id is not None), task.id
        assert task.memory_stale in {False, task.memory_seed}, task.id
        if task.memory_stale:
            assert task.expected_strategy == "abstain", task.id


def test_task_ids_are_unique_and_splits_are_complete() -> None:
    ids = [task.id for task in ALL_TASKS]
    assert len(ids) == len(set(ids))
    assert len(DEV_TASKS) == 14
    assert len(HELDOUT_TASKS) == 14
    assert {task.split for task in DEV_TASKS} == {"dev"}
    assert {task.split for task in HELDOUT_TASKS} == {"heldout"}
    assert tasks_for("dev") == DEV_TASKS
    assert tasks_for("heldout") == HELDOUT_TASKS
    with pytest.raises(ValueError):
        tasks_for("test")


# -- dev / held-out isolation -------------------------------------------------


def test_heldout_tasks_and_fixtures_are_not_reused_from_dev() -> None:
    dev_text = {task.task for task in DEV_TASKS}
    heldout_text = {task.task for task in HELDOUT_TASKS}
    assert dev_text.isdisjoint(heldout_text)

    dev_files = {name for task in DEV_TASKS for name in task.fixture_files}
    heldout_files = {name for task in HELDOUT_TASKS for name in task.fixture_files}
    assert dev_files.isdisjoint(heldout_files)

    dev_skills = {task.skill_id for task in DEV_TASKS if task.skill_id}
    heldout_skills = {task.skill_id for task in HELDOUT_TASKS if task.skill_id}
    assert dev_skills.isdisjoint(heldout_skills)


def test_heldout_skill_contract_serves_only_its_own_split() -> None:
    dev_skill_task = next(task for task in DEV_TASKS if task.skill_servable)
    heldout_skill_task = next(task for task in HELDOUT_TASKS if task.skill_servable)
    dev_contract = skill_contract_for(dev_skill_task)
    heldout_contract = skill_contract_for(heldout_skill_task)
    assert dev_contract is not None and heldout_contract is not None
    assert dev_contract["accepted_task_signatures"] != heldout_contract["accepted_task_signatures"]
    assert dev_contract["action_sequence"] != heldout_contract["action_sequence"]
    assert dev_contract["id"] != heldout_contract["id"]


def test_seeded_episodes_differ_between_splits() -> None:
    dev_repeat = task_by_id("dev-05-memory-repeat")
    heldout_repeat = task_by_id("ho-05-memory-repeat")
    dev_seed = seed_episode_spec(dev_repeat, root_id=1)
    heldout_seed = seed_episode_spec(heldout_repeat, root_id=1)
    assert dev_seed is not None and heldout_seed is not None
    assert dev_seed["evidence"] != heldout_seed["evidence"]

    stale = seed_episode_spec(task_by_id("dev-06-memory-stale"), root_id=1)
    assert stale is not None
    # The stale episode's evidence names an entry that the fixture never wrote.
    rel_path = stale["evidence"][0]["rel_path"]
    assert rel_path not in dev_repeat.fixture_files
    assert not any(rel_path in task.fixture_files for task in DEV_TASKS)


def test_ground_truth_module_does_not_consult_production_behaviour() -> None:
    source = (ROOT / "evals" / "bcr_tasks.py").read_text(encoding="utf-8")
    for forbidden in (
        "arceus_core.controller",
        "arceus_core.routing",
        "arceus_core.verifier",
        "arceus_core.workflow",
        "arceus_core.executor",
    ):
        assert forbidden not in source, f"ground truth must not import {forbidden}"


def test_production_modules_do_not_import_benchmarks() -> None:
    for name in ("routing.py", "workflow.py", "verifier.py", "controller.py", "memory.py"):
        source = (ROOT / "src" / "arceus_core" / name).read_text(encoding="utf-8")
        assert re.search(r"^\s*(from|import)\s+evals\b", source, re.M) is None, name


# -- measurement determinism --------------------------------------------------

_STABLE_FIELDS = (
    "strategy",
    "status",
    "action",
    "executed",
    "verification_status",
    "task_completed",
    "success",
    "failures",
    "false_verified_success",
    "capability_limited",
    "reuse_decision",
    "fallback_used",
    "model_called",
)


@pytest.mark.parametrize(
    "task_id", ["dev-01-exact", "dev-07-skill-eligible", "dev-10-missing-evidence"]
)
def test_runner_measurement_is_deterministic(task_id: str) -> None:
    task = task_by_id(task_id)
    arm = ARMS["F_memory_skill_no_model"]
    first = runner.run_task("F_memory_skill_no_model", arm, task, use_model=False)
    second = runner.run_task("F_memory_skill_no_model", arm, task, use_model=False)
    for field in _STABLE_FIELDS:
        assert first[field] == second[field], field


def test_runner_records_measured_wall_time_and_budget() -> None:
    task = task_by_id("dev-01-exact")
    entry = runner.run_task(
        "A_deterministic_only", ARMS["A_deterministic_only"], task, use_model=False
    )
    assert entry["wall_seconds"] > 0
    assert entry["wall_over_budget"] is False
    assert runner.BUDGET.max_execution_actions == 1  # structural: one action/task


# -- cost and safety denominators ---------------------------------------------


def _entry(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "task_id": "dev-01-exact",
        "arm": "A_deterministic_only",
        "split": "dev",
        "family": "exact_lookup",
        "expected_strategy": "deterministic",
        "strategy": "deterministic",
        "strategy_ok": True,
        "status": "verified",
        "expected_status": "verified",
        "action": "search_files",
        "executed": True,
        "verification_status": "verified",
        "task_completed": True,
        "verification_reason": "ok",
        "model_called": False,
        "fallback_used": False,
        "fallback_reason": None,
        "reuse_decision": None,
        "reuse_refs": {},
        "routing_reason": "deterministic",
        "model_eligible": False,
        "terminal": False,
        "wall_seconds": 0.1,
        "wall_over_budget": False,
        "success": True,
        "failures": [],
        "false_verified_success": False,
        "incorrect_adoption": False,
        "capability_limited": None,
    }
    base.update(overrides)
    return base


def test_summary_denominators_are_the_task_count() -> None:
    entries = [
        _entry(),
        _entry(task_id="dev-02-partial", success=False, failures=["x"]),
        _entry(task_id="dev-03-ambiguous", model_called=True),
    ]
    summary = runner.summarize("A_deterministic_only", "dev", entries)
    assert summary["task_count"] == 3
    assert summary["successes"] == 2
    assert summary["success_rate"] == round(2 / 3, 4)
    assert summary["model_calls"] == 1
    assert summary["model_call_rate"] == round(1 / 3, 4)
    assert summary["tool_calls"] == 3
    assert summary["budget_overruns"] == 0
    assert summary["router_mislabeled"] == 1


def test_cost_budget_overrun_is_counted_not_trimmed() -> None:
    entries = [_entry(wall_seconds=runner.BUDGET.max_workflow_seconds + 1, wall_over_budget=True)]
    summary = runner.summarize("A_deterministic_only", "dev", entries)
    assert summary["budget_overruns"] == 1
    assert summary["latency_median"] == entries[0]["wall_seconds"]


def test_capability_limited_failures_are_not_router_mislabels() -> None:
    task = task_by_id("dev-07-skill-eligible")
    entry = _entry(
        task_id=task.id,
        expected_strategy="verified_skill",
        strategy="abstain",
        strategy_ok=False,
        status="abstained",
        action="abstain",
        executed=False,
        verification_status="unverified",
        task_completed=False,
        success=False,
        failures=["strategy"],
        capability_limited="skills_disabled",
    )
    summary = runner.summarize("A_deterministic_only", "dev", [entry])
    assert summary["capability_limited"] == 1
    assert summary["router_mislabeled"] == 0


def test_correct_abstention_is_not_a_false_verified_success() -> None:
    task = task_by_id("dev-10-missing-evidence")
    view = SimpleNamespace(status="abstained", task_completed=True, proposed_action="abstain")
    assert runner._false_verified_success(task, view) is False


def test_empty_verified_search_is_not_a_false_success() -> None:
    task = task_by_id("dev-10-missing-evidence")
    view = SimpleNamespace(status="verified", task_completed=None, proposed_action="search_files")
    assert runner._false_verified_success(task, view) is False


def test_verified_result_on_an_abstain_task_is_a_false_success() -> None:
    task = task_by_id("dev-10-missing-evidence")
    view = SimpleNamespace(status="verified", task_completed=True, proposed_action="search_files")
    assert runner._false_verified_success(task, view) is True


def test_correctly_executed_deterministic_task_is_not_a_false_success() -> None:
    task = task_by_id("dev-01-exact")
    view = SimpleNamespace(status="verified", task_completed=True, proposed_action="search_files")
    assert runner._false_verified_success(task, view) is False
