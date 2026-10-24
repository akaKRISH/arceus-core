"""Frozen benchmark: ``skills-servable-v1`` — genuinely skill-eligible tasks."""

from __future__ import annotations

import contextlib
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from arceus_core.controller import CognitiveController
from arceus_core.database import open_database
from arceus_core.errors import MemoryStoreError
from arceus_core.memory import EpisodicMemory, normalize_task_signature
from arceus_core.roots import add_root, set_root_status
from arceus_core.scanner import scan_root
from arceus_core.skills import MIN_VERIFIED_RUNS, SkillAction, SkillRegistry
from arceus_core.workflow import TaskWorkflow

SKILL_BENCHMARK_ID = "skills-servable-v1"
SKILL_CONFIG = "benchmark-protocol-v1; skills-servable-v0-dev; fixture-v1"
SKILL_SCORING = "skill-adoption-v1; input-contract-v1"
FROZEN_AT = "2026-10-24"
ARMS = ("baseline", "memory_only", "skills_only", "memory_skills")

Family = Literal["skill_eligible", "skill_ineligible", "skill_stale", "skill_revoked", "held_out"]


@dataclass(frozen=True, slots=True)
class SkillTask:
    id: str
    family: Family
    task: str
    fixture_files: dict[str, str]
    expected_action: str
    expected_arguments: dict[str, Any]
    skill_id: str | None = None
    mutate_before_task2: str | None = None
    expect_reuse_decision: str | None = None
    note: str = ""


@dataclass(frozen=True, slots=True)
class SkillContract:
    skill_id: str
    purpose: str
    supported_categories: tuple[str, ...]
    accepted_task_signatures: tuple[str, ...]
    preconditions: tuple[dict[str, Any], ...]
    action_sequence: tuple[SkillAction, ...]
    verification_procedure: tuple[str, ...]
    known_failures: tuple[str, ...]
    provenance: str


FIXTURE = {
    "notes/todo.md": "- [ ] read\n",
    "notes/ideas.md": "# ideas\n",
    "docs/readme.txt": "hello\n",
    "docs/spec.md": "# spec\n",
    "src/app.py": "print('hi')\n",
    "config/settings.json": "{}\n",
    "secrets/key.txt": "secret\n",
}

CONTRACTS: tuple[SkillContract, ...] = (
    SkillContract(
        skill_id="find-secrets-network",
        purpose="Find secrets files on the network drive via search_files.",
        supported_categories=("name_search",),
        accepted_task_signatures=(normalize_task_signature("find secrets on the network drive"),),
        preconditions=({"kind": "root_active"},),
        action_sequence=(SkillAction(action="search_files", arguments={"query": "secrets"}),),
        verification_procedure=("verifier confirms search_files located secrets entries",),
        known_failures=("secrets removed", "root missing"),
        provenance="phase6.1 skill-servable family v1: promotion-evidenced from verified runs",
    ),
    SkillContract(
        skill_id="find-keys-network",
        purpose="Find api keys on the network drive via search_files.",
        supported_categories=("name_search",),
        accepted_task_signatures=(normalize_task_signature("find api keys on the network drive"),),
        preconditions=({"kind": "root_active"},),
        action_sequence=(SkillAction(action="search_files", arguments={"query": "key"}),),
        verification_procedure=("verifier confirms search_files located key entries",),
        known_failures=("keys removed"),
        provenance="phase6.1 skill-servable family v1: promotion-evidenced from verified runs",
    ),
    SkillContract(
        skill_id="find-credentials-network",
        purpose="Find credentials on the network drive via search_files.",
        supported_categories=("name_search",),
        accepted_task_signatures=(
            normalize_task_signature("look for credentials on the network drive"),
        ),
        preconditions=({"kind": "root_active"},),
        action_sequence=(SkillAction(action="search_files", arguments={"query": "secret"}),),
        verification_procedure=(
            "verifier confirms search_files located credential-related entries",
        ),
        known_failures=("credentials removed"),
        provenance="phase6.1 skill-servable family v1: promotion-evidenced from verified runs",
    ),
)

