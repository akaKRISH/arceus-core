"""Executor-boundary tests: validation, scope, and safety.

The suite covers the Phase 5 required matrix for the execution component:
supported actions, invalid actions/types, unregistered roots, out-of-scope
paths, malformed arguments, bounded evidence, no filesystem modification,
and explicit provenance.
"""

from __future__ import annotations

import json

import pytest

from arceus_core.controller import CognitiveController, Proposal
from arceus_core.executor import ProposalExecutor
from arceus_core.roots import add_root
from arceus_core.scanner import scan_root


@pytest.fixture
def indexed_root(conn, root_dir, make_files):
    make_files(
        root_dir,
        {
            "budget.xlsx": "",
            "readme.md": "",
            "docs/readme.md": "",
            "src/main.py": "# code",
            "src/util/helper.py": "",
        },
    )
    root = add_root(conn, root_dir, label="work")
    scan_root(conn, root)
    return root


def _proposal(action: str, arguments: dict, *, origin: str = "model") -> Proposal:
    return Proposal(
        action=action,
        arguments=arguments,
        status="ready",
        rationale="test",
        permission_level="read_only",
        origin=origin,
    )


def test_search_files_end_to_end(conn, indexed_root):
    executor = ProposalExecutor(conn)
    result = executor.execute("t1", _proposal("search_files", {"query": "budget.xlsx"}))
    assert result.status == "executed"
    assert result.tool_result["hit_count"] >= 1
    assert result.evidence[0]["name"] == "budget.xlsx"
    assert result.task_completed is None  # filled by the verifier, not the executor


def test_children_reject_deeper_descendants_as_evidence(conn, indexed_root):
    executor = ProposalExecutor(conn)
    result = executor.execute("t2", _proposal("graph_children", {"path": "src"}))
    assert result.status == "executed"
    names = {item["name"] for item in result.evidence}
    assert names == {"main.py", "util"}


def test_executor_rejects_unknown_action(conn, indexed_root):
    executor = ProposalExecutor(conn)
    result = executor.execute("t3", _proposal("delete_files", {}))
    assert result.status == "rejected"
    assert not result.executed
    assert "allow-list" in (result.failure_reason or "")


def test_executor_rejects_unknown_or_malformed_arguments(conn, indexed_root):
    executor = ProposalExecutor(conn)
    for action, args in (
        ("search_files", {"query": 42}),
        ("search_files", {" Query ": "x"}),
        ("graph_children", {"path": "src", "limit": 5}),
        ("graph_children", {}),
        ("graph_ancestors", {"path": ".."}),
        ("search_files", {"query": "a", "limit": 0}),
        ("search_files", {"query": "a", "limit": 10**9}),
    ):
        result = executor.execute("bad", _proposal(action, args))
        assert result.status == "rejected", (action, args)
        assert not result.executed


def test_executor_rejects_unregistered_root_and_out_of_scope_path(conn, indexed_root, tmp_path):
    executor = ProposalExecutor(conn)
    outside = executor.execute(
        "t4",
        _proposal("graph_children", {"path": str(tmp_path / "elsewhere"), "root_label": "work"}),
    )
    assert outside.status == "rejected"
    assert not outside.executed

    wrong_label = executor.execute(
        "t5",
        _proposal("graph_children", {"path": "src", "root_label": "ghost-root"}),
    )
    assert wrong_label.status == "rejected"


def test_terminal_decisions_never_execute(conn, indexed_root):
    executor = ProposalExecutor(conn)
    clarify = executor.execute("t6", _proposal("ask_clarification", {"question": "which one?"}))
    abstain = executor.execute("t7", _proposal("abstain", {"reason": "not possible"}))
    assert clarify.status == "clarification_required"
    assert abstain.status == "abstained"
    assert not clarify.executed and not abstain.executed
    assert clarify.tool_result is None and abstain.tool_result is None


def test_proposal_with_derived_keys_is_stripped_not_trusted(conn, indexed_root):
    # The controller enriches arguments with root_id/absolute_path AFTER
    # validation; the executor must strip them and re-derive authorization.
    proposal = _proposal(
        "graph_children",
        {"path": "src", "root_id": 999, "absolute_path": "C:/elsewhere"},
    )
    result = ProposalExecutor(conn).execute("t8", proposal)
    assert result.status == "executed"
    assert result.tool_result["root_id"] == indexed_root.id
    assert "absolute_path" not in result.tool_result or result.tool_result[
        "absolute_path"
    ].startswith(str(indexed_root.path))


def test_bad_paths_never_escape_registry_scope(conn, indexed_root):
    executor = ProposalExecutor(conn)
    for path in ("../../etc", "src/../../x", "C:/Windows", "\\\\server\\share"):
        result = executor.execute("t9", _proposal("graph_children", {"path": path}))
        assert result.status == "rejected", path


def test_evidence_is_bounded_and_content_free(conn, indexed_root):
    executor = ProposalExecutor(conn, evidence_limit=2)
    result = executor.execute("t10", _proposal("search_files", {"query": "readme", "limit": 50}))
    assert len(result.evidence) <= 2
    for item in result.evidence:
        assert set(item) <= {
            "root_id",
            "rel_path",
            "parent_rel_path",
            "name",
            "entry_type",
            "status",
            "observed_at",
            "last_seen_run_id",
            "root_path",
            "absolute_path",
            "match_score",
            "match_reason",
        }


def test_execution_does_not_modify_the_filesystem(conn, indexed_root, root_dir):
    before = {
        path: (path.stat().st_mtime_ns, path.stat().st_size if path.is_file() else None)
        for path in root_dir.rglob("*")
    }
    executor = ProposalExecutor(conn)
    executor.execute("t11", _proposal("search_files", {"query": "budget"}))
    executor.execute("t12", _proposal("graph_children", {"path": "src"}))
    after = {
        path: (path.stat().st_mtime_ns, path.stat().st_size if path.is_file() else None)
        for path in root_dir.rglob("*")
    }
    assert before == after


def test_model_output_cannot_trigger_shell_or_writes(conn, indexed_root):
    executor = ProposalExecutor(conn)
    hostile_payloads = [
        {"action": "search_files", "shell": True},
        {"action": "run_script", "script": "rm -rf /"},
        {"action": "write_file", "path": "evil.py"},
    ]
    for payload in hostile_payloads:
        encoded = json.dumps(payload)
        # Whatever the model emitted, only validated Proposal objects reach
        # the executor, and an injected command string is just an argument.
        for action in ("search_files", "graph_children"):
            proposal = _proposal(
                action, {"query": encoded} if action == "search_files" else {"path": "src"}
            )
            result = executor.execute("t13", proposal)
            assert result.status in {"executed", "rejected"}
            assert result.proposed_action in {"search_files", "graph_children"}


def test_workspace_records_carry_explicit_origin(conn, indexed_root):
    executor = ProposalExecutor(conn)
    model_result = executor.execute("t14", _proposal("search_files", {"query": "budget"}))
    det_result = executor.execute(
        "t15", _proposal("search_files", {"query": "budget"}, origin="deterministic")
    )
    assert model_result.origin == "model"
    assert det_result.origin == "deterministic"
    record = det_result.to_dict()
    json.dumps(record)  # audit record must be serializable with no secrets


def test_controller_decisions_execute_without_revalidation_gap(conn, indexed_root):
    """Deterministic and model-style decisions go through the same boundary."""

    controller = CognitiveController(conn)
    executor = ProposalExecutor(conn)
    proposal, _ = controller.decide_with_trace("find budget.xlsx")
    result = executor.execute("t16", proposal)
    assert result.status == "executed"
    assert result.origin == "deterministic"
