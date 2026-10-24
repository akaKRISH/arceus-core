"""Phase 7 tests: Budgeted Cognitive Routing.

The router is a thin deterministic policy over the existing controller,
memory, and skill services. These tests pin the strategy chosen for each
task category, the deterministic-first ordering, the model gate (unavailable /
resource-exhausted / ineligible), and the explicit fallback provenance on the
workflow result. No test here performs a live model call.
"""

from __future__ import annotations

import json

import pytest

from arceus_core.controller import CognitiveController
from arceus_core.errors import InferenceError
from arceus_core.inference import FakeInferenceBackend, InferenceRequest, LocalInference
from arceus_core.memory import EpisodicMemory, normalize_task_signature
from arceus_core.routing import (
    ROUTING_POLICY_VERSION,
    BudgetedInference,
    BudgetedRouter,
    ResourceSnapshot,
    RoutingBudget,
)
from arceus_core.scanner import scan_root
from arceus_core.skills import MIN_VERIFIED_RUNS, SkillAction, SkillRegistry
from arceus_core.workflow import TaskWorkflow

#: A task whose full phrase matches nothing but whose token ``alpha`` names
#: indexed evidence: the controller abstains with ``unmapped_intent``, which is
#: the only reason the Phase 7.1 policy may escalate.
RECOVERABLE_TASK = "locate the alpha briefing packet"


@pytest.fixture
def indexed(conn, root, root_dir, make_files):
    make_files(
        root_dir,
        {
            "notes/todo.md": "- note",
            "inbox/alpha_report.txt": "alpha",
            "inbox/alpha_summary.txt": "summary",
            "archive/report_2026.xlsx": "",
        },
    )
    scan_root(conn, root)
    return conn


def _fixed_probe(free_mb: float | None):
    if free_mb is None:

        def probe():
            return ResourceSnapshot(
                free_ram_mb=None, source="test", measured_at="t", kind="unavailable"
            )

        return probe

    def probe():
        return ResourceSnapshot(free_ram_mb=free_mb, source="test", measured_at="t")

    return probe


def _model_router(conn, responses, *, free_mb: float | None = 4096.0, budget=None):
    backend = FakeInferenceBackend(responses=responses)
    return BudgetedRouter(
        conn,
        inference=LocalInference(backend),
        budget=budget or RoutingBudget(),
        resource_probe=_fixed_probe(free_mb),
    )


def _register_skill(conn, task_text: str, *, skill_id: str = "find-secrets"):
    registry = SkillRegistry(conn)
    registry.register(
        id=skill_id,
        purpose="Find indexed secret material when a task names the network drive",
        supported_categories=("name_search",),
        accepted_task_signatures=(normalize_task_signature(task_text),),
        preconditions=({"kind": "root_active"},),
        action_sequence=[SkillAction(action="search_files", arguments={"query": "alpha"})],
        verification_procedure=("verifier confirms search_files returned a genuine match",),
        provenance="seeded from three independently verified benchmark episodes",
    )
    memory = EpisodicMemory(conn)
    for index in range(MIN_VERIFIED_RUNS):
        episode = memory.record(
            f"skill validation {skill_id} {index}",
            action="search_files",
            origin="deterministic",
            executed=True,
            verification_status="verified",
            task_completed=True,
            config_version="test-config",
        )
        registry.record_run(skill_id, episode_id=episode.id, verified=True, outcome="verified")
    assert registry.promote(skill_id)[0]
    return registry


# -- strategy selection per category -----------------------------------------


def test_name_search_routes_deterministic(indexed) -> None:
    decision = BudgetedRouter(indexed).route("find alpha_report")
    assert decision.strategy == "deterministic"
    assert decision.terminal is False
    assert decision.model_eligible is False
    assert decision.task_category == "name_search"
    assert decision.policy_version == ROUTING_POLICY_VERSION
    assert decision.evidence["deterministic_action"] == "search_files"


