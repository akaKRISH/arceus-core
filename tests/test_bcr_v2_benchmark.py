"""Integrity tests for the preregistered ``bcr-routing-v2`` suite.

These tests pin the frozen identifiers and ground-truth digest, prove the
dev/held-out isolation, verify the taxonomy expectations on the real fixture
(model-free), and check the cost/safety denominators. They never load a model.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_bcr_v2_benchmark as runner  # noqa: E402
from evals.bcr_v2_tasks import (  # noqa: E402
    ALL_TASKS,
    ARMS,
    BCR2_BENCHMARK_ID,
    BCR2_CONFIG,
    BCR2_FROZEN_AT,
    DEV_TASKS,
    HELDOUT_TASKS,
    MODEL_FAMILY,
    TRAP_FAMILY,
    seed_episode_spec,
    skill_contract_for,
    task_by_id,
    tasks_for,
)

GROUND_TRUTH_DIGEST = "e7c4924ab4b1ab7cacbe7536346e284dbcfaca5ef8f24e40d4f00ef26ce2fc45"
#: Terminal reasons the controller classifies before the routing branch, so even
#: the frozen v1 policy never reaches the model for them.
_EARLY_TERMINALS = {"content_unavailable", "unsafe_request", "invalid_task"}
PREREGISTRATION = ROOT / "reports" / "bcr-routing-v2_preregistration.md"


def _payload() -> list[dict[str, object]]:
    return [
        {
            "id": t.id,
            "split": t.split,
            "family": t.family,
            "task": t.task,
            "expected_strategy": t.expected_strategy,
            "expected_status": t.expected_status,
            "expected_action": t.expected_action,
            "expected_arguments": t.expected_arguments,
            "expected_evidence_token": t.expected_evidence_token,
            "skill_servable": t.skill_servable,
            "skill_id": t.skill_id,
            "memory_seed": t.memory_seed,
            "memory_stale": t.memory_stale,
            "mark_root_missing": t.mark_root_missing,
            "fixture_files": sorted(t.fixture_files),
        }
        for t in ALL_TASKS
    ]


# -- frozen identity ----------------------------------------------------------


def test_frozen_identifiers_and_digest() -> None:
    assert BCR2_BENCHMARK_ID == "bcr-routing-v2"
    assert BCR2_CONFIG == "bcr-policy-v2; fixture-v3; scoring=bcr-outcome-v1.1"
    assert BCR2_FROZEN_AT == "2026-10-24"
    blob = json.dumps(_payload(), sort_keys=True, ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()
    assert digest == GROUND_TRUTH_DIGEST, (
        f"the frozen v2 task table changed; the preregistration is invalid (new digest {digest})"
    )


def test_preregistration_exists_before_the_run_and_names_the_digest() -> None:
    assert PREREGISTRATION.is_file()
    text = PREREGISTRATION.read_text(encoding="utf-8")
    assert GROUND_TRUTH_DIGEST in text
    assert "before any model-assisted run" in text
    assert BCR2_CONFIG in text


def test_frozen_definition_matches_the_suite() -> None:
    definition = json.loads(
        (ROOT / "reports" / "bcr-routing-v2_frozen_tasks.json").read_text(encoding="utf-8")
    )
    assert definition["benchmark_id"] == BCR2_BENCHMARK_ID
    assert definition["config"] == BCR2_CONFIG
    assert definition["ground_truth_digest"] == GROUND_TRUTH_DIGEST
    assert definition["tasks"] == _payload()


def test_four_arms_differ_only_in_escalation_and_capabilities() -> None:
    assert set(ARMS) == {
        "A_deterministic_only",
        "B_memory_skills_no_model",
        "C_bcr_v1_policy",
        "D_bcr_v2_policy",
    }
    assert ARMS["C_bcr_v1_policy"]["escalation"] == "any"
    assert ARMS["D_bcr_v2_policy"]["escalation"] == "recoverable"
    assert ARMS["C_bcr_v1_policy"]["use_memory"] == ARMS["D_bcr_v2_policy"]["use_memory"]
    assert ARMS["C_bcr_v1_policy"]["use_skills"] == ARMS["D_bcr_v2_policy"]["use_skills"]
    for arm in ARMS.values():
        assert arm["escalation"] in {"any", "recoverable"}


def test_task_table_shape_and_family_ground_truth() -> None:
    assert len(DEV_TASKS) == 16 and len(HELDOUT_TASKS) == 16
    ids = [t.id for t in ALL_TASKS]
    assert len(ids) == len(set(ids))
    for task in ALL_TASKS:
        if task.family == MODEL_FAMILY:
            assert task.expected_strategy == "model", task.id
            assert task.expected_evidence_token, task.id
        elif task.family == TRAP_FAMILY:
            assert task.expected_strategy == "abstain", task.id
            assert task.expected_evidence_token is None, task.id
        elif task.expected_strategy in {"deterministic", "verified_skill"}:
            assert task.expected_status == "verified", task.id
        else:
            assert task.expected_status in {"abstained", "clarification_required"}, task.id


def test_splits_are_isolated() -> None:
    assert {t.task for t in DEV_TASKS}.isdisjoint({t.task for t in HELDOUT_TASKS})
    dev_files = {name for t in DEV_TASKS for name in t.fixture_files}
    held_files = {name for t in HELDOUT_TASKS for name in t.fixture_files}
    assert dev_files.isdisjoint(held_files)
    dev_skills = {t.skill_id for t in DEV_TASKS if t.skill_id}
    held_skills = {t.skill_id for t in HELDOUT_TASKS if t.skill_id}
    assert dev_skills.isdisjoint(held_skills)
    dev_contract = skill_contract_for(task_by_id("v2d-07-skill-eligible"))
    held_contract = skill_contract_for(task_by_id("v2h-07-skill-eligible"))
    assert dev_contract is not None and held_contract is not None
    assert dev_contract["accepted_task_signatures"] != held_contract["accepted_task_signatures"]


def test_stale_episode_evidence_does_not_exist_in_either_fixture() -> None:
    for task_id in ("v2d-06-stale-memory", "v2h-06-stale-memory"):
        task = task_by_id(task_id)
        spec = seed_episode_spec(task, root_id=1)
        assert spec is not None
        rel_path = spec["evidence"][0]["rel_path"]
        assert not any(rel_path in t.fixture_files for t in ALL_TASKS)


def test_ground_truth_module_does_not_consult_production_behaviour() -> None:
    source = (ROOT / "evals" / "bcr_v2_tasks.py").read_text(encoding="utf-8")
    for forbidden in (
        "arceus_core.controller",
        "arceus_core.routing",
        "arceus_core.verifier",
        "arceus_core.workflow",
        "arceus_core.executor",
    ):
        assert forbidden not in source


def test_production_modules_do_not_import_the_v2_suite() -> None:
    for name in ("routing.py", "controller.py", "workflow.py", "abstention.py"):
        source = (ROOT / "src" / "arceus_core" / name).read_text(encoding="utf-8")
        assert re.search(r"^\s*(from|import)\s+evals\b", source, re.M) is None, name


# -- taxonomy expectations on the real fixtures -------------------------------


@pytest.mark.parametrize("split", ["dev", "heldout"])
def test_v2_policy_never_escalates_a_terminal_reason(split: str) -> None:
    arm = ARMS["D_bcr_v2_policy"]
    codes: dict[str, str | None] = {}
    for task in tasks_for(split):
        entry = runner.run_task("D_bcr_v2_policy", arm, task, use_model=False)
        codes[task.id] = entry["abstain_reason_code"]
        if entry["strategy"] != "abstain":
            # Skill/memory adoptions can carry an abstain code from the
            # underlying controller call, but they are not abstentions.
            continue
        if entry["terminal_reason"]:
            assert entry["escalation_allowed"] is False, task.id
        if entry["recoverable_reason"]:
            assert entry["escalation_allowed"] is True, task.id
            assert entry["abstain_reason_code"] == "unmapped_intent", task.id
    # The recovery signal must come from evidence, not from a failed phrase.
    if split == "dev":
        assert codes["v2d-14-recoverable-budget"] == "unmapped_intent"
        assert codes["v2d-16-recoverable-trap"] == "unmapped_intent"
        assert codes["v2d-09-content"] == "content_unavailable"
        assert codes["v2d-13-unsafe"] == "unsafe_request"
    else:
        assert codes["v2h-10-unregistered-root"] in {"no_indexed_path", "path_not_in_scope"}
        assert codes["v2h-14-recoverable-attendance"] == "unmapped_intent"


@pytest.mark.parametrize("split", ["dev", "heldout"])
def test_v1_policy_would_escalate_terminal_reasons(split: str) -> None:
    arm = ARMS["C_bcr_v1_policy"]
    terminal_escalations = 0
    for task in tasks_for(split):
        entry = runner.run_task("C_bcr_v1_policy", arm, task, use_model=False)
        if entry["abstain_reason_code"] in _EARLY_TERMINALS:
            # Classified before the controller branch, so v1 never escalates
            # them either.
            assert entry["escalation_allowed"] is False, task.id
            continue
        if entry["strategy"] == "abstain" and entry["terminal_reason"]:
            # v1 escalates every abstention that reaches the controller branch.
            assert entry["escalation_allowed"] is True, task.id
            terminal_escalations += 1
    # Stale memory, skill-ineligible, missing path, missing evidence.
    assert terminal_escalations == 4


def test_model_free_run_makes_zero_model_calls() -> None:
    arm = ARMS["D_bcr_v2_policy"]
    for task in tasks_for("dev"):
        entry = runner.run_task("D_bcr_v2_policy", arm, task, use_model=False)
        assert entry["model_called"] is False, task.id


# -- determinism and denominators ---------------------------------------------


@pytest.mark.parametrize("task_id", ["v2d-01-exact", "v2d-16-recoverable-trap"])
def test_measurement_is_deterministic(task_id: str) -> None:
    task = task_by_id(task_id)
    arm = ARMS["D_bcr_v2_policy"]
    first = runner.run_task("D_bcr_v2_policy", arm, task, use_model=False)
    second = runner.run_task("D_bcr_v2_policy", arm, task, use_model=False)
    for field in (
        "strategy",
        "status",
        "action",
        "executed",
        "verification_status",
        "task_completed",
        "abstain_reason_code",
        "escalation_allowed",
        "success",
        "failures",
        "false_verified_success",
        "capability_limited",
    ):
        assert first[field] == second[field], field


def _entry(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "task_id": "v2d-01-exact",
        "arm": "D_bcr_v2_policy",
        "split": "dev",
        "family": "exact_lookup",
        "expected_strategy": "deterministic",
        "strategy": "deterministic",
        "strategy_ok": True,
        "policy_version": "bcr-policy-v2",
        "abstain_reason_code": None,
        "escalation_class": None,
        "escalation_policy": None,
        "abstain_evidence": [],
        "escalation_allowed": None,
        "model_eligible": False,
        "terminal": False,
        "status": "verified",
        "expected_status": "verified",
        "action": "search_files",
        "executed": True,
        "verification_status": "verified",
        "task_completed": True,
        "evidence": [],
        "verification_reason": "ok",
        "model_called": False,
        "fallback_used": False,
        "fallback_reason": None,
        "reuse_decision": None,
        "reuse_refs": {},
        "routing_reason": "deterministic",
        "wall_seconds": 0.1,
        "wall_over_budget": False,
        "success": True,
        "failures": [],
        "false_verified_success": False,
        "incorrect_adoption": False,
        "terminal_reason": False,
        "recoverable_reason": False,
        "capability_limited": None,
    }
    base.update(overrides)
    return base


def test_terminal_call_rate_denominator_excludes_adoptions() -> None:
    entries = [
        _entry(
            task_id="t1",
            strategy="abstain",
            action="abstain",
            executed=False,
            verification_status="unverified",
            task_completed=False,
            abstain_reason_code="no_indexed_evidence",
            terminal_reason=True,
            escalation_allowed=False,
            success=False,
            failures=["terminal"],
        ),
        _entry(
            task_id="t2",
            strategy="verified_skill",
            action="search_files",
            reuse_decision="skill_adopted",
            abstain_reason_code="unmapped_intent",
            recoverable_reason=True,
            escalation_allowed=False,
        ),
        _entry(task_id="t3", model_called=True, strategy="model", success=False),
    ]
    summary = runner.summarize("D_bcr_v2_policy", "dev", entries)
    # Only the true abstention counts; the skill adoption is not an abstention.
    assert summary["terminal_abstentions"] == 1
    assert summary["terminal_model_calls"] == 0
    assert summary["recoverable_abstentions"] == 0
    assert summary["model_calls"] == 1
    assert summary["model_call_rate"] == round(1 / 3, 4)


def test_capability_limited_is_not_a_router_mislabel() -> None:
    entry = _entry(
        family="recoverable_gap",
        expected_strategy="model",
        strategy="abstain",
        strategy_ok=False,
        status="abstained",
        action="abstain",
        executed=False,
        verification_status="unverified",
        task_completed=False,
        abstain_reason_code="unmapped_intent",
        recoverable_reason=True,
        escalation_allowed=True,
        success=False,
        failures=["model disabled"],
        capability_limited="model_disabled",
    )
    summary = runner.summarize("D_bcr_v2_policy", "dev", [entry])
    assert summary["capability_limited"] == 1
    assert summary["router_mislabeled"] == 0


def test_budget_overrun_is_counted() -> None:
    entry = _entry(wall_seconds=runner.BUDGET.max_workflow_seconds + 1, wall_over_budget=True)
    summary = runner.summarize("D_bcr_v2_policy", "dev", [entry])
    assert summary["budget_overruns"] == 1