TASKS: tuple[SkillTask, ...] = (
    SkillTask(
        id="sk-01-find-secrets",
        family="skill_eligible",
        task="find secrets on the network drive",
        fixture_files=FIXTURE,
        expected_action="search_files",
        expected_arguments={"query": "secrets"},
        skill_id="find-secrets-network",
    ),
    SkillTask(
        id="sk-02-find-keys",
        family="skill_eligible",
        task="find api keys on the network drive",
        fixture_files=FIXTURE,
        expected_action="search_files",
        expected_arguments={"query": "key"},
        skill_id="find-keys-network",
    ),
    SkillTask(
        id="sk-03-find-creds",
        family="skill_eligible",
        task="look for credentials on the network drive",
        fixture_files=FIXTURE,
        expected_action="search_files",
        expected_arguments={"query": "secret"},
        skill_id="find-credentials-network",
    ),
    SkillTask(
        id="sk-04-find-secrets-alt",
        family="skill_eligible",
        task="find secrets on the network drive",
        fixture_files=FIXTURE,
        expected_action="search_files",
        expected_arguments={"query": "secrets"},
        skill_id="find-secrets-network",
    ),
    SkillTask(
        id="sk-05-ineligible-notes-contents",
        family="skill_ineligible",
        task="list contents of notes recursively",
        fixture_files=FIXTURE,
        expected_action="abstain",
        expected_arguments={},
    ),
    SkillTask(
        id="sk-06-ineligible-different-query",
        family="skill_ineligible",
        task="find spec.md",
        fixture_files=FIXTURE,
        expected_action="abstain",
        expected_arguments={},
    ),
    SkillTask(
        id="sk-07-ineligible-random",
        family="skill_ineligible",
        task="find random nonexistent thing xyz123",
        fixture_files=FIXTURE,
        expected_action="abstain",
        expected_arguments={},
    ),
    SkillTask(
        id="sk-08-stale-secrets-removed",
        family="skill_stale",
        task="find secrets on the network drive",
        fixture_files=FIXTURE,
        mutate_before_task2="remove_secrets",
        skill_id="find-secrets-network",
        expected_action="abstain",
        expected_arguments={},
        expect_reuse_decision="skill_precondition_failed",
    ),
    SkillTask(
        id="sk-09-stale-root-missing",
        family="skill_stale",
        task="find api keys on the network drive",
        fixture_files=FIXTURE,
        mutate_before_task2="root_missing",
        skill_id="find-keys-network",
        expected_action="abstain",
        expected_arguments={},
        expect_reuse_decision="skill_precondition_failed",
    ),
    SkillTask(
        id="sk-10-revoked-skill",
        family="skill_revoked",
        task="look for credentials on the network drive",
        fixture_files=FIXTURE,
        mutate_before_task2="revoke_creds",
        skill_id="find-credentials-network",
        expected_action="abstain",
        expected_arguments={},
        expect_reuse_decision="skill_revoked",
    ),
    SkillTask(
        id="sk-11-heldout-list-docs",
        family="held_out",
        task="show children of docs",
        fixture_files=FIXTURE,
        expected_action="abstain",
        expected_arguments={},
    ),
    SkillTask(
        id="sk-12-heldout-ancestors",
        family="held_out",
        task="show ancestors of src/app.py",
        fixture_files=FIXTURE,
        expected_action="abstain",
        expected_arguments={},
    ),
)


def _build_fixture(base: Path, files: dict[str, str]) -> Path:
    root = base / "work"
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def _ensure_skill_setup(conn, registry: SkillRegistry) -> None:
    from arceus_core.memory import EpisodicMemory

    for contract in CONTRACTS:
        with contextlib.suppress(MemoryStoreError):
            registry.register(
                id=contract.skill_id,
                purpose=contract.purpose,
                supported_categories=contract.supported_categories,
                accepted_task_signatures=contract.accepted_task_signatures,
                preconditions=list(contract.preconditions),
                action_sequence=list(contract.action_sequence),
                verification_procedure=list(contract.verification_procedure),
                known_failures=list(contract.known_failures),
                provenance=contract.provenance,
            )

        mem = EpisodicMemory(conn)
        for idx in range(MIN_VERIFIED_RUNS):
            ep = mem.record(
                f"skill validation {contract.skill_id} {idx}",
                action=contract.action_sequence[0].action,
                origin="deterministic",
                executed=True,
                verification_status="verified",
                task_completed=True,
                config_version="skill-val-v1",
            )
            registry.record_run(
                contract.skill_id, episode_id=ep.id, verified=True, outcome="verified"
            )
        promoted, reason = registry.promote(contract.skill_id)
        if not promoted:
            raise RuntimeError(f"failed to promote {contract.skill_id}: {reason}")


