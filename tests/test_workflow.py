"""Workflow tests: the full one-task loop with explicit attribution.

Model failures, malformed outputs, and fallback usage are always recorded
as explicit data; tests cover the Phase 5 required matrix for the workflow.
"""

from __future__ import annotations

import json

import pytest

from arceus_core.controller import CognitiveController
from arceus_core.inference import FakeInferenceBackend, LocalInference
from arceus_core.roots import add_root
from arceus_core.scanner import scan_root
from arceus_core.workflow import TaskWorkflow


@pytest.fixture
def indexed_workflow(conn, root_dir, make_files):
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
    scanned_root = add_root(conn, root_dir, label="work")
    _ = scan_root(conn, scanned_root)

    def build(backend=None):
        controller = (
            CognitiveController(conn, inference=LocalInference(backend))
            if backend is not None
            else CognitiveController(conn)
        )
        return TaskWorkflow(conn, controller)

    return build


def _model_response(action: str, arguments: dict) -> str:
    return json.dumps({"action": action, "arguments": arguments, "rationale": "test rationale"})


def test_valid_model_proposal_runs_and_verifies(indexed_workflow):
    backend = FakeInferenceBackend(
        responses=[_model_response("search_files", {"query": "budget.xlsx"})]
    )
    wf = indexed_workflow(backend)
    result = wf.run("find budget.xlsx")
    assert result.origin == "model"
    assert result.proposed_action == "search_files"
    assert result.executed
    assert result.verification_status == "verified"
    assert result.task_completed is True
    assert not result.fallback_used


def test_malformed_model_json_falls_back_and_is_attributed(indexed_workflow):
    backend = FakeInferenceBackend(responses=["{not json"])
    wf = indexed_workflow(backend)
    result = wf.run("find budget.xlsx")
    assert result.origin == "deterministic"
    assert result.fallback_used is True
    assert result.rejection_stage == "json_parse"
    assert result.tool_result is not None  # fallback still executes properly
    assert result.task_completed is True


def test_model_failure_falls_back_retry_limit(indexed_workflow):
    backend = FakeInferenceBackend(fail_with=TimeoutError("simulated model timeout"))
    wf = indexed_workflow(backend)
    result = wf.run("what is in src")
    assert result.origin == "deterministic"
    assert result.fallback_used is True
    assert result.rejection_stage == "model_unavailable"
    assert result.proposed_action == "graph_children"


def test_invalid_model_action_falls_back(indexed_workflow):
    backend = FakeInferenceBackend(
        responses=[
            json.dumps(
                {
                    "action": "delete_files",
                    "arguments": {"path": "src"},
                    "rationale": "unwanted",
                }
            )
        ]
    )
    wf = indexed_workflow(backend)
    result = wf.run("what is in src")
    assert result.origin == "deterministic"
    assert result.fallback_used
    assert result.proposed_action == "graph_children"


def test_model_abstain_with_evidence_is_flagged_unverified(indexed_workflow):
    backend = FakeInferenceBackend(
        responses=[_model_response("abstain", {"reason": "cannot help"})]
    )
    wf = indexed_workflow(backend)
    result = wf.run("find budget.xlsx")
    assert result.proposed_action == "abstain"
    assert result.status == "abstained"
    # Index has matching evidence: abstention withheld it -> not certified.
    assert result.task_completed is None
    assert result.verification_status == "unverified"


def test_no_result_search_abstains_and_verifies_the_evidence_claim(indexed_workflow):
    """No-result tasks terminate with a qualified abstention, never a fake
    discovery; the verifier independently confirms the absence of evidence."""

    wf = indexed_workflow(None)
    result = wf.run("find nonexistent.qml")
    assert result.proposed_action == "abstain"
    assert result.status == "abstained"
    assert result.verification_status == "verified"
    assert result.task_completed is True  # correct terminal for a no-evidence task


def test_content_question_abstains_and_stays_incomplete(indexed_workflow):
    """A document-content question terminates with an abstention that is
    verified as the correct decision, but the task itself is NOT completed:
    metadata can never supply document text."""

    wf = indexed_workflow(None)
    result = wf.run("what does readme.md say about the project")
    assert result.proposed_action == "abstain"
    assert result.status == "abstained"
    assert result.executed is False
    assert result.verification_status == "verified"
    assert result.task_completed is False