def test_structural_listing_routes_deterministic(indexed) -> None:
    decision = BudgetedRouter(indexed).route("list the contents of notes")
    assert decision.strategy == "deterministic"
    assert decision.evidence["deterministic_action"] == "graph_children"


def test_unsafe_request_is_terminal_abstain(indexed) -> None:
    decision = BudgetedRouter(indexed).route("delete every file in notes")
    assert decision.strategy == "abstain"
    assert decision.terminal is True
    assert decision.model_eligible is False
    assert decision.task_category == "unsafe_request"


def test_content_question_is_terminal_abstain(indexed) -> None:
    decision = BudgetedRouter(indexed).route("what does alpha_report say about deadlines")
    assert decision.strategy == "abstain"
    assert decision.terminal is True
    assert decision.task_category == "content_request"


def test_ambiguous_match_routes_clarify(indexed) -> None:
    decision = BudgetedRouter(indexed).route("find alpha")
    assert decision.strategy == "clarify"
    assert decision.terminal is True
    assert decision.evidence["deterministic_action"] == "ask_clarification"


def test_empty_task_is_terminal_abstain(indexed) -> None:
    decision = BudgetedRouter(indexed).route("   ")
    assert decision.strategy == "abstain"
    assert decision.terminal is True
    assert decision.task_category == "invalid"


# -- deterministic preference over reuse -------------------------------------


def test_well_supported_task_prefers_deterministic_over_skill(indexed) -> None:
    # A verified, eligible skill exists for a task the deterministic controller
    # can already serve, so deterministic must win (no needless reuse).
    task = "find alpha_report"
    registry = _register_skill(indexed, task)
    router = BudgetedRouter(indexed, skills=registry)
    decision = router.route(task)
    assert decision.evidence["deterministic_action"] == "search_files"
    assert decision.strategy == "deterministic"


# -- skill eligibility --------------------------------------------------------


def test_verified_skill_fills_abstention_gap(indexed) -> None:
    # A name_search whose query token matches nothing in the index, so the
    # deterministic controller abstains and the eligible verified skill fills
    # the gap.
    task = "find secrets on the network drive"
    registry = _register_skill(indexed, task)
    router = BudgetedRouter(indexed, skills=registry)
    decision = router.route(task)
    assert decision.evidence["deterministic_action"] == "abstain"
    assert decision.strategy == "verified_skill"
    assert decision.evidence["skill"]["passed"] is True
    assert decision.evidence["skill"]["passed"] is True


def test_revoked_skill_is_not_adoptable(indexed) -> None:
    task = "find secrets on the network drive"
    registry = _register_skill(indexed, task)
    registry.set_status("find-secrets", "revoked", "compromised procedure")
    router = BudgetedRouter(indexed, skills=registry)
    decision = router.route(task)
    assert decision.strategy != "verified_skill"
    assert decision.evidence["skill"]["decision"] == "skill_revoked"


def test_skill_with_failing_precondition_is_not_adoptable(indexed, root) -> None:
    task = "find secrets on the network drive"
    registry = _register_skill(indexed, task)
    # Deactivate the root the skill's root_active precondition requires.
    indexed.execute("UPDATE roots SET status='missing' WHERE id = ?", (root.id,))
    indexed.commit()
    router = BudgetedRouter(indexed, skills=registry)
    decision = router.route(task)
    assert decision.strategy != "verified_skill"
    assert decision.evidence["skill"]["passed"] is False


def test_no_skill_registry_reports_unavailable(indexed) -> None:
    # With no registry, a task the controller abstains on reaches the reuse
    # stage and reports the skill layer as unavailable.
    router = BudgetedRouter(indexed)
    decision = router.route("find secrets on the network drive")
    assert decision.evidence["skill"] is None


# -- memory eligibility -------------------------------------------------------


