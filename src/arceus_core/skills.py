"""Phase 6: declarative verified-skill registry over the read-only allow-list.

Conservative first release (see SPRINT_STATUS Phase 6 and the phase report):

* Skills are reviewable records inserted through ``SkillRegistry.register``
  — there is no model-generated or autonomous skill-creation path. Every
  action and its literal arguments are re-validated with
  ``controller.validate_arguments`` at registration, and only the read-only
  allow-list actions are accepted. Anything else raises ``MemoryStoreError``.
* The bounded workflow executes at most ONE tool action per task, so a skill
  registration requires exactly one action step; multi-step sequences are
  rejected rather than silently truncated.
* A skill is ``candidate`` until it earns promotion under the explicit,
  tested minimum validation policy: ``MIN_VERIFIED_RUNS`` DISTINCT successful
  runs, each independently verified (``verified=1``), with ZERO recorded
  failures. A model writing a skill, or a single successful action, can
  never satisfy this policy.
* Selection re-checks declarative preconditions against the CURRENT index at
  recall time (``entry_present`` / ``root_active`` / ``category_is``), so a
  revoked skill or a precondition that no longer holds is rejected
  observably instead of replayed.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from arceus_core.controller import READ_ONLY_ACTIONS, validate_arguments
from arceus_core.errors import MemoryStoreError
from arceus_core.graph import rel_key_for
from arceus_core.memory import normalize_task_signature

SKILL_POLICY_VERSION = "skill-policy-v1"
MIN_VERIFIED_RUNS = 3  # explicit promotion policy: distinct verified runs
MAX_STEPS = 1  # bounded workflow executes at most one tool action per task

SkillStatus = Literal["candidate", "verified", "disabled", "revoked"]
PRECONDITION_KINDS = frozenset({"entry_present", "root_active", "category_is"})

_SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class SkillAction:
    action: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Skill:
    """One declarative, reviewable skill record."""

    id: str
    version: int
    purpose: str
    supported_categories: tuple[str, ...]
    #: Declared input contract: normalized task signatures this skill serves.
    #: Empty means legacy category-gated selection (records registered before
    #: schema v3); the Phase 6.1 frozen suites always declare signatures.
    accepted_task_signatures: tuple[str, ...]
    preconditions: tuple[dict[str, Any], ...]
    action_sequence: tuple[SkillAction, ...]
    required_permissions: tuple[str, ...]
    input_limits: dict[str, int]
    expected_outputs: str
    verification_procedure: tuple[str, ...]
    evaluation_evidence: tuple[int, ...]
    known_failures: tuple[str, ...]
    provenance: str
    status: SkillStatus
    status_reason: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "purpose": self.purpose,
            "supported_categories": list(self.supported_categories),
            "accepted_task_signatures": list(self.accepted_task_signatures),
            "preconditions": [dict(p) for p in self.preconditions],
            "action_sequence": [
                {"action": step.action, "arguments": dict(step.arguments)}
                for step in self.action_sequence
            ],
            "required_permissions": list(self.required_permissions),
            "input_limits": dict(self.input_limits),
            "expected_outputs": self.expected_outputs,
            "verification_procedure": list(self.verification_procedure),
            "evaluation_evidence": list(self.evaluation_evidence),
            "known_failures": list(self.known_failures),
            "provenance": self.provenance,
            "status": self.status,
            "status_reason": self.status_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class SkillDecision:
    """Observable selection outcome — including every rejection reason."""

    decision: Literal[
        "skill_adoptable",
        "skill_precondition_failed",
        "skill_revoked",
        "skill_disabled",
        "skill_not_verified",
        "skill_no_match",
        "skill_input_mismatch",
    ]
    skill_id: str | None = None
    reasons: tuple[str, ...] = ()
    skill: Skill | None = None


def _validate_registration(
    *,
    skill_id: str,
    version: int,
    purpose: str,
    supported_categories: tuple[str, ...],
    accepted_task_signatures: tuple[str, ...],
    preconditions: tuple[dict[str, Any], ...],
    action_sequence: tuple[SkillAction, ...],
    required_permissions: tuple[str, ...],
    input_limits: dict[str, int],
    expected_outputs: str,
    verification_procedure: tuple[str, ...],
    provenance: str,
) -> None:
    if not _SKILL_ID_RE.match(skill_id):
        raise MemoryStoreError("skill id must match ^[a-z0-9][a-z0-9-]{2,63}$ (lowercase, stable)")
    if version < 1:
        raise MemoryStoreError("skill version must be >= 1")
    if not purpose.strip() or len(purpose) > 300:
        raise MemoryStoreError("skill purpose must be 1..300 chars")
    if not supported_categories or any(not c.strip() for c in supported_categories):
        raise MemoryStoreError("skill needs at least one non-empty supported category")
    if len(accepted_task_signatures) > 20:
        raise MemoryStoreError("skill accepts at most 20 declared task signatures")
    for sig in accepted_task_signatures:
        if not sig or len(sig) > 300:
            raise MemoryStoreError(
                "accepted task signatures must be non-empty and <= 300 chars after normalization"
            )
    if len(provenance.strip()) < 5 or len(provenance) > 300:
        raise MemoryStoreError("skill provenance must describe how the skill was created")
    if not verification_procedure:
        raise MemoryStoreError("skill must declare an independent verification procedure")
    if not expected_outputs.strip():
        raise MemoryStoreError("skill must declare expected outputs")
    if required_permissions != ("read_only",):
        raise MemoryStoreError("skills may only require the 'read_only' permission")

    # Bounded workflow boundary: exactly one tool action per skill.
    if not (1 <= len(action_sequence) <= MAX_STEPS):
        raise MemoryStoreError(
            f"skill action_sequence must contain exactly {MAX_STEPS} step(s); "
            "the bounded workflow executes at most one tool action per task"
        )
    for step in action_sequence:
        if step.action not in READ_ONLY_ACTIONS:
            raise MemoryStoreError(
                f"skill action {step.action!r} is outside the read-only allow-list"
            )
        if step.action in {"ask_clarification", "abstain"}:
            raise MemoryStoreError("skills execute tools; terminal decisions are not skill steps")
        try:
            validate_arguments(step.action, dict(step.arguments))
        except Exception as exc:  # controller validation is authoritative
            raise MemoryStoreError(f"invalid skill arguments: {exc}") from exc

    for precondition in preconditions:
        kind = precondition.get("kind")
        if kind not in PRECONDITION_KINDS:
            raise MemoryStoreError(f"unknown precondition kind {kind!r}")
        if kind == "entry_present" and not isinstance(precondition.get("rel_path"), str):
            raise MemoryStoreError("entry_present precondition needs a rel_path")
        if (
            kind == "root_active"
            and precondition.get("label") is not None
            and not isinstance(precondition.get("label"), str)
        ):
            raise MemoryStoreError("root_active label must be a string or null")
        if kind == "category_is" and not isinstance(precondition.get("categories"), list):
            raise MemoryStoreError("category_is precondition needs a categories list")

    for key in ("max_task_chars",):
        value = input_limits.get(key)
        if not isinstance(value, int) or value < 1:
            raise MemoryStoreError(f"input_limits.{key} must be a positive integer")


def _row_to_skill(row: sqlite3.Row) -> Skill:
    try:
        categories = tuple(json.loads(row["supported_categories"]))
        accepted = tuple(str(x) for x in json.loads(row["accepted_task_signatures"]))
        preconditions = tuple(json.loads(row["preconditions"]))
        steps = tuple(
            SkillAction(action=step["action"], arguments=dict(step["arguments"]))
            for step in json.loads(row["action_sequence"])
        )
        permissions = tuple(json.loads(row["required_permissions"]))
        limits = dict(json.loads(row["input_limits"]))
        evidence = tuple(int(x) for x in json.loads(row["evaluation_evidence"]))
        known = tuple(str(x) for x in json.loads(row["known_failures"]))
        procedure = tuple(str(x) for x in json.loads(row["verification_procedure"]))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise MemoryStoreError(f"corrupt skill record {row['id']!r}: {exc}") from exc
    return Skill(
        id=str(row["id"]),
        version=int(row["version"]),
        purpose=str(row["purpose"]),
        supported_categories=categories,
        accepted_task_signatures=accepted,
        preconditions=preconditions,
        action_sequence=steps,
        required_permissions=permissions,
        input_limits=limits,
        expected_outputs=str(row["expected_outputs"]),
        verification_procedure=procedure,
        evaluation_evidence=evidence,
        known_failures=known,
        provenance=str(row["provenance"]),
        status=row["status"],
        status_reason=str(row["status_reason"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


class SkillRegistry:
    """SQLite-backed registry with an explicit promotion policy."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # -- registration -------------------------------------------------------

    def register(
        self,
        *,
        id: str,
        purpose: str,
        supported_categories: tuple[str, ...] | list[str],
        accepted_task_signatures: tuple[str, ...] | list[str] = (),
        action_sequence: list[dict[str, Any]] | tuple[SkillAction, ...],
        verification_procedure: tuple[str, ...] | list[str],
        provenance: str,
        preconditions: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
        required_permissions: tuple[str, ...] = ("read_only",),
        input_limits: dict[str, int] | None = None,
        expected_outputs: str = "indexed metadata result matching the declared action",
        evaluation_evidence: tuple[int, ...] | list[int] = (),
        known_failures: tuple[str, ...] | list[str] = (),
        version: int = 1,
    ) -> Skill:
        steps = tuple(
            step
            if isinstance(step, SkillAction)
            else SkillAction(action=step["action"], arguments=dict(step.get("arguments", {})))
            for step in action_sequence
        )
        limits = dict(input_limits or {"max_task_chars": 2000})
        accepted = tuple(
            sig
            for sig in (normalize_task_signature(task) for task in accepted_task_signatures)
            if sig
        )
        _validate_registration(
            skill_id=id,
            version=version,
            purpose=purpose,
            supported_categories=tuple(supported_categories),
            accepted_task_signatures=accepted,
            preconditions=tuple(preconditions),
            action_sequence=steps,
            required_permissions=tuple(required_permissions),
            input_limits=limits,
            expected_outputs=expected_outputs,
            verification_procedure=tuple(verification_procedure),
            provenance=provenance,
        )
        if self.get(id) is not None:
            raise MemoryStoreError(
                f"skill {id!r} already exists (records are reviewable, versioned)"
            )

        now = _utc_now()
        try:
            self._conn.execute(
                "INSERT INTO skills (id, version, purpose, supported_categories, "
                "accepted_task_signatures, preconditions, "
                "action_sequence, required_permissions, input_limits, expected_outputs, "
                "verification_procedure, evaluation_evidence, known_failures, provenance, "
                "status, status_reason, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate', '', ?, ?)",
                (
                    id,
                    version,
                    purpose,
                    json.dumps(list(supported_categories), ensure_ascii=False),
                    json.dumps(list(accepted), ensure_ascii=False),
                    json.dumps([dict(p) for p in preconditions], ensure_ascii=False),
                    json.dumps(
                        [{"action": s.action, "arguments": dict(s.arguments)} for s in steps],
                        ensure_ascii=False,
                    ),
                    json.dumps(list(required_permissions)),
                    json.dumps(limits),
                    expected_outputs,
                    json.dumps(list(verification_procedure), ensure_ascii=False),
                    json.dumps([int(x) for x in evaluation_evidence]),
                    json.dumps([str(x) for x in known_failures], ensure_ascii=False),
                    provenance,
                    now,
                    now,
                ),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            raise MemoryStoreError(f"cannot store skill {id!r}: {exc}") from exc
        skill = self.get(id)
        assert skill is not None
        return skill

    # -- inspection ---------------------------------------------------------

    def get(self, skill_id: str) -> Skill | None:
        row = self._conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
        if row is None:
            return None
        try:
            return _row_to_skill(row)
        except MemoryStoreError:
            return None

    def list(self, status: SkillStatus | None = None) -> tuple[Skill, ...]:
        query = "SELECT * FROM skills"
        params: tuple[Any, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY id"
        skills: list[Skill] = []
        for row in self._conn.execute(query, params):
            try:
                skills.append(_row_to_skill(row))
            except MemoryStoreError:
                continue
        return tuple(skills)

    # -- lifecycle ----------------------------------------------------------

    def promote(self, skill_id: str) -> tuple[bool, str | None]:
        """Apply the explicit minimum-validation policy.

        Promotion requires status=candidate, at least MIN_VERIFIED_RUNS
        distinct runs with verified=1, and zero recorded failures.
        Returns ``(promoted, reason_if_not)``.
        """

        skill = self.get(skill_id)
        if skill is None:
            raise MemoryStoreError(f"no skill {skill_id!r}")
        if skill.status != "candidate":
            return False, f"skill is {skill.status}, not candidate"
        row = self._conn.execute(
            "SELECT COUNT(*) AS runs, COALESCE(SUM(verified), 0) AS verified "
            "FROM skill_runs WHERE skill_id = ?",
            (skill_id,),
        ).fetchone()
        runs, verified = int(row["runs"]), int(row["verified"])
        if runs < MIN_VERIFIED_RUNS:
            return False, f"needs {MIN_VERIFIED_RUNS} distinct verified runs, has {runs}"
        if verified != runs:
            return False, f"{runs - verified} recorded run(s) were not verified"
        self._set_status(skill_id, "verified", "promotion policy satisfied")
        return True, None

    def set_status(self, skill_id: str, status: SkillStatus, reason: str) -> Skill:
        allowed: dict[str, set[str]] = {
            "candidate": set(),  # only promotion may leave candidate
            "verified": {"verified", "disabled", "revoked"},
            "disabled": {"verified", "revoked"},  # re-enable requires prior verification
            "revoked": set(),  # revocation is terminal
        }
        skill = self.get(skill_id)
        if skill is None:
            raise MemoryStoreError(f"no skill {skill_id!r}")
        if status not in allowed[skill.status]:
            raise MemoryStoreError(
                f"illegal skill transition {skill.status} -> {status} for {skill_id!r}"
            )
        if not reason.strip():
            raise MemoryStoreError("a status change needs an explicit reason")
        self._set_status(skill_id, status, reason)
        updated = self.get(skill_id)
        assert updated is not None
        return updated

    def _set_status(self, skill_id: str, status: SkillStatus, reason: str) -> None:
        self._conn.execute(
            "UPDATE skills SET status = ?, status_reason = ?, updated_at = ? WHERE id = ?",
            (status, reason, _utc_now(), skill_id),
        )
        self._conn.commit()

    # -- run evidence -------------------------------------------------------

    def record_run(
        self, skill_id: str, *, episode_id: int | None, verified: bool, outcome: str
    ) -> None:
        """Record one execution as promotion evidence.

        Deduplicated on (skill_id, episode_id): replaying the same episode
        cannot inflate the evidence count.
        """

        if self.get(skill_id) is None:
            raise MemoryStoreError(f"no skill {skill_id!r}")
        try:
            self._conn.execute(
                "INSERT OR IGNORE INTO skill_runs "
                "(skill_id, episode_id, ran_at, verified, outcome) "
                "VALUES (?, ?, ?, ?, ?)",
                (skill_id, episode_id, _utc_now(), int(verified), outcome),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            raise MemoryStoreError(f"cannot record skill run: {exc}") from exc

    def run_stats(self, skill_id: str) -> dict[str, int]:
        row = self._conn.execute(
            "SELECT COUNT(*) AS runs, COALESCE(SUM(verified), 0) AS verified "
            "FROM skill_runs WHERE skill_id = ?",
            (skill_id,),
        ).fetchone()
        return {"runs": int(row["runs"]), "verified": int(row["verified"])}

    # -- preconditions & selection ------------------------------------------

    def check_preconditions(
        self, skill: Skill, *, task_category: str, task_text: str
    ) -> tuple[bool, list[str]]:
        """Evaluate declarative preconditions against the CURRENT state."""

        failures: list[str] = []
        max_chars = int(skill.input_limits.get("max_task_chars", 2000))
        if len(task_text) > max_chars:
            failures.append(f"task exceeds input limit ({len(task_text)} > {max_chars})")
        if task_category not in skill.supported_categories:
            failures.append(f"category {task_category!r} not in {list(skill.supported_categories)}")
        for precondition in skill.preconditions:
            kind = precondition.get("kind")
            if kind == "category_is":
                allowed = [str(c) for c in precondition.get("categories", [])]
                if task_category not in allowed:
                    failures.append(f"category {task_category!r} not in precondition {allowed}")
            elif kind == "root_active":
                label = precondition.get("label")
                if label is None:
                    row = self._conn.execute(
                        "SELECT status FROM roots ORDER BY id LIMIT 1"
                    ).fetchone()
                else:
                    row = self._conn.execute(
                        "SELECT status FROM roots WHERE label = ? ORDER BY id LIMIT 1",
                        (label,),
                    ).fetchone()
                if row is None:
                    failures.append(f"root {label!r} is not registered")
                elif str(row["status"]) != "active":
                    failures.append(f"root {label!r} is marked missing")
            elif kind == "entry_present":
                rel_path = str(precondition["rel_path"])
                entry = self._conn.execute(
                    "SELECT 1 FROM entries e JOIN roots r ON r.id = e.root_id "
                    "WHERE e.rel_key = ? AND e.status = 'present' AND r.status = 'active' "
                    "LIMIT 1",
                    (_rel_key(rel_path),),
                ).fetchone()
                if entry is None:
                    failures.append(f"{rel_path} is not present in the active index")
        return (not failures), failures

    def evaluate(self, *, task_category: str, task_text: str) -> SkillDecision:
        """Observable skill-selection decision, including every rejection.

        Input contract first: a skill that declares accepted task signatures
        only ever matches tasks whose normalized signature it serves, even if
        the category matches and every precondition would pass. Records
        without declared signatures are category-gated (legacy schema-v2
        behavior).
        """

        category_matches = [
            skill for skill in self.list() if task_category in skill.supported_categories
        ]
        if not category_matches:
            return SkillDecision(decision="skill_no_match")

        signature = normalize_task_signature(task_text)
        matches: list[Skill] = []
        input_rejected: list[Skill] = []
        for skill in category_matches:
            declared = skill.accepted_task_signatures
            if not declared or signature in declared:
                matches.append(skill)
            else:
                input_rejected.append(skill)
        if not matches:
            # Every category match declares inputs that exclude this task:
            # the input contract, not the task, decides eligibility.
            skill = input_rejected[0]
            return SkillDecision(
                decision="skill_input_mismatch",
                skill_id=skill.id,
                reasons=(f"task signature {signature!r} is not in the skill's accepted inputs",),
                skill=skill,
            )

        verified = [s for s in matches if s.status == "verified"]
        for skill in verified:
            passed, failures = self.check_preconditions(
                skill, task_category=task_category, task_text=task_text
            )
            if passed:
                return SkillDecision(decision="skill_adoptable", skill_id=skill.id, skill=skill)
        if verified:
            skill = verified[0]
            _, failures = self.check_preconditions(
                skill, task_category=task_category, task_text=task_text
            )
            return SkillDecision(
                decision="skill_precondition_failed",
                skill_id=skill.id,
                reasons=tuple(failures),
                skill=skill,
            )
        for status, decision in (
            ("revoked", "skill_revoked"),
            ("disabled", "skill_disabled"),
            ("candidate", "skill_not_verified"),
        ):
            for skill in matches:
                if skill.status == status:
                    return SkillDecision(
                        decision=decision,
                        skill_id=skill.id,
                        reasons=(skill.status_reason or f"skill is {status}",),
                        skill=skill,
                    )
        return SkillDecision(decision="skill_no_match")


def _rel_key(rel_path: str) -> str:
    return rel_key_for(rel_path)