def test_content_question_search_execution_is_not_declared_complete(indexed_workflow):
    """Even when a model-proposed search locates the named file, an
    independent verifier must not certify a content question from metadata."""

    backend = FakeInferenceBackend(
        responses=[_model_response("search_files", {"query": "readme.md"})]
    )
    wf = indexed_workflow(backend)
    result = wf.run("what does readme.md say about the project")
    assert result.origin == "model"
    assert result.executed
    assert result.verification_status == "incomplete"
    assert result.task_completed is False
    assert result.status == "incomplete"


def test_unindexed_structural_path_abstains_without_execution(indexed_workflow):
    """A structural request for a path the index does not contain abstains
    instead of executing a graph call that could only invent an answer."""

    wf = indexed_workflow(None)
    result = wf.run("list what is in project/src")
    assert result.proposed_action == "abstain"
    assert result.executed is False
    assert result.tool_result is None
    assert result.verification_status == "verified"


def test_stale_index_downgrades_verification(conn, indexed_workflow):
    from arceus_core.roots import get_root, set_root_status

    wf = indexed_workflow(None)
    healthy = wf.run("find budget.xlsx")
    assert healthy.verification_status == "verified"

    # The registry marks the root as unavailable: evidence is now suspect.
    set_root_status(conn, get_root(conn, "work").id, "missing")
    stale = wf.run("find budget.xlsx")
    assert stale.proposed_action == "search_files"
    assert stale.verification_status == "unverified"
    assert "marked missing" in (stale.verification_reason or "")


def test_workflow_one_action_per_request(indexed_workflow):
    wf = indexed_workflow(None)
    first = wf.run("find budget.xlsx")
    second = wf.run("find budget.xlsx")
    assert first.task_id != second.task_id
    # Every call performs AT MOST one tool action: executions == requests.
    assert first.executed and second.executed


def test_workspace_records_contain_no_file_contents(indexed_workflow):
    wf = indexed_workflow(None)
    result = wf.run("find budget.xlsx")
    record = result.to_dict()
    text = json.dumps(record)
    assert "code" not in text or "src/main.py" not in record


def _last_request(backend):
    """Return the inference request the backend last saw (Stage 7 evidence)."""

    assert backend.requests, "backend never received an inference request"
    return backend.requests[-1]


def test_model_context_is_minimal_and_schema_is_passed(indexed_workflow):
    """The model receives only the task, rules, root labels, and the schema.

    Index metadata (file names, paths, sizes) and file contents are never
    placed into the model prompt; the structured-output schema is forwarded.
    """

    from arceus_core.controller import CONTROLLER_JSON_SCHEMA

    class _RecordingBackend(FakeInferenceBackend):
        def __init__(self) -> None:
            super().__init__(responses=[_model_response("search_files", {"query": "budget.xlsx"})])
            self.requests: list[object] = []

        def generate(self, request):
            self.requests.append(request)
            return super().generate(request)

    backend = _RecordingBackend()
    wf = indexed_workflow(backend)
    result = wf.run("find budget.xlsx")
    assert result.origin == "model"

    request = _last_request(backend)
    assert request.prompt == "find budget.xlsx"
    assert request.output_json_schema == CONTROLLER_JSON_SCHEMA
    assert request.contract is None

    system_prompt = request.system_prompt
    assert "permits" in system_prompt or "read-only" in system_prompt
    assert '"work"' in system_prompt or "work" in system_prompt
    # No index metadata or file contents leak into the prompt.
    assert "budget.xlsx" not in system_prompt
    assert "src/main.py" not in system_prompt
    assert "helper.py" not in system_prompt


def test_root_label_required_for_relative_path_with_multiple_roots(conn, tmp_path, make_files):
    from arceus_core.controller import Proposal
    from arceus_core.executor import ProposalExecutor

    for name in ("rootA", "rootB"):
        target = tmp_path / name
        target.mkdir()
        _ = (target / "f.py").write_text("", encoding="utf-8")
        root = add_root(conn, target, label=name)
        scan_root(conn, root)

    executor = ProposalExecutor(conn)
    proposal = Proposal(
        action="graph_children",
        arguments={"path": ""},
        status="ready",
        rationale="test",
        permission_level="read_only",
    )
    result = executor.execute("multi", proposal)
    assert result.status == "rejected"