def test_authoritative_fresh_episode_fills_abstention_gap(indexed, root) -> None:
    memory = EpisodicMemory(indexed)
    memory.record(
        "locate the quarterly forecast workbook",
        action="search_files",
        origin="deterministic",
        executed=True,
        verification_status="verified",
        task_completed=True,
        evidence=[{"root_id": root.id, "rel_path": "notes/todo.md"}],
        provenance={"arguments": {"query": "forecast"}},
        config_version="test-config",
    )
    router = BudgetedRouter(indexed, memory=memory)
    decision = router.route("locate the quarterly forecast workbook")
    assert decision.strategy == "memory"
    assert "adoptable_episode" in decision.evidence["memory"]


def test_stale_episode_is_not_adoptable(indexed, root) -> None:
    memory = EpisodicMemory(indexed)
    memory.record(
        "locate the quarterly forecast workbook",
        action="search_files",
        origin="deterministic",
        executed=True,
        verification_status="verified",
        task_completed=True,
        evidence=[{"root_id": root.id, "rel_path": "notes/gone.txt"}],
        provenance={"arguments": {"query": "forecast"}},
        config_version="test-config",
    )
    router = BudgetedRouter(indexed, memory=memory)
    decision = router.route("locate the quarterly forecast workbook")
    assert decision.strategy != "memory"
    assert any("stale" in item["reason"] for item in decision.evidence["memory"]["rejected"])


def test_unverified_episode_is_not_adoptable(indexed, root) -> None:
    memory = EpisodicMemory(indexed)
    memory.record(
        "locate the quarterly forecast workbook",
        action="search_files",
        origin="deterministic",
        executed=True,
        verification_status="unverified",
        task_completed=None,
        evidence=[{"root_id": root.id, "rel_path": "notes/todo.md"}],
        provenance={"arguments": {"query": "forecast"}},
        config_version="test-config",
    )
    router = BudgetedRouter(indexed, memory=memory)
    decision = router.route("locate the quarterly forecast workbook")
    assert decision.strategy != "memory"


# -- model gate ---------------------------------------------------------------


def test_no_inference_makes_model_ineligible(indexed) -> None:
    router = BudgetedRouter(indexed, resource_probe=_fixed_probe(4096.0))
    decision = router.route(RECOVERABLE_TASK)
    assert decision.strategy == "abstain"
    assert decision.model_eligible is False
    assert decision.abstain_reason_code == "unmapped_intent"
    assert decision.escalation_allowed is True
    assert decision.evidence["model_gate"] == "model_unavailable"


def test_low_memory_blocks_model_even_with_inference(indexed) -> None:
    router = _model_router(
        indexed,
        [json.dumps({"action": "abstain", "arguments": {"reason": "x"}, "rationale": "x"})],
        free_mb=100.0,
    )
    decision = router.route(RECOVERABLE_TASK)
    assert decision.strategy == "abstain"
    assert decision.model_eligible is False
    assert decision.evidence["model_gate"] == "resource_exhausted"
    assert decision.fallback_reason == "resource_exhausted"
    assert decision.escalation_allowed is True


def test_unknown_resources_blocked_when_required(indexed) -> None:
    router = BudgetedRouter(
        indexed,
        inference=LocalInference(FakeInferenceBackend()),
        budget=RoutingBudget(require_known_resources=True),
        resource_probe=_fixed_probe(None),
    )
    decision = router.route(RECOVERABLE_TASK)
    assert decision.evidence["model_gate"] == "resources_unknown"


def test_unknown_resources_allowed_by_default(indexed) -> None:
    router = BudgetedRouter(
        indexed,
        inference=LocalInference(FakeInferenceBackend()),
        resource_probe=_fixed_probe(None),
    )
    decision = router.route(RECOVERABLE_TASK)
    assert decision.strategy == "model"
    assert decision.evidence["escalation_policy"] == "recoverable_interpretation_gap"


