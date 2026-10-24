"""Regression tests for the tool-execution benchmark's attribution model.

The benchmark must never hide fallback-assisted successes inside the model's
score. These tests pin the attribution guarantees against the frozen
``executor-workflow-v0`` task definitions:

* the model's own action is recorded BEFORE any deterministic fallback;
* every task's final outcome lands in exactly one terminal path;
* model and fallback counts partition the model-called tasks;
* a successful fallback never inflates first-proposal accuracy;
* clarification and abstention are scored against frozen ground truth;
* verification failure prevents a false success;
* summary counters are reproducible from the per-task records alone.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.execution_tasks import execution_tasks
from scripts.run_executor_benchmark import summarize

from arceus_core.workflow import WorkflowResult


def _task(task_id: str):
    return next(task for task in execution_tasks() if task.id == task_id)


def _result(task_id: str, **overrides) -> WorkflowResult:
    """A verified, deterministic search success unless overridden."""

    task = _task(task_id)
    fields: dict = dict(
        task_id=task.id,
        task=task.prompt,
        status="verified",
        origin="deterministic",
        proposed_action="search_files",
        proposed_arguments={"query": "robin.md", "limit": 20},
        executed=True,
        tool_result={"action": "search_files", "query": "robin.md", "hit_count": 1},
        verification_status="verified",
        task_completed=True,
        explanation="synthetic test result",
    )
    fields.update(overrides)
    return WorkflowResult(**fields)


def _abstain(task_id: str, **overrides) -> WorkflowResult:
    fields: dict = dict(
        status="abstained",
        proposed_action="abstain",
        proposed_arguments={"reason": "no indexed evidence"},
        executed=False,
        tool_result=None,
        verification_status="verified",
        task_completed=True,
    )
    fields.update(overrides)
    return _result(task_id, **fields)


def test_model_action_is_recorded_before_fallback() -> None:
    """A wrong model proposal followed by a correct fallback: the model's
    action stays on record and is scored wrong; only the fallback gets the
    end-to-end credit."""

    task = _task("exec-09-content-question")
    result = _abstain(
        task.id,
        origin="deterministic",
        fallback_used=True,
        model_called=True,
        model_action="search_files",  # wrong first proposal (content question)
        rejection_stage="controller_validation",
        task_completed=False,
    )
    summary = summarize([task], {task.id: result})
    record = summary["tasks"][task.id]

    assert record["model_action"] == "search_files"  # recorded before fallback
    assert record["origin"] == "deterministic" and record["fallback_used"] is True
    assert summary["first_proposal_correct_count"] == 0  # never inflated
    assert record["success"] is True  # final abstention matches ground truth
    assert summary["end_to_end_success_count"] == 1
    assert summary["fallback_assisted_success_count"] == 1
    assert summary["model_originated_success_count"] == 0
    assert summary["terminal_paths"] == {"fallback_success": 1}


def test_every_task_lands_in_exactly_one_terminal_path() -> None:
    """Four synthetic outcomes covering all four terminal classes: the counts
    partition the suite and the model-called denominator exactly."""

    tasks = [
        _task("exec-01-search-file"),
        _task("exec-02-children"),
        _task("exec-06-no-result"),
        _task("exec-09-content-question"),
    ]
    results = {
        # 1. model-originated, correct, verified.
        "exec-01-search-file": _result(
            "exec-01-search-file",
            origin="model",
            model_called=True,
            model_action="search_files",
        ),
        # 2. model wrong, deterministic fallback corrects it.
        "exec-02-children": _result(
            "exec-02-children",
            origin="deterministic",
            fallback_used=True,
            model_called=True,
            model_action="ask_clarification",
            proposed_action="graph_children",
            proposed_arguments={"path": "media"},
            tool_result={"action": "graph_children", "path": "media", "child_count": 2},
        ),
        # 3. deterministic success, model never called.
        "exec-06-no-result": _abstain("exec-06-no-result"),
        # 4. deterministic failure (abstained where a tool action was needed).
        "exec-09-content-question": _result(
            "exec-09-content-question",
            proposed_action="graph_children",
            proposed_arguments={"path": "encyclopedia"},
            tool_result={"action": "graph_children", "path": "encyclopedia", "child_count": 2},
        ),
    }
    summary = summarize(tasks, results)

    assert summary["terminal_paths"] == {
        "model_success": 1,
        "fallback_success": 1,
        "deterministic_success": 1,
        "deterministic_failure": 1,
    }
    assert sum(summary["terminal_paths"].values()) == len(tasks)
    assert summary["model_called_count"] == 2
    assert (
        summary["model_validated_count"] + summary["fallback_count"]
        == (summary["model_called_count"])
    )
    assert summary["end_to_end_success_count"] == 3


def test_verification_failure_prevents_false_success() -> None:
    """The proposal matched the frozen ground truth, but the independent
    verifier could not confirm the outcome: that is NOT a success."""

    task = _task("exec-01-search-file")
    result = _result(
        task.id,
        origin="model",
        model_called=True,
        model_action="search_files",
        verification_status="incomplete",
        task_completed=False,
    )
    summary = summarize([task], {task.id: result})

    assert summary["tasks"][task.id]["success"] is False
    assert summary["model_originated_success_count"] == 0
    assert summary["terminal_paths"] == {"model_wrong": 1}


def test_terminals_are_scored_against_frozen_ground_truth() -> None:
    clarify = _task("exec-05-clarify")
    no_result = _task("exec-06-no-result")
    name_search = _task("exec-01-search-file")

    results = {
        # Correct clarification for the ambiguous task.
        clarify.id: _result(
            clarify.id,
            status="clarification_required",
            proposed_action="ask_clarification",
            proposed_arguments={"question": "welcome.md matched twice; which one?"},
            executed=False,
            tool_result=None,
            verification_status=None,
            task_completed=None,
        ),
        # Clarification is wrong where the ground truth expects abstention.
        no_result.id: _result(
            no_result.id,
            status="clarification_required",
            proposed_action="ask_clarification",
            proposed_arguments={"question": "which ledger?"},
            executed=False,
            tool_result=None,
            verification_status=None,
            task_completed=None,
        ),
        # Abstention is wrong where the ground truth expects a tool action.
        name_search.id: _abstain(name_search.id),
    }
    summary = summarize([clarify, no_result, name_search], results)

    assert summary["tasks"][clarify.id]["success"] is True
    assert summary["tasks"][no_result.id]["success"] is False
    assert summary["tasks"][name_search.id]["success"] is False
    assert summary["end_to_end_success_count"] == 1


def test_summary_is_reproducible_from_per_task_records() -> None:
    """The JSON artifact alone carries enough provenance to recompute the
    summary counters."""

    task = _task("exec-01-search-file")
    results = {
        task.id: _result(
            task.id,
            origin="model",
            model_called=True,
            model_action="search_files",
        ),
        "exec-02-children": _abstain(
            "exec-02-children",
            origin="deterministic",
            fallback_used=True,
            model_called=True,
            model_action="graph_children",
            rejection_stage="permission",
            task_completed=None,
        ),
    }
    tasks = [task, _task("exec-02-children")]
    summary = summarize(tasks, results)

    # Round-trips through JSON exactly like the artifact writer does.
    encoded = json.dumps(summary)
    loaded = json.loads(encoded)
    records = list(loaded["tasks"].values())

    assert len(records) == len(tasks)
    for record in records:
        for key in (
            "origin",
            "proposed_action",
            "proposed_arguments",
            "fallback_used",
            "executed",
            "verification_status",
            "task_completed",
            "model_called",
            "model_action",
            "rejection_stage",
            "terminal_path",
            "success",
        ):
            assert key in record, f"artifact record is missing provenance key {key!r}"

    assert loaded["model_called_count"] == sum(r["model_called"] for r in records)
    assert loaded["end_to_end_success_count"] == sum(bool(r["success"]) for r in records)
    assert loaded["fallback_count"] == sum(
        r["model_called"] and r["origin"] == "deterministic" and r["fallback_used"] for r in records
    )
    assert loaded["model_originated_success_count"] == sum(
        r["origin"] == "model" and r["success"] for r in records
    )
    assert sum(loaded["terminal_paths"].values()) == len(tasks)
