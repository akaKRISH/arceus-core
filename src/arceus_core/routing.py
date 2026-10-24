"""Phase 7: Budgeted Cognitive Routing (BCR) — explicit, auditable strategy choice.

Design rules (see SPRINT_STATUS Phase 7 and the phase report):

* The router is a thin, deterministic policy layered on the EXISTING
  controller, executor, verifier, memory, and skill services. It never
  executes anything itself and never widens the read-only allow-list. Its
  only new powers are: (a) consulting the deterministic controller for a
  proposal *without* a model attached, and (b) deciding whether a real model
  call is warranted and affordable.
* Ordering is deterministic-first and strictly bounded: a safe terminal
  (unsafe / content / no-evidence) is answered without a model; a
  well-supported deterministic tool proposal is used directly; a verified
  skill whose preconditions hold may fill an abstention gap; an authoritative,
  still-current episodic-memory record may fill it next; only then is a real
  model consulted, and only when the configured policy and the measured
  resource snapshot both permit it.
* A routing decision is explicit data: the selected strategy, a machine
  reason, evidence/precondition references, the *configured* cost budget
  (clearly distinguished from measured cost), model-eligibility, whether the
  decision is terminal, and the policy version. Model-generated confidence is
  never a routing signal.
* Every model-proposed action still crosses the same controller contract,
  permission, executor, and independent-verifier boundaries as before. When
  the model is unavailable, over budget, or its proposal is rejected, the
  router records an explicit fallback and the reason — never a silent success.
* Resource readings carry their source and timestamp, and are labelled
  ``measured``. Budgets are ``configured`` (policy) or ``measured`` (actual);
  the two are never conflated.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Literal

from arceus_core.abstention import AbstentionReason, coerce_reason, escalation_class
from arceus_core.controller import CognitiveController, classify_task
from arceus_core.errors import InferenceError
from arceus_core.executor import TOOL_ACTIONS
from arceus_core.memory import EpisodicMemory
from arceus_core.skills import SkillRegistry

#: Phase 7 policy: escalate on ANY deterministic abstention.
ROUTING_POLICY_VERSION_V1 = "bcr-policy-v1"
#: Phase 7.1 policy: escalate only on a recognized recoverable interpretation gap.
ROUTING_POLICY_VERSION_V2 = "bcr-policy-v2"
ROUTING_POLICY_VERSION = ROUTING_POLICY_VERSION_V2

EscalationMode = Literal["any", "recoverable"]

Strategy = Literal[
    "deterministic",
    "verified_skill",
    "memory",
    "model",
    "clarify",
    "abstain",
]

#: Strategies that terminate the task without executing a tool action.
TERMINAL_STRATEGIES = frozenset({"clarify", "abstain"})

MIN_FREE_RAM_MB = 750.0


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    """A point-in-time resource reading, labelled with source and time.

    ``free_ram_mb`` is ``measured`` when a platform probe succeeded and
    ``None`` otherwise (unknown is never reported as healthy). Values are
    estimates of host state, not exact process accounting.
    """

    free_ram_mb: float | None
    source: str
    measured_at: str
    kind: Literal["measured", "unavailable"] = "measured"

    def to_dict(self) -> dict[str, Any]:
        return {
            "free_ram_mb": self.free_ram_mb,
            "source": self.source,
            "measured_at": self.measured_at,
            "kind": self.kind,
        }


def measure_resources() -> ResourceSnapshot:
    """Best-effort free-RAM probe. Never raises; unknown stays unknown."""

    try:
        import ctypes

        class _MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = _MemoryStatusEx()
        stat.dwLength = ctypes.sizeof(_MemoryStatusEx)
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
        if ok:
            return ResourceSnapshot(
                free_ram_mb=stat.ullAvailPhys / (1024 * 1024),
                source="windows.GlobalMemoryStatusEx",
                measured_at=_utc_now(),
            )
    except Exception:
        pass
    try:
        import os

        if hasattr(os, "sysconf") and os.path.exists("/proc/meminfo"):
            with open("/proc/meminfo", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("MemAvailable:"):
                        kb = float(line.split()[1])
                        return ResourceSnapshot(
                            free_ram_mb=kb / 1024,
                            source="linux./proc/meminfo",
                            measured_at=_utc_now(),
                        )
    except Exception:
        pass
    return ResourceSnapshot(
        free_ram_mb=None, source="unavailable", measured_at=_utc_now(), kind="unavailable"
    )


@dataclass(frozen=True, slots=True)
class RoutingBudget:
    """Configured per-task limits. These are policy, never measurements."""

    max_model_calls: int = 1
    max_inference_seconds: float = 60.0
    max_output_tokens: int = 256
    max_execution_actions: int = 1
    memory_candidates: int = 5
    skill_candidates: int = 5
    max_workflow_seconds: float = 120.0
    min_free_ram_mb: float = MIN_FREE_RAM_MB
    #: When True a live model call is refused unless the resource probe
    #: returned a real reading; unknown resources then block the model path.
    require_known_resources: bool = False

    def __post_init__(self) -> None:
        for name in (
            "max_model_calls",
            "max_output_tokens",
            "max_execution_actions",
            "memory_candidates",
            "skill_candidates",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be >= 1")
        for name in ("max_inference_seconds", "max_workflow_seconds"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.min_free_ram_mb < 0:
            raise ValueError("min_free_ram_mb must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_model_calls": self.max_model_calls,
            "max_inference_seconds": self.max_inference_seconds,
            "max_output_tokens": self.max_output_tokens,
            "max_execution_actions": self.max_execution_actions,
            "memory_candidates": self.memory_candidates,
            "skill_candidates": self.skill_candidates,
            "max_workflow_seconds": self.max_workflow_seconds,
            "min_free_ram_mb": self.min_free_ram_mb,
            "require_known_resources": self.require_known_resources,
        }


DEFAULT_BUDGET = RoutingBudget()


class BudgetedInference:
    """Cap one live inference backend to a task's configured budget.

    Wraps rather than mutates the injected ``LocalInference``: every call is
    counted against ``max_model_calls`` and the request's own timeout is
    reduced to ``max_inference_seconds``, so a slow model cannot outlive the
    budget. The controller treats an exhausted budget exactly like any other
    inference failure, so the workflow falls back to the deterministic policy
    and records the reason instead of silently retrying.
    """

    def __init__(self, inner: Any, budget: RoutingBudget = DEFAULT_BUDGET) -> None:
        self._inner = inner
        self._budget = budget
        self.calls = 0

    @property
    def budget(self) -> RoutingBudget:
        return self._budget

    def generate(self, request: Any) -> Any:
        if self.calls >= self._budget.max_model_calls:
            raise InferenceError(
                f"model call budget exhausted (max_model_calls={self._budget.max_model_calls})"
            )
        self.calls += 1
        capped = replace(
            request,
            timeout_seconds=min(request.timeout_seconds, self._budget.max_inference_seconds),
        )
        return self._inner.generate(capped)


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """One explicit, auditable routing outcome."""

    strategy: Strategy
    reason: str
    task_category: str
    terminal: bool
    model_eligible: bool
    policy_version: str
    evidence: dict[str, Any] = field(default_factory=dict)
    #: Configured budget values that shaped this decision (policy, not cost).
    budget_estimate: dict[str, Any] = field(default_factory=dict)
    resource: dict[str, Any] = field(default_factory=dict)
    fallback_reason: str | None = None
    #: Typed reason when this decision is an abstention, else None.
    abstain_reason_code: str | None = None
    #: Whether the POLICY permitted escalation (independent of model/gate
    #: availability). None when the decision is not an abstention.
    escalation_allowed: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "reason": self.reason,
            "task_category": self.task_category,
            "terminal": self.terminal,
            "model_eligible": self.model_eligible,
            "policy_version": self.policy_version,
            "evidence": dict(self.evidence),
            "budget_estimate": dict(self.budget_estimate),
            "resource": dict(self.resource),
            "fallback_reason": self.fallback_reason,
            "abstain_reason_code": self.abstain_reason_code,
            "escalation_allowed": self.escalation_allowed,
        }


class BudgetedRouter:
    """Choose one strategy for a task under a frozen, configured budget.

    The router holds an OPTIONAL real ``LocalInference``-backed controller for
    the model path and a separate deterministic controller for probing. With
    no inference attached the model strategy is ineligible, and the router
    degrades to deterministic / skill / memory / clarify / abstain.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        inference: Any | None = None,
        memory: EpisodicMemory | None = None,
        skills: SkillRegistry | None = None,
        budget: RoutingBudget = DEFAULT_BUDGET,
        resource_probe: Any = measure_resources,
        escalation: EscalationMode = "recoverable",
    ) -> None:
        if escalation not in {"any", "recoverable"}:
            raise ValueError(f"unknown escalation mode {escalation!r}")
        self._conn = conn
        self._probe = resource_probe
        self._budget = budget
        self._memory = memory
        self._skills = skills
        self._escalation = escalation
        self._inference = BudgetedInference(inference, budget) if inference is not None else None
        self._model_controller = (
            CognitiveController(conn, inference=self._inference)
            if self._inference is not None
            else None
        )
        self._det_controller = CognitiveController(conn)

    @property
    def budget(self) -> RoutingBudget:
        return self._budget

    @property
    def escalation(self) -> EscalationMode:
        return self._escalation

    @property
    def policy_version(self) -> str:
        """The frozen policy id that matches this router's escalation mode."""

        return ROUTING_POLICY_VERSION_V1 if self._escalation == "any" else ROUTING_POLICY_VERSION_V2

    @property
    def inference(self) -> BudgetedInference | None:
        return self._inference

    @property
    def model_available(self) -> bool:
        return self._model_controller is not None

    @property
    def model_controller(self) -> CognitiveController | None:
        """The model-attached controller, used by the workflow for strategy=model."""

        return self._model_controller

    def _terminal_decision(
        self,
        *,
        category: str,
        reason: str,
        code: AbstentionReason,
        evidence: dict[str, Any],
        snapshot: ResourceSnapshot,
        budget_estimate: dict[str, Any],
        escalation_allowed: bool = False,
        fallback_reason: str | None = None,
    ) -> RoutingDecision:
        return RoutingDecision(
            strategy="abstain",
            reason=reason,
            task_category=category,
            terminal=True,
            model_eligible=False,
            policy_version=self.policy_version,
            evidence=evidence,
            budget_estimate=budget_estimate,
            resource=snapshot.to_dict(),
            fallback_reason=fallback_reason,
            abstain_reason_code=code.value,
            escalation_allowed=escalation_allowed,
        )

    def _escalation_permitted(
        self, code: AbstentionReason | None, skill_decision: str | None
    ) -> tuple[bool, str]:
        """Policy-only decision: may this abstention be escalated?

        ``recoverable`` mode requires a recognized recoverable reason; a
        missing, unknown, or malformed reason fails closed. A revoked, disabled,
        or precondition-failed skill is terminal too: the model must not route
        around a procedure the registry refused. ``any`` mode preserves the
        frozen Phase 7 behaviour for the baseline arm.
        """

        if self._escalation == "any":
            return True, "policy_v1_any_abstention"
        if skill_decision in {
            "skill_revoked",
            "skill_disabled",
            "skill_precondition_failed",
        }:
            return False, str(skill_decision)
        if code is not None and escalation_class(code) == "recoverable":
            return True, "recoverable_interpretation_gap"
        if code is None:
            return False, "unknown_abstention_reason"
        return False, f"terminal:{code.value}"

    def route(self, task: str) -> RoutingDecision:
        """Select a strategy for ``task``. Pure policy; executes nothing."""

        text = task.strip()
        snapshot = self._probe()
        budget_estimate = self._budget.to_dict()

        if not text or len(text) > 2000:
            return self._terminal_decision(
                category="invalid",
                reason="task is empty or oversized",
                code=AbstentionReason.INVALID_TASK,
                evidence={},
                snapshot=snapshot,
                budget_estimate=budget_estimate,
            )

        instruction = text.splitlines()[0]
        category = classify_task(instruction)

        terminal_for_category = {
            "invalid": AbstentionReason.INVALID_TASK,
            "unsafe_request": AbstentionReason.UNSAFE_REQUEST,
            "content_request": AbstentionReason.CONTENT_UNAVAILABLE,
        }
        if category in terminal_for_category:
            # These can never be fixed by interpretation: the index cannot
            # answer a content question and cannot serve an unsafe request,
            # so a model call could only add risk.
            return self._terminal_decision(
                category=category,
                reason=f"deterministic terminal for category {category}",
                code=terminal_for_category[category],
                evidence={},
                snapshot=snapshot,
                budget_estimate=budget_estimate,
            )

        # 1. Deterministic controller proposal (no model attached).
        proposal, _trace = self._det_controller.decide_with_trace(text)
        evidence: dict[str, Any] = {"deterministic_action": proposal.action}

        # Skill eligibility is evaluated for EVERY routed task so that a
        # revoked / disabled / precondition-failed skill is always observable,
        # matching the workflow's reuse contract. Only the abstain path may
        # ADOPT a skill.
        skill_info = self._skill_decision(instruction, category)
        evidence.update(skill_info["evidence"])

        if proposal.action == "ask_clarification":
            return RoutingDecision(
                strategy="clarify",
                reason="deterministic policy found multiple candidate matches",
                task_category=category,
                terminal=True,
                model_eligible=False,
                policy_version=self.policy_version,
                evidence=evidence,
                budget_estimate=budget_estimate,
                resource=snapshot.to_dict(),
            )

        if proposal.action in TOOL_ACTIONS:
            # Well-supported deterministic case: no reuse or model needed (H1).
            return RoutingDecision(
                strategy="deterministic",
                reason="deterministic controller produced a validated tool proposal",
                task_category=category,
                terminal=False,
                model_eligible=False,
                policy_version=self.policy_version,
                evidence=evidence,
                budget_estimate=budget_estimate,
                resource=snapshot.to_dict(),
            )

        # The deterministic controller abstained. Classify the reason BEFORE
        # any escalation: only a recognized recoverable gap may reach the model.
        code = coerce_reason(proposal.abstain_reason_code)
        evidence["abstain_reason_code"] = code.value if code else None
        evidence["abstain_evidence"] = list(proposal.abstain_evidence)
        evidence["escalation_class"] = escalation_class(code)

        # Reuse precedes escalation: a verified skill or an authoritative,
        # still-current episode is a known procedure, not further
        # interpretation, so it may still replace an abstention.
        if skill_info["decision"] == "skill_adoptable":
            return RoutingDecision(
                strategy="verified_skill",
                reason="a verified skill's preconditions hold for this task",
                task_category=category,
                terminal=False,
                model_eligible=False,
                policy_version=self.policy_version,
                evidence=evidence,
                budget_estimate=budget_estimate,
                resource=snapshot.to_dict(),
                abstain_reason_code=code.value if code else None,
                escalation_allowed=False,
            )

        memory_info = self._memory_decision(instruction, category)
        evidence.update(memory_info["evidence"])
        if memory_info["decision"] == "memory_adoptable":
            return RoutingDecision(
                strategy="memory",
                reason="an authoritative, still-current episode can serve this task",
                task_category=category,
                terminal=False,
                model_eligible=False,
                policy_version=self.policy_version,
                evidence=evidence,
                budget_estimate=budget_estimate,
                resource=snapshot.to_dict(),
                abstain_reason_code=code.value if code else None,
                escalation_allowed=False,
            )

        # 2. Model strategy — only when the policy permits escalation for THIS
        # reason, a model exists, and the budget/resource gate passes.
        permitted, policy_reason = self._escalation_permitted(code, skill_info.get("decision"))
        evidence["escalation_policy"] = policy_reason
        if not permitted:
            reason = (
                f"terminal abstention ({code.value}): model escalation not permitted"
                if code is not None
                else "unknown abstention reason: failing closed without a model call"
            )
            return self._terminal_decision(
                category=category,
                reason=reason,
                code=code or AbstentionReason.UNKNOWN_ABSTENTION_REASON,
                evidence=evidence,
                snapshot=snapshot,
                budget_estimate=budget_estimate,
                escalation_allowed=False,
            )

        model_eligible, model_reason = self._model_gate(snapshot)
        evidence["model_gate"] = model_reason
        if model_eligible:
            reason = (
                "recoverable interpretation gap; bounded model escalation permitted"
                if self._escalation == "recoverable"
                else "deterministic/reuse insufficient; model reasoning warranted"
            )
            return RoutingDecision(
                strategy="model",
                reason=reason,
                task_category=category,
                terminal=False,
                model_eligible=True,
                policy_version=self.policy_version,
                evidence=evidence,
                budget_estimate=budget_estimate,
                resource=snapshot.to_dict(),
                abstain_reason_code=code.value if code else None,
                escalation_allowed=True,
            )

        return self._terminal_decision(
            category=category,
            reason=f"no affordable strategy: {model_reason}",
            code=code or AbstentionReason.UNKNOWN_ABSTENTION_REASON,
            evidence=evidence,
            snapshot=snapshot,
            budget_estimate=budget_estimate,
            escalation_allowed=True,
            fallback_reason=model_reason,
        )

    # -- reuse eligibility --------------------------------------------------

    def _skill_decision(self, instruction: str, category: str) -> dict[str, Any]:
        if self._skills is None:
            return {"decision": "skill_unavailable", "evidence": {"skill": None}}
        try:
            decision = self._skills.evaluate(task_category=category, task_text=instruction)
        except Exception as exc:  # a corrupt registry must not crash routing
            return {
                "decision": "skill_unavailable",
                "evidence": {"skill": {"error": type(exc).__name__}},
            }
        info: dict[str, Any] = {
            "decision": decision.decision,
            "skill": None,
        }
        evidence: dict[str, Any] = {"skill": {"decision": decision.decision}}
        if decision.skill is not None:
            passed, failures = self._skills.check_preconditions(
                decision.skill, task_category=category, task_text=instruction
            )
            info["skill"] = {
                "id": decision.skill.id,
                "status": decision.skill.status,
                "passed": passed,
                "failures": list(failures),
            }
            evidence["skill"] = {**evidence["skill"], **info["skill"]}
        info["evidence"] = evidence
        return info

    def _memory_decision(self, instruction: str, category: str) -> dict[str, Any]:
        if self._memory is None:
            return {"decision": "memory_unavailable", "evidence": {"memory": None}}
        try:
            candidates = self._memory.retrieve(
                instruction, category=category, limit=self._budget.memory_candidates
            )
        except Exception as exc:
            return {
                "decision": "memory_unavailable",
                "evidence": {"memory": {"error": type(exc).__name__}},
            }
        exact = [c for c in candidates if c.matched == "exact_signature"]
        evidence: dict[str, Any] = {
            "memory": {
                "candidates": len(candidates),
                "exact_matches": len(exact),
            }
        }
        for candidate in exact:
            episode = candidate.episode
            if episode.action not in TOOL_ACTIONS:
                continue
            if not candidate.authoritative:
                evidence["memory"].setdefault("rejected", []).append(
                    {"id": episode.id, "reason": f"not authoritative ({episode.warning})"}
                )
                continue
            fresh, problems = self._memory.confirm_evidence(episode)
            if not fresh:
                evidence["memory"].setdefault("rejected", []).append(
                    {"id": episode.id, "reason": "stale evidence"}
                )
                continue
            evidence["memory"]["adoptable_episode"] = episode.id
            return {"decision": "memory_adoptable", "evidence": evidence}
        return {"decision": "memory_no_match", "evidence": evidence}

    # -- model gate ---------------------------------------------------------

    def _model_gate(self, snapshot: ResourceSnapshot) -> tuple[bool, str]:
        if self._model_controller is None:
            return False, "model_unavailable"
        if snapshot.kind == "measured" and snapshot.free_ram_mb is not None:
            if snapshot.free_ram_mb < self._budget.min_free_ram_mb:
                return False, "resource_exhausted"
        elif self._budget.require_known_resources:
            return False, "resources_unknown"
        return True, "within_budget"