def test_sufficient_memory_allows_model(indexed) -> None:
    router = _model_router(
        indexed,
        [json.dumps({"action": "abstain", "arguments": {"reason": "x"}, "rationale": "x"})],
        free_mb=4096.0,
    )
    decision = router.route(RECOVERABLE_TASK)
    assert decision.strategy == "model"
    assert decision.model_eligible is True


def test_resource_exhaustion_is_labelled_measured(indexed) -> None:
    router = BudgetedRouter(indexed, resource_probe=_fixed_probe(128.0))
    decision = router.route("find alpha_report")
    assert decision.resource["kind"] == "measured"
    assert decision.resource["free_ram_mb"] == 128.0
    # Deterministic strategy is still chosen; resource only gates the model.
    assert decision.strategy == "deterministic"


# -- budget validation --------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_model_calls": 0},
        {"max_inference_seconds": 0},
        {"max_output_tokens": 0},
        {"max_execution_actions": 0},
        {"memory_candidates": 0},
        {"skill_candidates": 0},
        {"max_workflow_seconds": 0},
        {"min_free_ram_mb": -1},
    ],
)
def test_invalid_budget_is_rejected(kwargs) -> None:
    with pytest.raises(ValueError):
        RoutingBudget(**kwargs)


# -- budget enforcement -------------------------------------------------------


class _RecordingInference:
    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def generate(self, request):
        self.timeouts.append(request.timeout_seconds)
        return "ok"


def test_budgeted_inference_caps_the_request_timeout() -> None:
    inner = _RecordingInference()
    budgeted = BudgetedInference(inner, RoutingBudget(max_inference_seconds=5.0))
    assert budgeted.generate(InferenceRequest(prompt="x", timeout_seconds=120.0)) == "ok"
    # The configured cap wins over the caller's larger timeout.
    assert inner.timeouts == [5.0]


def test_budgeted_inference_keeps_a_smaller_caller_timeout() -> None:
    inner = _RecordingInference()
    budgeted = BudgetedInference(inner, RoutingBudget(max_inference_seconds=60.0))
    budgeted.generate(InferenceRequest(prompt="x", timeout_seconds=2.0))
    assert inner.timeouts == [2.0]


def test_budgeted_inference_exhausts_the_model_call_budget() -> None:
    inner = _RecordingInference()
    budgeted = BudgetedInference(inner, RoutingBudget(max_model_calls=2))
    for _ in range(2):
        budgeted.generate(InferenceRequest(prompt="x"))
    with pytest.raises(InferenceError, match="budget exhausted"):
        budgeted.generate(InferenceRequest(prompt="x"))
    # The exhausted call never reaches the backend.
    assert len(inner.timeouts) == 2
    assert budgeted.calls == 2


def test_exhausted_model_budget_falls_back_with_attribution(indexed) -> None:
    backend = FakeInferenceBackend(
        responses=[
            json.dumps({"action": "abstain", "arguments": {"reason": "x"}, "rationale": "x"})
        ]
    )
    router = BudgetedRouter(
        indexed,
        inference=LocalInference(backend),
        budget=RoutingBudget(max_model_calls=1),
        resource_probe=_fixed_probe(4096.0),
    )
    first = _run(indexed, router, RECOVERABLE_TASK)
    assert first.routing_decision["strategy"] == "model"
    assert first.model_called is True
    assert first.fallback_used is False

    # The per-task budget is spent: the next task still routes to the model
    # strategy, but the attempt is refused and attributed as a fallback.
    second = _run(indexed, router, RECOVERABLE_TASK)
    assert second.routing_decision["strategy"] == "model"
    assert second.model_called is True
    assert second.fallback_used is True
    assert second.origin == "deterministic"
    assert "budget exhausted" in (second.rejection_reason or "")


