"""Safe execution boundary for already-validated controller proposals.

Phase 5 milestone: turn validated proposals into observable, evidence-backed
read-only tool executions. The executor is deliberately separate from
planning (``controller.py``) and from verification (``verifier.py``):

* Only ``search_files`` / ``graph_children`` / ``graph_ancestors`` execute.
  ``ask_clarification`` and ``abstain`` are terminal decisions, not tools.
* Every action passes the SAME application-level validation
  (``controller.validate_arguments``) no matter which backend proposed it.
* Authorization comes from the registered-roots registry only
  (``controller.resolve_proposed_path``); a model's assertion is never
  authorization.
* Tool results come from the existing metadata index services
  (``search.search``, ``graph.children``, ``graph.ancestors``). No file
  contents are read, nothing is written, no processes are started.
* Evidence is bounded index metadata (paths, names, types, observation
  status) — never file contents or secrets.

The executor does not loop: one call executes at most one validated action.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Literal

from arceus_core.controller import (
    Proposal,
    resolve_proposed_path,
    validate_arguments,
)
from arceus_core.errors import (
    ControllerValidationError,
    PathResolutionError,
    QueryError,
    RootError,
)
from arceus_core.graph import ancestors as graph_ancestors_of
from arceus_core.graph import children as graph_children_of
from arceus_core.graph import lookup_entry
from arceus_core.models import AncestorLink, Entry, SearchHit
from arceus_core.roots import get_root
from arceus_core.search import SearchFilters, search

TOOL_ACTIONS = frozenset({"search_files", "graph_children", "graph_ancestors"})
TERMINAL_ACTIONS = frozenset({"ask_clarification", "abstain"})

# The controller adds registry-resolved DERIVED detail to tool proposals
# after validation (root_id / absolute_path). The tools consume only the
# contract keys, so the executor strips these before re-validating — this
# is semantics-preserving and authorization is re-derived from the path and
# the roots registry inside the executor (never from the proposal's claim).
CONTROLLER_DERIVED_ARGUMENT_KEYS = frozenset({"root_id", "absolute_path"})

TaskStatus = Literal[
    "proposed",
    "rejected",
    "executed",
    "verified",
    "incomplete",
    "clarification_required",
    "abstained",
    "failed",
]

VerificationStatus = Literal["verified", "unverified", "incomplete"]
Origin = Literal["model", "deterministic", "memory", "skill"]

MAX_EVIDENCE_ITEMS = 50


def _entry_evidence(entry: Entry) -> dict[str, Any]:
    """Bounded, content-free metadata record for one indexed entry."""

    return {
        "root_id": entry.root_id,
        "rel_path": entry.rel_path,
        "parent_rel_path": entry.parent_rel_path,
        "name": entry.name,
        "entry_type": entry.entry_type,
        "status": entry.status,
        "observed_at": entry.observed_at,
        "last_seen_run_id": entry.last_seen_run_id,
    }


def _hit_evidence(hit: SearchHit) -> dict[str, Any]:
    evidence = _entry_evidence(hit.entry)
    evidence.update(
        {
            "root_path": hit.root_path,
            "absolute_path": hit.absolute_path,
            "match_score": hit.score,
            "match_reason": hit.reason,
        }
    )
    return evidence


def _ancestor_evidence(link: AncestorLink) -> dict[str, Any]:
    return {
        "rel_path": link.rel_path,
        "display_name": link.display_name,
        "observed": link.observed,
        "entry_type": link.entry_type,
    }


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Audit record of one bounded execution attempt.

    ``status`` is task-level state (proposed/rejected/executed/verified/
    incomplete/clarification_required/abstained/failed). Origin and
    provenance are explicit data from the proposal — never derived from
    notes or wording. ``evidence`` holds bounded metadata only.
    """

    task_id: str
    proposed_action: str
    origin: Origin
    status: TaskStatus
    validation_outcome: str
    executed: bool = False
    tool_result: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    verification_status: VerificationStatus | None = None
    verification_reason: str | None = None
    task_completed: bool | None = None
    failure_reason: str | None = None
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "origin": self.origin,
            "proposed_action": self.proposed_action,
            "status": self.status,
            "validation_outcome": self.validation_outcome,
            "executed": self.executed,
            "tool_result": self.tool_result,
            "evidence": list(self.evidence),
            "warnings": list(self.warnings),
            "verification_status": self.verification_status,
            "verification_reason": self.verification_reason,
            "task_completed": self.task_completed,
            "failure_reason": self.failure_reason,
            "rationale": self.rationale,
        }


