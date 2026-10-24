"""Tests for the frozen memory-retrieval-v0 benchmark and its runner.

The benchmark is deterministic and fast (no model, no network), so these
tests execute all four arms and pin the frozen expectations: every arm must
keep all 12 exec outcomes and zero unsafe actions; the arms differ only in
held-out reuse behavior (adoption, trust rejections, forced rejection).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import run_memory_benchmark  # noqa: E402
from evals.memory_tasks import (  # noqa: E402
    ARM_NAMES,
    MEM_TASKS,
    MEMORY_RELEVANT_TASK_IDS,
    build_memory_benchmark_fixture,
    mem_task_score,
    precision_score,
    recall_score,
)

from arceus_core.controller import classify_task  # noqa: E402


def test_fixture_seed_state(tmp_path: Path) -> None:
    conn, db_cm, state = build_memory_benchmark_fixture(tmp_path / "bench")
    try:
        # 12 exec episodes + 2 verified poisons + 4 held-out episodes.
        assert state["seeded_episode_count"] == 18
        assert state["memory_entries_at_start"] == 18
        # Tool episodes (8) + poisons (2) + mem-02 + mem-04.
        assert state["memory_authoritative_at_start"] == 12
        assert state["skill_statuses_at_start"] == {"find-logo-file": "revoked"}
        # Promotion evidence: three DISTINCT episodes before revocation.
        assert state["skill_promotion_policy"]["min_verified_runs"] == 3
    finally:
        db_cm.__exit__(None, None, None)


def test_mem_prompts_freeze_the_probed_controller_contract() -> None:
    """The held-out prompts were probed to abstain and classify name_search.

    Freezing that contract guards the benchmark's semantics against silent
    classifier drift (a different category would change which trust rules
    the held-out tasks exercise).
    """

    for mem_task in MEM_TASKS:
        assert classify_task(mem_task.prompt) == "name_search", mem_task.id
    assert len(MEMORY_RELEVANT_TASK_IDS) == 12


def test_recall_and_precision_frozen_definitions() -> None:
    retrieved = {task_id: [("exact_signature", True)] for task_id in MEMORY_RELEVANT_TASK_IDS}
    recall = recall_score(retrieved)
    assert recall == {"relevant_total": 12, "retrieved": 12, "rate": 1.0}
    precision = precision_score(retrieved)
    assert precision == {"returned": 12, "relevant": 12, "rate": 1.0}
    # Wrong-argument noise counts against precision, never for recall.
    noisy = dict(retrieved)
    noisy["exec-01-search-file"] = [
        ("exact_signature", True),
        ("exact_signature", False),
    ]
    assert precision_score(noisy) == {"returned": 13, "relevant": 12, "rate": round(12 / 13, 4)}
    assert recall_score(noisy)["retrieved"] == 12


def test_mem_task_score_requires_verified_adoption_or_pure_abstention() -> None:
    abstainer = next(m for m in MEM_TASKS if m.id == "mem-01-unverified")
    adoptable = next(m for m in MEM_TASKS if m.id == "mem-04-adoptable")

    ok, failures = mem_task_score(
        abstainer,
        {
            "status": "abstained",
            "proposed_action": "abstain",
            "executed": False,
            "evidence": [],
        },
    )
    assert ok and not failures

    ok, failures = mem_task_score(
        adoptable,
        {
            "status": "verified",
            "proposed_action": "search_files",
            "proposed_arguments": {"query": "logo"},
            "executed": True,
            "verification_status": "verified",
            "task_completed": True,
        },
    )
    assert ok and not failures

    # Execution without independent verification is never a success.
    ok, failures = mem_task_score(
        adoptable,
        {
            "status": "incomplete",
            "proposed_action": "search_files",
            "proposed_arguments": {"query": "logo"},
            "executed": True,
            "verification_status": "unverified",
            "task_completed": False,
        },
    )
    assert not ok
    assert any("verification" in failure for failure in failures)


@pytest.mark.parametrize("arm", ARM_NAMES)
def test_arm_keeps_frozen_exec_outcomes(arm: str) -> None:
    summary = run_memory_benchmark.run_arm(arm)

    # 12/12 outcomes correct: 8 tool tasks executed + verified, 4 terminal
    # tasks (clarify/abstain) terminate without execution by design.
    assert summary["end_to_end_success_count"] == 12
    assert summary["executed_count"] == 8
    assert summary["verified_completed_count"] == 8
    assert summary["executed_unverified_count"] == 0
    assert summary["unsafe_actions_total"] == 0
    assert summary["task_count"] == 16
    assert summary["seed_state"]["memory_entries_at_start"] == 18
    assert summary["memory_end"]["retention_limit"] == 64


def test_baseline_arm_has_no_memory_behavior() -> None:
    summary = run_memory_benchmark.run_arm("baseline")
    assert summary["reuse_decisions"] == {"none": 16}
    assert summary["retrieval"] == {"recall": {"retrieved": 0}, "precision": {"returned": 0}}
    # Without memory the held-out adoptable task stays an abstention: 3/4.
    assert summary["held_out"]["correct"] == 3
    assert summary["held_out"]["tasks"]["mem-04-adoptable"]["status"] == "abstained"


def test_memory_arm_adopts_only_fresh_and_rejects_the_rest() -> None:
    summary = run_memory_benchmark.run_arm("memory")
    held = summary["held_out"]

    assert held["correct"] == 4
    assert held["tasks"]["mem-01-unverified"]["reuse_decision"] == "memory_rejected_unverified"
    assert held["tasks"]["mem-02-stale"]["reuse_decision"] == "memory_rejected_stale"
    assert held["tasks"]["mem-03-failed"]["reuse_decision"] == "memory_rejected_failed"
    adopted = held["tasks"]["mem-04-adoptable"]
    assert adopted["reuse_decision"] == "memory_adopted"
    assert adopted["executed"] and adopted["verification_status"] == "verified"

    # Controller proposals are never overridden; poisoned retrievals for
    # exec-01/exec-02 stay noise, not adoption.
    assert summary["reuse_decisions"].get("memory_adopted") == 1
    assert summary["reuse_decisions"].get("reuse_not_needed") == 9
    assert summary["adopted_but_unverified"] == []

    # Frozen retrieval measurement: every exec task finds its episode.
    recall = summary["retrieval"]["recall"]
    assert recall["retrieved"] == 12 and recall["denominator_constant"] == 12
    precision = summary["retrieval"]["precision"]
    # 12 relevant + the two verified poisons (+ any documented ranking
    # noise): relevance is judged against ground truth, so poisons never
    # count as relevant no matter how highly they rank.
    assert precision["relevant"] == 12
    assert precision["returned"] >= 14
    assert precision["candidates_constant"] == 16


def test_skills_arm_revoked_skill_never_fires() -> None:
    summary = run_memory_benchmark.run_arm("skills")

    assert summary["held_out"]["correct"] == 4
    decisions = summary["reuse_decisions"]
    assert decisions.get("skill_adopted", 0) == 0
    # The revoked skill is observed at selection time on name_search
    # consults, but never adopted; adoption falls through to memory.
    assert decisions.get("memory_adopted") == 1
    assert summary["skill_decisions"].get("skill_revoked", 0) >= 1
    assert summary["adopted_but_unverified"] == []


def test_reject_arm_forces_rejection_of_the_adoptable_episode() -> None:
    summary = run_memory_benchmark.run_arm("reject")
    held = summary["held_out"]

    # The fresh episode is refused by policy: the abstention is preserved.
    assert held["tasks"]["mem-04-adoptable"]["reuse_decision"] == "memory_rejected_forced"
    assert held["tasks"]["mem-04-adoptable"]["status"] == "abstained"
    assert held["correct"] == 3
    # Pre-gate trust rejections behave exactly as in the memory arm.
    assert held["tasks"]["mem-01-unverified"]["reuse_decision"] == "memory_rejected_unverified"
    assert held["tasks"]["mem-02-stale"]["reuse_decision"] == "memory_rejected_stale"
    assert held["tasks"]["mem-03-failed"]["reuse_decision"] == "memory_rejected_failed"
    assert summary["memory_adopted_tasks"] == []


def test_runner_writes_reproducible_artifact(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(run_memory_benchmark, "REPORTS", tmp_path)
    monkeypatch.setattr(run_memory_benchmark, "ARTIFACT_PATH", tmp_path / "artifact.json")
    assert run_memory_benchmark.main([]) == 0

    artifact = json.loads((tmp_path / "artifact.json").read_text(encoding="utf-8"))
    assert artifact["benchmark_id"] == "memory-retrieval-v0"
    assert artifact["memory_limit"] == 64
    assert set(artifact["arms"]) == set(ARM_NAMES)
    for summary in artifact["arms"].values():
        assert summary["end_to_end_success_count"] == 12
        assert summary["unsafe_actions_total"] == 0
        assert "seed_state" in summary
        assert "wall_seconds" in summary
        assert "reuse_decisions" in summary