def test_separate_tasks_get_separate_model_budgets(indexed) -> None:
    backend = FakeInferenceBackend(
        responses=[
            json.dumps({"action": "abstain", "arguments": {"reason": "x"}, "rationale": "x"}),
            json.dumps({"action": "abstain", "arguments": {"reason": "x"}, "rationale": "x"}),
        ]
    )
    first_router = BudgetedRouter(
        indexed, inference=LocalInference(backend), resource_probe=_fixed_probe(4096.0)
    )
    _run(indexed, first_router, RECOVERABLE_TASK)
    second_router = BudgetedRouter(
        indexed, inference=LocalInference(backend), resource_probe=_fixed_probe(4096.0)
    )
    result = _run(indexed, second_router, RECOVERABLE_TASK)
    assert result.fallback_used is False
    assert backend._index == 2


def test_budget_estimate_is_configured_not_measured(indexed) -> None:
    budget = RoutingBudget(max_model_calls=2, max_output_tokens=128)
    router = BudgetedRouter(indexed, budget=budget, resource_probe=_fixed_probe(4096.0))
    decision = router.route("find alpha_report")
    assert decision.budget_estimate["max_model_calls"] == 2
    assert decision.budget_estimate["max_output_tokens"] == 128
    # The estimate mirrors the configured policy exactly, with no measured
    # cost mixed in.
    assert decision.budget_estimate == budget.to_dict()


# -- workflow integration and attribution -------------------------------------


def _run(conn, router, task):
    workflow = TaskWorkflow(
        conn,
        CognitiveController(conn),
        router=router,
        memory=router._memory,
        skills=router._skills,
    )
    return workflow.run(task)


def test_workflow_records_routing_decision(indexed) -> None:
    router = BudgetedRouter(indexed)
    result = _run(router._conn, router, "find alpha_report")
    assert result.routing_decision is not None
    assert result.routing_decision["strategy"] == "deterministic"
    assert result.routing_decision["policy_version"] == ROUTING_POLICY_VERSION
    assert result.status == "verified"
    assert result.to_dict()["routing_decision"]["strategy"] == "deterministic"


def test_workflow_without_router_has_no_routing_decision(indexed) -> None:
    workflow = TaskWorkflow(indexed, CognitiveController(indexed))
    result = workflow.run("find alpha_report")
    assert result.routing_decision is None
    assert result.to_dict()["routing_decision"] is None


def test_workflow_model_strategy_uses_model_and_attributes(indexed) -> None:
    backend = FakeInferenceBackend(
        responses=[
            json.dumps(
                {
                    "action": "search_files",
                    "arguments": {"query": "alpha_report.txt"},
                    "rationale": "model found the file",
                }
            )
        ]
    )
    router = BudgetedRouter(
        indexed,
        inference=LocalInference(backend),
        resource_probe=_fixed_probe(4096.0),
    )
    result = _run(indexed, router, RECOVERABLE_TASK)
    assert result.routing_decision["strategy"] == "model"
    assert result.model_called is True
    assert result.origin == "model"
    # The backend was actually consulted exactly once.
    assert backend._index == 1


def test_workflow_model_fallback_is_attributed(indexed) -> None:
    # Model proposes a forbidden action; the controller must fall back to the
    # deterministic policy and the rejection stage must be recorded.
    backend = FakeInferenceBackend(
        responses=[
            json.dumps({"action": "delete_files", "arguments": {"path": "notes"}, "rationale": "x"})
        ]
    )
    router = BudgetedRouter(
        indexed,
        inference=LocalInference(backend),
        resource_probe=_fixed_probe(4096.0),
    )
    result = _run(indexed, router, RECOVERABLE_TASK)
    assert result.routing_decision["strategy"] == "model"
    assert result.fallback_used is True
    assert result.origin == "deterministic"
    assert result.rejection_stage == "controller_validation"
    assert result.model_called is True
    assert result.model_action == "delete_files"


def test_routing_is_deterministic_for_same_task(indexed) -> None:
    router = BudgetedRouter(indexed)
    first = router.route("find alpha_report").to_dict()
    second = router.route("find alpha_report").to_dict()
    # Resource timestamps differ by design; everything else must match.
    first.pop("resource")
    second.pop("resource")
    assert first == second
