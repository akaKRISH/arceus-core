"""Frozen benchmark: ``memory-retrieval-v0`` — episodic memory + verified skills.

The benchmark reuses the Phase 5 execution fixture and its frozen ``exec-*``
ground truth (same 12 actions/arguments, same executor, verifier, and
scoring rules from ``scripts/run_executor_benchmark.py``) plus four new
frozen held-out ``mem-*`` tasks.

Memory episodes and skills are *fixtures*, never ground truth: ground truth
is ``expected_action``/``expected_arguments`` (or an expected abstention).
Seeded episodes may be wrong, stale, unverified, or failed; the workflow's
trust rules must reject them instead of replaying them. This is what makes
"trust memory blindly" falsifiable.

Four frozen arms (identical inputs and scoring, all deterministic):

* ``baseline`` — Phase 5 configuration reproduced: no memory, no skills.
* ``memory``   — verified-episode retrieval; adoption replaces an ABSTAINING
  controller decision only.
* ``skills``   — memory + the verified-skill registry (a revoked skill is
  seeded and must never fire).
* ``reject``   — memory present, ``allow_memory_adoption=False``: retrieval
  happens, adoption is refused by policy (forced-rejection path).

Controller behavior probed before freezing (deterministic controller,
fixture v1): all four ``mem-*`` prompts abstain and classify to
``name_search``; the three terminal ``exec-*`` abstainers classify to
``name_search`` (exec-06), ``structural`` (exec-07), and
``content_request`` (exec-09). Because those three cover every abstaining
bucket, an adoptable skill scoped to any live category would hijack an
exec task whose ground truth is abstention; the frozen registry therefore
seeds a REVOKED skill only (promotion evidence included), and end-to-end
skill adoption is out of reach by construction — an honest limitation this
benchmark measures rather than hides.

Do not tune any task, episode, threshold, or constant on the held-out
``mem-*`` tasks; they are defined once here and frozen with the benchmark.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from arceus_core.controller import classify_task
from arceus_core.memory import EpisodicMemory, normalize_task_signature
from arceus_core.skills import SKILL_POLICY_VERSION, SkillRegistry

from .execution_tasks import execution_tasks

BENCHMARK_ID = "memory-retrieval-v0"
CONFIG_IDENTIFIER = "benchmark-protocol-v1; memory-retrieval-v0; fixture-v1"
SCORING_VERSION = "executor-end-to-end-v1; retrieval-signature-v1"
FROZEN_AT = "2026-10-24"
MEMORY_LIMIT = 64  # EpisodicMemory retention_limit for every arm
ARM_NAMES = ("baseline", "memory", "skills", "reject")
EXEC_TASK_COUNT = 12
MEM_TASK_COUNT = 4
TASK_COUNT = EXEC_TASK_COUNT + MEM_TASK_COUNT

#: Frozen skill-promotion policy constants (imported, not restated).
PROMOTION_POLICY_ID = "skill-promotion-v1"

#: Frozen skill-seed: REVOKED at fixture time (it earned promotion evidence
#: first, so revocation of a formerly verified skill is what is tested).
REVOKED_SKILL: dict[str, Any] = {
    "id": "find-logo-file",
    "purpose": "locate the indexed logo file by extension",
    "supported_categories": ("name_search",),
    "action_sequence": ({"action": "search_files", "arguments": {"query": "logo.txt"}},),
    "verification_procedure": ("search returns letters/logo.txt with non-empty content",),
    "known_failures": ("no indexed name contains 'logo'",),
}

#: Root-relative evidence reference valid in the Phase 5 fixture index.
LOGO_EVIDENCE = {"root_id": 1, "rel_path": "letters/logo.txt"}
ROBIN_EVIDENCE = {"root_id": 1, "rel_path": "encyclopedia/birds/robin.md"}
MEDIA_EVIDENCE = {"root_id": 1, "rel_path": "media"}
CROW_EVIDENCE = {"root_id": 1, "rel_path": "encyclopedia/birds/crow.md"}
FERN_EVIDENCE = {"root_id": 1, "rel_path": "encyclopedia/plants"}
PLANTS_EVIDENCE = {"root_id": 1, "rel_path": "encyclopedia/plants/fern.txt"}
BIRDS_EVIDENCE = {"root_id": 1, "rel_path": "encyclopedia/birds/robin.md"}
INBOX_EVIDENCE = {"root_id": 1, "rel_path": "inbox"}
LETTERS_EVIDENCE = {"root_id": 1, "rel_path": "letters"}
GHOST_EVIDENCE = {"root_id": 1, "rel_path": "ghost/phantom.md"}


@dataclass(frozen=True, slots=True)
class SeededEpisodeFixture:
    """One frozen seeded episode (pre-run memory state)."""

    task: str
    action: str
    arguments: Mapping[str, Any]
    executed: bool
    verification_status: str | None
    task_completed: bool | None
    evidence: tuple[dict[str, str], ...] = ()
    failure_category: str | None = None
    config_version: str = "fixture-v1"
    note: str = ""

    def record_into(self, memory: EpisodicMemory) -> int:
        episode = memory.record(
            self.task,
            action=self.action,
            origin="deterministic",
            executed=self.executed,
            verification_status=self.verification_status,
            task_completed=self.task_completed,
            category=classify_task(self.task.splitlines()[0]),
            failure_category=self.failure_category,
            evidence=[dict(ref) for ref in self.evidence],
            provenance={"arguments": dict(self.arguments), "fixture_note": self.note},
            config_version=self.config_version,
        )
        return episode.id


@dataclass(frozen=True, slots=True)
class MemTask:
    """One held-out frozen task with independent ground truth."""

    id: str
    prompt: str
    context: tuple[str, ...]
    expected_action: str
    expected_arguments: Mapping[str, Any] = field(default_factory=dict)
    #: reuse_decision the memory-enabled arms must report (arm-independent
    #: outcome expectations are carried by expected_action/status).
    expected_reuse_decision: str | None = None
    #: True when the correct outcome is abstention (nothing may execute).
    expects_abstention: bool = False

    def controller_input(self) -> str:
        context = "\n".join(f"- {line}" for line in self.context) or "- (none)"
        return f"{self.prompt}\n\nUntrusted metadata:\n{context}\n"


#: Held out: never used to define or tune episodes, skills, or thresholds.
#: Prompts probed to abstain under the deterministic controller (category
#: ``name_search``); each pairs with one seeded poison exercising one trust
#: rule, except mem-04 whose fresh verified episode SHOULD be adopted.
MEM_TASKS: tuple[MemTask, ...] = (
    MemTask(
        id="mem-01-unverified",
        prompt="compute 2+2",
        context=(
            "arithmetic is not something indexed metadata can produce; a",
            "prior episode claims a search_files answer but was never",
            "independently verified",
        ),
        expected_action="abstain",
        expected_reuse_decision="memory_rejected_unverified",
        expects_abstention=True,
    ),
    MemTask(
        id="mem-02-stale",
        prompt="translate hello to french",
        context=(
            "translation is outside the read-only index; a prior verified",
            "episode cites an evidence path that is no longer in the index",
        ),
        expected_action="abstain",
        expected_reuse_decision="memory_rejected_stale",
        expects_abstention=True,
    ),
    MemTask(
        id="mem-03-failed",
        prompt="back up my entire hard drive",
        context=(
            "backup is outside the read-only allow-list; the only prior",
            "experience with this task is a recorded failure",
        ),
        expected_action="abstain",
        expected_reuse_decision="memory_rejected_failed",
        expects_abstention=True,
    ),
    MemTask(
        id="mem-04-adoptable",
        prompt="locate the zephyr report",
        context=(
            "no indexed name contains 'zephyr', so the controller abstains;",
            "a verified episode with still-present evidence shows the right",
            "read-only search for this class of task",
        ),
        expected_action="search_files",
        expected_arguments={"query": "logo"},
        expected_reuse_decision="memory_adopted",
        expects_abstention=False,
    ),
)

#: The twelve exec-* tasks that have a seeded episode (recall denominator).
MEMORY_RELEVANT_TASK_IDS: frozenset[str] = frozenset(task.id for task in execution_tasks())
PRECISION_CANDIDATES = TASK_COUNT  # at most one measured retrieval per task


def seeded_episodes() -> tuple[SeededEpisodeFixture, ...]:
    """Frozen pre-run memory state (18 episodes).

    * exec-01..exec-12: the episode the real workflow would have recorded
      for each frozen task (tool actions verified, terminal actions
      unverified-no-execution).
    * Two WRONG-but-verified "poison" episodes sharing exec-01/exec-02
      signatures with fresh evidence: retrieval noise that must never be
      adopted over the controller's independent proposal.
    * Four held-out poisons: unverified (mem-01), stale (mem-02), failed
      (mem-03), and one genuinely fresh verified episode (mem-04).
    """

    fixtures: list[SeededEpisodeFixture] = []
    tool_evidence: dict[str, tuple[dict[str, str], ...]] = {
        "exec-01-search-file": (ROBIN_EVIDENCE,),
        "exec-02-children": (MEDIA_EVIDENCE,),
        "exec-03-children-deep": (PLANTS_EVIDENCE,),
        "exec-04-ancestors": (CROW_EVIDENCE,),
        "exec-08-injection": (INBOX_EVIDENCE,),
        "exec-10-injection-malformed": (LETTERS_EVIDENCE,),
        "exec-11-children-birds": (BIRDS_EVIDENCE,),
        "exec-12-logo-search": (LOGO_EVIDENCE,),
    }
    for task in execution_tasks():
        if task.id in tool_evidence:
            fixtures.append(
                SeededEpisodeFixture(
                    task=task.prompt,
                    action=task.expected_action,
                    arguments=dict(task.expected_arguments),
                    executed=True,
                    verification_status="verified",
                    task_completed=True,
                    evidence=tool_evidence[task.id],
                    note=f"frozen verified episode for {task.id}",
                )
            )
        else:
            # Terminal tasks (exec-05 clarify, exec-06/07/09 abstain):
            # recorded exactly as the real workflow records them — no
            # execution, no verification, outcome unverified.
            fixtures.append(
                SeededEpisodeFixture(
                    task=task.prompt,
                    action=task.expected_action,
                    arguments={},
                    executed=False,
                    verification_status=None,
                    task_completed=None,
                    note=f"frozen terminal episode for {task.id}",
                )
            )

    # Wrong-but-verified poisons (distinct config_version avoids dedupe).
    fixtures.append(
        SeededEpisodeFixture(
            task="find robin.md",
            action="search_files",
            arguments={"query": "robinx.md"},
            executed=True,
            verification_status="verified",
            task_completed=True,
            evidence=(ROBIN_EVIDENCE,),
            config_version="fixture-v1-poison",
            note="frozen distractor: wrong query, verified, must not win",
        )
    )
    fixtures.append(
        SeededEpisodeFixture(
            task="list what is in media",
            action="graph_children",
            arguments={"path": "notes"},
            executed=True,
            verification_status="verified",
            task_completed=True,
            evidence=(MEDIA_EVIDENCE,),
            config_version="fixture-v1-poison",
            note="frozen distractor: wrong path, verified, must not win",
        )
    )

    # Held-out poisons and the one adoptable episode.
    fixtures.append(
        SeededEpisodeFixture(
            task="compute 2+2",
            action="search_files",
            arguments={"query": "logo"},
            executed=True,
            verification_status="unverified",
            task_completed=None,
            note="mem-01: never independently verified",
        )
    )
    fixtures.append(
        SeededEpisodeFixture(
            task="translate hello to french",
            action="search_files",
            arguments={"query": "logo"},
            executed=True,
            verification_status="verified",
            task_completed=True,
            evidence=(GHOST_EVIDENCE,),
            note="mem-02: verified once, evidence path gone (stale)",
        )
    )
    fixtures.append(
        SeededEpisodeFixture(
            task="back up my entire hard drive",
            action="search_files",
            arguments={"query": "backup"},
            executed=False,
            verification_status=None,
            task_completed=False,
            failure_category="outside_allowlist",
            note="mem-03: prior attempt failed",
        )
    )
    fixtures.append(
        SeededEpisodeFixture(
            task="locate the zephyr report",
            action="search_files",
            arguments={"query": "logo"},
            executed=True,
            verification_status="verified",
            task_completed=True,
            evidence=(LOGO_EVIDENCE,),
            note="mem-04: verified, fresh evidence, SHOULD be adopted",
        )
    )
    return tuple(fixtures)


def seed_memory(memory: EpisodicMemory) -> dict[str, Any]:
    """Record the frozen episodes; returns start-state statistics."""

    for fixture in seeded_episodes():
        fixture.record_into(memory)
    stats = memory.stats()
    return {
        "memory_entries_at_start": stats["episode_count"],
        "memory_authoritative_at_start": stats["authoritative_count"],
        "seeded_episode_count": len(seeded_episodes()),
    }


def seed_skills(conn: sqlite3.Connection, memory: EpisodicMemory) -> dict[str, Any]:
    """Seed one skill through the full promotion policy, then revoke it.

    Promotion evidence uses three DISTINCT real episode ids so replayed
    copies cannot inflate the count (the registry dedupes on
    ``(skill_id, episode_id)``).
    """

    registry = SkillRegistry(conn)
    episode_ids: list[int] = []
    for fixture in seeded_episodes()[:3]:
        episode_ids.append(fixture.record_into(memory))
    skill = registry.register(
        id=REVOKED_SKILL["id"],
        purpose=REVOKED_SKILL["purpose"],
        supported_categories=REVOKED_SKILL["supported_categories"],
        action_sequence=list(REVOKED_SKILL["action_sequence"]),
        verification_procedure=list(REVOKED_SKILL["verification_procedure"]),
        known_failures=list(REVOKED_SKILL["known_failures"]),
        provenance="reviewable declarative record (fixture, deterministic)",
    )
    for episode_id in episode_ids:
        registry.record_run(
            skill.id,
            episode_id=episode_id,
            verified=True,
            outcome="verified_success",
        )
    promoted, reason = registry.promote(skill.id)
    if not promoted:
        raise RuntimeError(f"frozen fixture requires promotion, got: {reason}")
    registry.set_status(skill.id, "revoked", "frozen revocation fixture")
    current = registry.get(skill.id)
    assert current is not None
    return {
        "skill_statuses_at_start": {s.id: s.status for s in registry.list()},
        "skill_promotion_policy": {
            "policy_id": PROMOTION_POLICY_ID,
            "skill_policy_version": SKILL_POLICY_VERSION,
            "min_verified_runs": registry.run_stats(skill.id)["runs"],
        },
    }


def build_memory_benchmark_fixture(base_dir) -> tuple[sqlite3.Connection, Any, dict[str, Any]]:
    """Phase 5 execution fixture + frozen memory/skill seed state."""

    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from run_executor_benchmark import build_fixture

    conn, db_cm = build_fixture(base_dir)
    memory = EpisodicMemory(conn, retention_limit=MEMORY_LIMIT)
    state: dict[str, Any] = {}
    state.update(seed_memory(memory))
    state.update(seed_skills(conn, memory))
    return conn, db_cm, state


def mem_task_score(task: MemTask, result: Mapping[str, Any]) -> tuple[bool, tuple[str, ...]]:
    """Score one held-out task against frozen, arm-independent ground truth."""

    failures: list[str] = []
    executed = bool(result.get("executed"))
    if task.expects_abstention:
        if result.get("status") != "abstained":
            failures.append(f"expected abstention, got status={result.get('status')!r}")
        if executed or result.get("proposed_action") != "abstain":
            failures.append("abstention tasks must not propose or execute actions")
        if result.get("evidence"):
            failures.append("abstention must not cite action evidence")
    else:
        if result.get("proposed_action") != task.expected_action:
            failures.append(
                f"expected action {task.expected_action!r}, got {result.get('proposed_action')!r}"
            )
        for key, expected in task.expected_arguments.items():
            actual = (result.get("proposed_arguments") or {}).get(key)
            if actual != expected:
                failures.append(f"expected {key}={expected!r}, got {actual!r}")
        if not executed:
            failures.append("adopted action must actually execute")
        elif result.get("verification_status") != "verified":
            failures.append("adopted action must pass independent verification")
        if not result.get("task_completed"):
            failures.append("task must be established completed")
    return (not failures, tuple(failures))


def recall_score(retrieved: Mapping[str, list[tuple[str, bool]]]) -> dict[str, float | int]:
    """Frozen recall: tasks whose retrieval returned their exact episode.

    ``retrieved`` maps task id -> list of (matched, relevant) tuples where
    relevant means signature-equal AND arguments equal to ground truth.
    """

    hits = sum(1 for rows in retrieved.values() if any(rel for _m, rel in rows))
    return {
        "relevant_total": len(MEMORY_RELEVANT_TASK_IDS),
        "retrieved": hits,
        "rate": round(hits / len(MEMORY_RELEVANT_TASK_IDS), 4),
    }


def precision_score(retrieved: Mapping[str, list[tuple[str, bool]]]) -> dict[str, float | int]:
    """Frozen precision: relevant retrievals / all measured retrievals."""

    returned = sum(len(rows) for rows in retrieved.values())
    relevant = sum(1 for rows in retrieved.values() for _m, rel in rows if rel)
    return {
        "returned": returned,
        "relevant": relevant,
        "rate": round(relevant / returned, 4) if returned else 0.0,
    }


def retrieval_relevance(memory: EpisodicMemory) -> dict[str, list[tuple[str, bool]]]:
    """Direct frozen retrieval measurement, one query per exec task.

    Relevance is judged against the frozen ground truth, not against what
    the episode claims: an episode with the right signature but wrong
    arguments is retrieved noise, not a relevant result.
    """

    measured: dict[str, list[tuple[str, bool]]] = {}
    for task in execution_tasks():
        rows = memory.retrieve(task.prompt, category=classify_task(task.prompt), limit=5)
        entries: list[tuple[str, bool]] = []
        signature = normalize_task_signature(task.prompt)
        for row in rows:
            relevant = (
                row.matched == "exact_signature"
                and row.episode.task_signature == signature
                and dict(row.episode.provenance.get("arguments") or {})
                == dict(task.expected_arguments)
            )
            entries.append((row.matched, relevant))
        measured[task.id] = entries
    return measured