class ProposalExecutor:
    """Execute at most one validated read-only action per call."""

    def __init__(
        self, conn: sqlite3.Connection, *, evidence_limit: int = MAX_EVIDENCE_ITEMS
    ) -> None:
        self._conn = conn
        if not 1 <= evidence_limit <= 1000:
            raise ValueError("evidence_limit must be between 1 and 1000")
        self._evidence_limit = evidence_limit

    # -- validation -------------------------------------------------------

    def _terminal_result(
        self, task_id: str, proposal: Proposal, terminal: TaskStatus
    ) -> ExecutionResult:
        """Clarify/abstain terminate the workflow: recorded, never executed."""

        return ExecutionResult(
            task_id=task_id,
            proposed_action=proposal.action,
            origin=proposal.origin,
            status=terminal,
            validation_outcome="terminal decision (not executable)",
            executed=False,
            task_completed=None,
            rationale=proposal.rationale,
        )

    # -- execution ----------------------------------------------------------

    def execute(self, task_id: str, proposal: Proposal) -> ExecutionResult:
        """Validate and execute one proposal. Never modifies the filesystem.

        The proposal is re-validated against the application contract even if
        it was validated in the controller, so a caller passing a constructed
        proposal gets the same guarantees.
        """

        if proposal.action in TERMINAL_ACTIONS:
            status: TaskStatus = (
                "clarification_required" if proposal.action == "ask_clarification" else "abstained"
            )
            return self._terminal_result(task_id, proposal, status)

        if proposal.action not in TOOL_ACTIONS:
            return ExecutionResult(
                task_id=task_id,
                proposed_action=proposal.action,
                origin=proposal.origin,
                status="rejected",
                validation_outcome=f"action {proposal.action!r} is not an executable tool",
                executed=False,
                task_completed=False,
                failure_reason=f"action {proposal.action!r} is not on the execution allow-list",
                rationale=proposal.rationale,
            )

        arguments = {
            key: value
            for key, value in proposal.arguments.items()
            if key not in CONTROLLER_DERIVED_ARGUMENT_KEYS
        }
        try:
            validated = validate_arguments(proposal.action, arguments)
        except ControllerValidationError as exc:
            return self._rejected(task_id, proposal, str(exc))
        try:
            return self._dispatch(task_id, proposal, validated)
        except (
            ControllerValidationError,
            PathResolutionError,
            QueryError,
            RootError,
        ) as exc:
            return self._rejected(task_id, proposal, str(exc))
        except Exception as exc:  # unexpected tool failure — never a fake success
            return ExecutionResult(
                task_id=task_id,
                proposed_action=proposal.action,
                origin=proposal.origin,
                status="failed",
                validation_outcome="validated",
                executed=False,
                task_completed=False,
                failure_reason=f"execution error: {exc}",
                rationale=proposal.rationale,
            )

    def _rejected(self, task_id: str, proposal: Proposal, reason: str) -> ExecutionResult:
        return ExecutionResult(
            task_id=task_id,
            proposed_action=proposal.action,
            origin=proposal.origin,
            status="rejected",
            validation_outcome=reason,
            executed=False,
            task_completed=False,
            failure_reason=reason,
            rationale=proposal.rationale,
        )

    # -- tool dispatch ------------------------------------------------------

    def _root_context(self, root_id: int) -> dict[str, Any]:
        root = get_root(self._conn, root_id)
        return {"root_id": root.id, "root_label": root.label, "root_path": root.path}

    def _dispatch(
        self, task_id: str, proposal: Proposal, arguments: dict[str, Any]
    ) -> ExecutionResult:
        if proposal.action == "search_files":
            return self._execute_search(task_id, proposal, arguments)
        if proposal.action == "graph_children":
            return self._execute_children(task_id, proposal, arguments)
        if proposal.action == "graph_ancestors":
            return self._execute_ancestors(task_id, proposal, arguments)
        raise ControllerValidationError(f"unreachable dispatch for {proposal.action!r}")

    def _execute_search(
        self, task_id: str, proposal: Proposal, arguments: dict[str, Any]
    ) -> ExecutionResult:
        query = str(arguments["query"])
        limit = int(arguments.get("limit", 20))
        # Scope from the registry only: a resolved root_id (validated in the
        # controller against the roots table) narrows the search; no id means
        # the authorized scope is the whole registry.
        filters = SearchFilters(limit=limit, root_id=proposal.root_id)
        result = search(self._conn, query, filters)
        evidence = [_hit_evidence(hit) for hit in result.hits[: self._evidence_limit]]
        tool_result = {
            "query": query,
            "limit": limit,
            "hit_count": len(result.hits),
            "truncated_evidence": len(result.hits) > len(evidence),
        }
        return ExecutionResult(
            task_id=task_id,
            proposed_action=proposal.action,
            origin=proposal.origin,
            status="executed",
            validation_outcome="validated",
            executed=True,
            tool_result=tool_result,
            evidence=evidence,
            warnings=list(result.warnings),
            rationale=proposal.rationale,
        )

    def _execute_children(
        self, task_id: str, proposal: Proposal, arguments: dict[str, Any]
    ) -> ExecutionResult:
        resolved = resolve_proposed_path(
            self._conn, str(arguments["path"]), arguments.get("root_label")
        )
        entries = graph_children_of(self._conn, resolved.root.id, resolved.rel_path)
        evidence = [_entry_evidence(entry) for entry in entries[: self._evidence_limit]]
        requested_present = lookup_entry(self._conn, resolved.root.id, resolved.rel_path)
        tool_result = {
            **self._root_context(resolved.root.id),
            "path": resolved.rel_path,
            "requested_path_indexed": requested_present is not None,
            "requested_path_status": None
            if requested_present is None
            else requested_present.status,
            "child_count": len(entries),
            "truncated_evidence": len(entries) > len(evidence),
        }
        return ExecutionResult(
            task_id=task_id,
            proposed_action=proposal.action,
            origin=proposal.origin,
            status="executed",
            validation_outcome="validated",
            executed=True,
            tool_result=tool_result,
            evidence=evidence,
            rationale=proposal.rationale,
        )

    def _execute_ancestors(
        self, task_id: str, proposal: Proposal, arguments: dict[str, Any]
    ) -> ExecutionResult:
        label = arguments.get("root_label")
        resolved = resolve_proposed_path(self._conn, str(arguments["path"]), label)
        links = graph_ancestors_of(self._conn, resolved)
        evidence = [_ancestor_evidence(link) for link in links[: self._evidence_limit]]
        target_present = lookup_entry(self._conn, resolved.root.id, resolved.rel_path)
        tool_result = {
            **self._root_context(resolved.root.id),
            "path": resolved.rel_path,
            "target_indexed": target_present is not None,
            "target_status": None if target_present is None else target_present.status,
            "ancestor_count": len(links),
            "truncated_evidence": len(links) > len(evidence),
        }
        return ExecutionResult(
            task_id=task_id,
            proposed_action=proposal.action,
            origin=proposal.origin,
            status="executed",
            validation_outcome="validated",
            executed=True,
            tool_result=tool_result,
            evidence=evidence,
            rationale=proposal.rationale,
        )
