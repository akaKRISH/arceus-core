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
from dataclasses import dataclass, field
from typing import Any

from arceus_core.controller import CognitiveController
from arceus_core.executor import ProposalExecutor, TaskStatus
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
        }


class TaskWorkflow:
    """One task -> one validated proposal -> at most one tool execution."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        controller: CognitiveController,
        *,
        task_id_prefix: str = "wf",
    ) -> None:
        self._conn = conn
        self._controller = controller
        self._executor = ProposalExecutor(conn)
        self._counter = 0
        self._prefix = task_id_prefix

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

        proposal, trace = self._controller.decide_with_trace(text)
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
        return WorkflowResult(
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
        )


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
