"""Bounded task workflow: one user task, one validated read-only action.

Complete flow per call (no loop):

1. Record the original task text.
2. Ask the controller for a proposal (model when available; the existing
   deterministic policy otherwise or as explicit fallback).
3. Parse/validate the proposed action (controller + executor both
   re-validate — the same application contract for every backend).
4. Permission/root checks happen inside validation: root-relative paths
   resolve against the registered-roots registry only.
5. Execute only an allow-listed read-only tool (search/graph over the
   index); clarification and abstention terminate without executing.
6. Collect tool result + bounded evidence.
7. Run the independent verifier.
8. Return a structured, attributable result plus a short explanation.

Model failures never masquerade as model successes: the controller's
decision trace records the fallback stage explicitly, and records carry
origin ("model" | "deterministic") as data.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field, replace
from typing import Any

from arceus_core.controller import (
    CONTROLLER_SCHEMA_VERSION,
    CognitiveController,
    classify_task,
)
from arceus_core.executor import TOOL_ACTIONS, ProposalExecutor, TaskStatus
from arceus_core.memory import MEMORY_POLICY_VERSION, EpisodicMemory
from arceus_core.skills import SKILL_POLICY_VERSION, SkillRegistry
from arceus_core.verifier import VerificationStatus, verify_execution

MAX_TASK_LENGTH = 2000  # mirrors controller.MAX_TASK_LENGTH


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    """One bounded workflow attempt, fully attributable."""

    task_id: str
    task: str
    status: TaskStatus
    origin: Any
    proposed_action: str
    proposed_arguments: dict[str, Any] = field(default_factory=dict)
    fallback_used: bool = False
    executed: bool = False
    tool_result: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    verification_status: VerificationStatus | None = None
    verification_reason: str | None = None
    task_completed: bool | None = None
    explanation: str = ""
    rejection_stage: str | None = None
    rejection_reason: str | None = None
    wall_seconds: float = 0.0
    model_called: bool = False
    model_action: str | None = None
    raw_model_text: str | None = None
    # Phase 6 reuse attribution (None when memory/skills are disabled):
    # reuse_decision is one of: reuse_not_needed, memory_adopted,
    # memory_rejected_stale|unverified|failed|forced, memory_no_match,
    # memory_unavailable, skill_adopted, skill_precondition_failed,
    # skill_revoked, skill_disabled, skill_not_verified, skill_input_mismatch,
    # skill_no_match.
    reuse_decision: str | None = None
    reuse_refs: dict[str, Any] = field(default_factory=dict)
    # Phase 7 BCR routing attribution (None when no router is attached):
    # the full RoutingDecision.to_dict() — strategy, reason, evidence,
    # configured budget estimate, resource snapshot, fallback reason.
    routing_decision: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task": self.task,
            "status": self.status,
            "origin": self.origin,
            "proposed_action": self.proposed_action,
            "proposed_arguments": dict(self.proposed_arguments),
            "fallback_used": self.fallback_used,
            "executed": self.executed,
            "tool_result": self.tool_result,
            "evidence": list(self.evidence),
            "warnings": list(self.warnings),
            "verification_status": self.verification_status,
            "verification_reason": self.verification_reason,
            "task_completed": self.task_completed,
            "explanation": self.explanation,
            "rejection_stage": self.rejection_stage,
            "rejection_reason": self.rejection_reason,
            "wall_seconds": self.wall_seconds,
            "model_called": self.model_called,
            "model_action": self.model_action,
            "raw_model_text": self.raw_model_text,
            "reuse_decision": self.reuse_decision,
            "reuse_refs": dict(self.reuse_refs),
            "routing_decision": dict(self.routing_decision) if self.routing_decision else None,
        }


class TaskWorkflow:
    """One task -> one validated proposal -> at most one tool execution."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        controller: CognitiveController,
        *,
        task_id_prefix: str = "wf",
        memory: EpisodicMemory | None = None,
        skills: SkillRegistry | None = None,
        allow_memory_adoption: bool = True,
        router: Any | None = None,
    ) -> None:
        self._conn = conn
        self._controller = controller
        self._executor = ProposalExecutor(conn)
        self._counter = 0
        self._prefix = task_id_prefix
        self._memory = memory
        self._skills = skills
        self._allow_memory_adoption = allow_memory_adoption
        # Phase 7: optional Budgeted Cognitive Routing. When attached, the
        # workflow consults the router BEFORE the controller, and the
        # workflow's controller must be the deterministic one — the router
        # owns the (optional) model-attached controller and only invokes it
        # for strategy=model, after the budget and resource gate passes.
        self._router = router

    def run(self, task: str) -> WorkflowResult:
        started = time.perf_counter()
        self._counter += 1
        task_id = f"{self._prefix}-{self._counter:04d}"
        text = task.strip()
        if not text or len(text) > MAX_TASK_LENGTH:
            return WorkflowResult(
                task_id=task_id,
                task=task[:MAX_TASK_LENGTH],
                status="abstained",
                origin="deterministic",
                proposed_action="abstain",
                fallback_used=False,
                executed=False,
                tool_result=None,
                verification_status="unverified",
                verification_reason="task input empty or oversized",
                task_completed=False,
                explanation="The task text is empty or too long to process safely.",
            )

        routing_decision: dict[str, Any] | None = None
        if self._router is not None:
            decision = self._router.route(text)
            routing_decision = decision.to_dict()
            if decision.strategy == "model" and self._router.model_controller is not None:
                # The model gate already passed; the model-attached controller
                # still re-validates every proposal and falls back to the
                # deterministic policy on any rejection (attributed in trace).
                proposal, trace = self._router.model_controller.decide_with_trace(text)
            else:
                proposal, trace = self._controller.decide_with_trace(text)
        else:
            proposal, trace = self._controller.decide_with_trace(text)

        reuse_decision: str | None = None
        reuse_refs: dict[str, Any] = {}
        if self._memory is not None or self._skills is not None:
            adopted, reuse_decision, reuse_refs = self._reuse(text, proposal)
            if adopted is not None:
                proposal = adopted
                trace = replace(trace, origin=proposal.origin)

        execution = self._executor.execute(task_id, proposal)

        status, reason, completed = verify_execution(text, execution, conn=self._conn)
        # Fold verifier certainty into the task status (executed -> verified/
        # incomplete); executor states stay authoritative otherwise.
        status_map = {
            "proposed": "proposed",
            "rejected": "rejected",
            "failed": "failed",
            "clarification_required": "clarification_required",
            "abstained": "abstained",
        }
        if execution.status == "executed":
            final_status: TaskStatus = "verified" if status == "verified" else "incomplete"
        else:
            final_status = status_map.get(execution.status, execution.status)

        explanation = _explain(text, proposal.action, execution, status, reason)
        fallback_used = bool(trace.model_called and trace.rejection_stage is not None)
        result = WorkflowResult(
            task_id=task_id,
            task=text,
            status=final_status,
            origin=proposal.origin,
            proposed_action=proposal.action,
            proposed_arguments=dict(proposal.arguments),
            fallback_used=fallback_used,
            executed=execution.executed,
            tool_result=execution.tool_result,
            evidence=list(execution.evidence),
            warnings=list(execution.warnings),
            verification_status=status,
            verification_reason=reason,
            task_completed=completed,
            explanation=explanation,
            rejection_stage=trace.rejection_stage,
            rejection_reason=trace.rejection_reason,
            wall_seconds=round(time.perf_counter() - started, 4),
            model_called=trace.model_called,
            model_action=trace.model_action,
            raw_model_text=trace.raw_model_text,
            reuse_decision=reuse_decision,
            reuse_refs=reuse_refs,
            routing_decision=routing_decision,
        )
        # Optional write-back: record the completed episode (deduplicated).
        # Recording failures never fail the task run.
        if self._memory is not None:
            try:
                self._memory.record_from_workflow_result(
                    result, config_version=CONTROLLER_SCHEMA_VERSION
                )
            except Exception as exc:  # memory layer must stay non-fatal
                result = replace(
                    result,
                    reuse_refs={**reuse_refs, "record_error": type(exc).__name__},
                )
        return result

    # -- Phase 6: optional episodic-memory / verified-skill reuse -----------

    def _reuse(self, text: str, proposal: Any) -> tuple[Any, str | None, dict[str, Any]]:
        """Consult episodic memory and the verified-skill registry.

        Safety rules enforced here:

        * reuse only ever REPLACES an abstaining decision; a controller
          proposal or a terminal clarify is never overridden;
        * an adopted action is still a ``Proposal`` that flows through the
          same executor validation, registered-root permission checks, and
          independent verifier as everything else;
        * only an ACTIVE, independently verified episode whose evidence
          references are re-confirmed against the CURRENT index is adopted
          (stale / unverified / failed episodes are recorded as rejections);
        * any memory failure degrades to ``memory_unavailable`` and the
          normal workflow continues.
        """

        instruction = text.splitlines()[0] if text else ""
        refs: dict[str, Any] = {}
        try:
            category = classify_task(instruction)
            refs["task_category"] = category

            skill_decision = None
            if self._skills is not None:
                skill_decision = self._skills.evaluate(
                    task_category=category, task_text=instruction
                )
                refs["skill_decision"] = skill_decision.decision
                if skill_decision.skill_id:
                    refs["skill_id"] = skill_decision.skill_id
                if skill_decision.reasons:
                    refs["skill_reasons"] = list(skill_decision.reasons)
            skill_rejected = skill_decision is not None and skill_decision.decision in {
                "skill_precondition_failed",
                "skill_revoked",
                "skill_disabled",
                "skill_not_verified",
                "skill_input_mismatch",
            }

            if proposal.action != "abstain":
                # Current controller decision takes precedence over reuse.
                if skill_rejected:
                    return None, skill_decision.decision, refs
                return None, "reuse_not_needed", refs

            # The controller abstained: a verified, fresh procedure MAY fill
            # the gap, still fully validated and independently verified.
            if (
                self._skills is not None
                and skill_decision is not None
                and skill_decision.decision == "skill_adoptable"
                and skill_decision.skill is not None
            ):
                skill = skill_decision.skill
                step = skill.action_sequence[0]
                try:
                    adopted = self._controller._tool_proposal(
                        step.action,
                        dict(step.arguments),
                        f"verified skill {skill.id}: {skill.purpose}",
                        notes=(f"adopted verified skill {skill.id} ({SKILL_POLICY_VERSION})",),
                        origin="skill",
                        provenance="verified_skill",
                    )
                    return adopted, "skill_adopted", refs
                except Exception as exc:
                    # A skill that no longer validates must not block episodes.
                    refs["skill_adoption_error"] = str(exc)[:200]

            if self._memory is None:
                fallback = skill_decision.decision if skill_decision else "memory_no_match"
                return None, fallback, refs

            candidates = self._memory.retrieve(instruction, category=category, limit=5)
            refs["retrieved"] = [
                {
                    "id": cand.episode.id,
                    "matched": cand.matched,
                    "authoritative": cand.authoritative,
                    "warning": cand.warning,
                }
                for cand in candidates
            ]
            exact = [cand for cand in candidates if cand.matched == "exact_signature"]
            if not exact:
                if skill_rejected:
                    return None, skill_decision.decision, refs
                return None, "memory_no_match", refs

            rejection: str | None = None
            for cand in exact:
                episode = cand.episode
                if episode.action not in TOOL_ACTIONS:
                    continue  # terminal episodes cannot replace an abstain with a tool
                if not cand.authoritative:
                    reason = (
                        "memory_rejected_unverified"
                        if episode.outcome == "unverified"
                        else "memory_rejected_failed"
                    )
                    rejection = rejection or reason
                    continue
                fresh, problems = self._memory.confirm_evidence(episode)
                if not fresh:
                    refs["stale_reasons"] = problems[:3]
                    rejection = "memory_rejected_stale"
                    continue
                if not self._allow_memory_adoption:
                    # Deliberate-rejection mode: retrieval happened, adoption
                    # is refused by policy (benchmark arm 4).
                    return None, "memory_rejected_forced", refs
                arguments = dict(episode.provenance.get("arguments") or {})
                try:
                    adopted = self._controller._tool_proposal(
                        episode.action,
                        arguments,
                        f"verified episode {episode.id}: previously verified outcome",
                        notes=(f"adopted verified episode {episode.id} ({MEMORY_POLICY_VERSION})",),
                        origin="memory",
                        provenance="verified_episode",
                    )
                except Exception as exc:
                    refs["episode_adoption_error"] = str(exc)[:200]
                    rejection = rejection or "memory_rejected_stale"
                    continue
                refs["episode_id"] = episode.id
                refs["occurrence_count"] = episode.occurrence_count
                return adopted, "memory_adopted", refs

            decision = rejection or (
                skill_decision.decision if skill_rejected else "memory_no_match"
            )
            return None, decision, refs
        except Exception as exc:  # missing/corrupt/incompatible memory layer
            return None, "memory_unavailable", {"error": type(exc).__name__}


def _explain(
    task: str,
    action: str,
    execution: Any,
    verification_status: str,
    verification_reason: str,
) -> str:
    """One short user-facing sentence, grounded in what actually happened."""

    if action == "ask_clarification":
        return "I need one more detail before this task can be answered safely."
    if action == "abstain":
        return "I could not complete this task from the indexed metadata alone."
    if not execution.executed:
        detail = execution.failure_reason or execution.validation_outcome
        return f"The proposed {action} was not executed: {detail}"
    if verification_status == "verified":
        return f"Executed a read-only {action}; the result was verified against the index."
    if verification_status == "incomplete":
        return (
            f"Executed a read-only {action}, but completion could not be "
            f"fully established: {verification_reason}"
        )
    return (
        f"Executed a read-only {action}, but the result could not be "
        f"independently verified: {verification_reason}"
    )