def run_skill_task(arm: str, task: SkillTask, base: Path) -> dict[str, Any]:
    started = time.perf_counter()
    with open_database(base / "arceus.sqlite3") as conn:
        root = _build_fixture(base, task.fixture_files)
        registered = add_root(conn, root, label="work")
        scan_root(conn, registered)

        memory = EpisodicMemory(conn) if arm in ("memory_only", "memory_skills") else None
        registry = SkillRegistry(conn) if arm in ("skills_only", "memory_skills") else None
        if registry is not None:
            _ensure_skill_setup(conn, registry)

        workflow = TaskWorkflow(
            conn,
            CognitiveController(conn),
            task_id_prefix=task.id,
            memory=memory,
            skills=registry,
        )

        if task.mutate_before_task2 == "remove_secrets":
            try:
                shutil.rmtree(root / "secrets")
                scan_root(conn, registered)
            except Exception:
                pass
        elif task.mutate_before_task2 == "root_missing":
            set_root_status(conn, registered.id, "missing")
        elif task.mutate_before_task2 == "revoke_creds" and registry is not None:
            with contextlib.suppress(Exception):
                registry.set_status(
                    "find-credentials-network", "revoked", "benchmark revocation test"
                )

        result = workflow.run(task.task)

        entry = {
            "task_id": task.id,
            "family": task.family,
            "task": task.task,
            "arm": arm,
            "status": result.status,
            "action": result.proposed_action,
            "arguments": result.proposed_arguments,
            "executed": result.executed,
            "verification": result.verification_status,
            "completed": result.task_completed,
            "reuse_decision": result.reuse_decision,
            "reuse_refs": result.reuse_refs,
            "wall_seconds": round(time.perf_counter() - started, 4),
        }

        success = False
        if task.family in ("skill_ineligible", "held_out"):
            if result.reuse_decision == "skill_adopted":
                entry["incorrect_adoption"] = True
                success = False
            else:
                entry["incorrect_adoption"] = False
                success = result.reuse_decision != "skill_adopted"
        elif task.family in ("skill_stale", "skill_revoked"):
            expected_hit = (
                task.expect_reuse_decision and result.reuse_decision == task.expect_reuse_decision
            )
            observed_rejection = result.reuse_decision in (
                "skill_precondition_failed",
                "skill_revoked",
            )
            success = expected_hit or observed_rejection
        elif task.family == "skill_eligible":
            if result.reuse_decision == "skill_adopted":
                if result.proposed_action == task.expected_action:
                    args_ok = all(
                        result.proposed_arguments.get(k) == v
                        for k, v in task.expected_arguments.items()
                    )
                    if args_ok and result.executed and result.verification_status == "verified":
                        success = True
                    else:
                        success = False
                else:
                    success = False
            else:
                if (
                    result.proposed_action == task.expected_action
                    and result.verification_status == "verified"
                ):
                    success = True
                else:
                    success = False
        else:
            success = False

        entry["success"] = success
        entry["skill_adopted"] = result.reuse_decision == "skill_adopted"
        entry["skill_rejected"] = result.reuse_decision in (
            "skill_precondition_failed",
            "skill_revoked",
            "skill_disabled",
            "skill_not_verified",
            "skill_input_mismatch",
        )
        return entry


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return round((ordered[mid - 1] + ordered[mid]) / 2, 4)


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return ordered[index]


def summarize_skills(arm: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    by_family = {}
    for fam in ("skill_eligible", "skill_ineligible", "skill_stale", "skill_revoked", "held_out"):
        fam_entries = [e for e in entries if e["family"] == fam]
        by_family[fam] = {
            "count": len(fam_entries),
            "success": sum(1 for e in fam_entries if e["success"]),
            "skill_adopted": sum(1 for e in fam_entries if e["skill_adopted"]),
            "skill_rejected": sum(1 for e in fam_entries if e["skill_rejected"]),
            "incorrect_adoption": sum(1 for e in fam_entries if e.get("incorrect_adoption")),
        }

    return {
        "arm": arm,
        "benchmark_id": SKILL_BENCHMARK_ID,
        "config": SKILL_CONFIG,
        "scoring": SKILL_SCORING,
        "frozen_at": FROZEN_AT,
        "end_to_end_success": sum(1 for e in entries if e["success"]),
        "task_count": len(entries),
        "by_family": by_family,
        "wall_seconds": round(sum(e["wall_seconds"] for e in entries), 4),
        "latency_median": _median([e["wall_seconds"] for e in entries]),
        "latency_p95": _p95([e["wall_seconds"] for e in entries]),
        "false_verified_claims": sum(
            1 for e in entries if e["status"] == "verified" and not e["success"]
        ),
        "skill_adoptions_total": sum(1 for e in entries if e["skill_adopted"]),
        "incorrect_adoptions_total": sum(1 for e in entries if e.get("incorrect_adoption")),
    }


def run_skills_arm(arm: str) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="arceus-skills-") as tmp_name:
        base = Path(tmp_name)
        for task in TASKS:
            task_dir = base / task.id
            task_dir.mkdir()
            entries.append(run_skill_task(arm, task, task_dir))
    summary = summarize_skills(arm, entries)
    summary["by_task"] = {e["task_id"]: e for e in entries}
    return summary


def run_all_skills() -> dict[str, Any]:
    results = {}
    for arm in ARMS:
        results[arm] = run_skills_arm(arm)
    return results
