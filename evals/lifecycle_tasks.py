"""Frozen benchmark: ``memory-lifecycle-v1`` — scan, mutate, re-ask.

Answers: does episodic memory stay reliable when the filesystem changes
after an episode was recorded?

Every scenario runs the full lifecycle against its own synthetic fixture:

1. write files, register the root, scan (index built from disk);
2. run task 1 through the bounded workflow (verified read-only outcome,
   written back to memory as an episode with root-relative evidence);
3. mutate the filesystem (remove / rename / relocate / mark root missing)
   or leave it unchanged (control);
4. reconcile the index with a fresh scan (or registry status change);
5. ask task 2 (the original or a changed-path variant) again;
6. score against ground truth derived from the FIXTURE mutation itself —
   never from production retrieval or memory code.

Frozen expectations below were probed against the deterministic controller
on this exact fixture family (deterministic probe, 2026-10-24):

* original re-ask after removal/rename  -> controller abstains
  (``no indexed match``), so the adoption gate runs and stale memory must
  be rejected rather than replayed;
* changed-path variant re-ask            -> controller proposes and the
  result verifies (new correct result);
* unchanged re-ask (control)             -> verifies; evidence still fresh;
* root marked missing / directory moved  -> the action executes but the
  INDEPENDENT VERIFIER refuses to certify it, so no false success may be
  claimed from insufficient current evidence.

Arms (deterministic, no model): ``baseline`` (no memory) vs ``memory``
(EpisodicMemory with write-back and trust rules). The baseline isolates
controller behavior; the difference between arms is what memory adds.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from arceus_core.controller import CognitiveController, classify_task
from arceus_core.database import open_database
from arceus_core.memory import EpisodicMemory, normalize_task_signature
from arceus_core.roots import add_root, set_root_status
from arceus_core.scanner import scan_root
from arceus_core.workflow import TaskWorkflow

LIFECYCLE_BENCHMARK_ID = "memory-lifecycle-v1"
LIFECYCLE_CONFIG = "benchmark-protocol-v1; memory-lifecycle-v0-dev; fixture-v1"
LIFECYCLE_SCORING = "lifecycle-outcome-v1; stale-revalidation-v1"
FROZEN_AT = "2026-10-24"
ARM_NAMES = ("baseline", "memory")
MEMORY_LIMIT = 64

ScenarioOutcome = Literal["abstain", "verified_action", "insufficient_evidence"]

_MOTIF = {"docs/alpha.md": "A", "docs/alpha-old.md": "OLD", "notes/beta.md": "B"}


def _remove_alpha_keep_similar(root: Path) -> None:
    (root / "docs" / "alpha.md").unlink()


def _remove_alpha_clean(root: Path) -> None:
    (root / "docs" / "alpha.md").unlink()
    (root / "docs" / "alpha-old.md").unlink()


def _rename_alpha_gamma(root: Path) -> None:
    (root / "docs" / "alpha.md").rename(root / "docs" / "gamma.md")


def _alpha_to_archive(root: Path) -> None:
    (root / "archive").mkdir(exist_ok=True)
    (root / "docs" / "alpha.md").rename(root / "archive" / "alpha.md")


def _docs_to_nested(root: Path) -> None:
    (root / "nested").mkdir(exist_ok=True)
    (root / "docs").rename(root / "nested" / "docs")


def _no_change(root: Path) -> None:
    return None


_MUTATIONS: dict[str, Callable[[Path], None]] = {
    "remove_alpha_keep_similar": _remove_alpha_keep_similar,
    "remove_alpha_clean": _remove_alpha_clean,
    "rename_alpha_gamma": _rename_alpha_gamma,
    "alpha_to_archive": _alpha_to_archive,
    "docs_to_nested": _docs_to_nested,
    "none": _no_change,
}


@dataclass(frozen=True, slots=True)
class LifecycleScenario:
    id: str
    files: dict[str, str]
    task1: str
    mutation: str
    root_marked_missing: bool
    task2: str
    outcome: ScenarioOutcome
    expected_arguments: dict[str, Any] = field(default_factory=dict)
    expected_action: str | None = None  # for verified_action
    #: confirm_evidence(t1 verified episode) must be False after the change.
    expect_stale_evidence: bool = True
    #: memory arm's task-2 reuse_decision (only when the controller abstains
    #: on task 2, which is when the adoption gate actually runs).
    expect_stale_rejection: bool = False
    note: str = ""


#: Frozen scenario table (probed 2026-10-24; expectations derive from the
#: fixture mutation, not from production code behavior).
SCENARIOS: tuple[LifecycleScenario, ...] = (
    LifecycleScenario(
        id="lc-01-removal-similar-remains",
        files=dict(_MOTIF),
        task1="find alpha.md",
        mutation="remove_alpha_keep_similar",
        root_marked_missing=False,
        task2="find alpha.md",
        outcome="abstain",
        expect_stale_rejection=True,
        note="alpha.md deleted while alpha-old.md remains: stale evidence plus "
        "a superficially similar file; exact re-ask must not replay memory",
    ),
    LifecycleScenario(
        id="lc-02-removal-clean",
        files=dict(_MOTIF),
        task1="find alpha.md",
        mutation="remove_alpha_clean",
        root_marked_missing=False,
        task2="find alpha.md",
        outcome="abstain",
        note="evidence fully gone; abstention must be preserved",
    ),
    LifecycleScenario(
        id="lc-03-rename-original-ask",
        files=dict(_MOTIF),
        task1="find alpha.md",
        mutation="rename_alpha_gamma",
        root_marked_missing=False,
        task2="find alpha.md",
        outcome="abstain",
        expect_stale_rejection=True,
        note="original ask after rename: verified episode is stale",
    ),
    LifecycleScenario(
        id="lc-04-rename-new-name-ask",
        files=dict(_MOTIF),
        task1="find alpha.md",
        mutation="rename_alpha_gamma",
        root_marked_missing=False,
        task2="find gamma.md",
        outcome="verified_action",
        expected_action="search_files",
        expected_arguments={"query": "gamma.md"},
        note="changed-path generalization: the new name must be found fresh",
    ),
    LifecycleScenario(
        id="lc-05-same-name-other-directory",
        files=dict(_MOTIF),
        task1="find alpha.md",
        mutation="alpha_to_archive",
        root_marked_missing=False,
        task2="find alpha.md",
        outcome="verified_action",
        expected_action="search_files",
        expected_arguments={"query": "alpha.md"},
        note="same-named file moved to another directory: current index wins, "
        "the relocated file is the correct fresh result",
    ),
    LifecycleScenario(
        id="lc-06-unchanged-reask-control",
        files=dict(_MOTIF),
        task1="find alpha.md",
        mutation="none",
        root_marked_missing=False,
        task2="find alpha.md",
        outcome="verified_action",
        expected_action="search_files",
        expected_arguments={"query": "alpha.md"},
        expect_stale_evidence=False,
        note="control: nothing changed, evidence must still re-confirm fresh",
    ),
    LifecycleScenario(
        id="lc-07-root-marked-missing",
        files=dict(_MOTIF),
        task1="find alpha.md",
        mutation="none",
        root_marked_missing=True,
        task2="find alpha.md",
        outcome="insufficient_evidence",
        note="registered directory inaccessible (registry status): the "
        "verifier must refuse certification; no false success allowed",
    ),
    LifecycleScenario(
        id="lc-08-parent-directory-moved",
        files={"docs/alpha.md": "A", "docs/beta.md": "B"},
        task1="list what is in docs",
        mutation="docs_to_nested",
        root_marked_missing=False,
        task2="list what is in docs",
        outcome="insufficient_evidence",
        note="parent/child relationship changed: old path no longer indexed, "
        "verifier must refuse certification",
    ),
    LifecycleScenario(
        id="lc-09-parent-directory-moved-new-path",
        files={"docs/alpha.md": "A", "docs/beta.md": "B"},
        task1="list what is in docs",
        mutation="docs_to_nested",
        root_marked_missing=False,
        task2="list what is in nested/docs",
        outcome="verified_action",
        expected_action="graph_children",
        expected_arguments={"path": "nested/docs"},
        note="changed-path generalization for graph actions",
    ),
)


def scenario_score(scenario: LifecycleScenario, result: Any) -> tuple[bool, tuple[str, ...]]:
    """Score one task-2 result against fixture-derived ground truth."""

    failures: list[str] = []
    if scenario.outcome == "abstain":
        if result.status != "abstained":
            failures.append(f"expected abstention, got {result.status!r}")
        if result.executed:
            failures.append("abstention scenario must not execute")
        if result.proposed_action != "abstain":
            failures.append(f"expected abstain, got {result.proposed_action!r}")
    elif scenario.outcome == "verified_action":
        if result.proposed_action != scenario.expected_action:
            failures.append(
                f"expected {scenario.expected_action!r}, got {result.proposed_action!r}"
            )
        for key, expected in scenario.expected_arguments.items():
            actual = (result.proposed_arguments or {}).get(key)
            if actual != expected:
                failures.append(f"expected {key}={expected!r}, got {actual!r}")
        if not result.executed:
            failures.append("expected action must execute")
        elif result.verification_status != "verified":
            failures.append(
                f"expected independent verification, got {result.verification_status!r}"
            )
        elif not result.task_completed:
            failures.append("task must be established completed")
    else:  # insufficient_evidence
        if result.status == "verified":
            failures.append("must not claim verified success from insufficient current evidence")
    return (not failures), tuple(failures)


def _build_scenario_fixture(base: Path, scenario: LifecycleScenario) -> Any:
    """Materialize the scenario's initial files and index them."""

    root = base / "work"
    for relative, content in scenario.files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def run_scenario(arm: str, scenario: LifecycleScenario, base: Path) -> dict[str, Any]:
    """Execute one full scan-mutate-re-ask lifecycle for one arm."""

    with open_database(base / "arceus.sqlite3") as conn:
        root = _build_scenario_fixture(base, scenario)
        registered = add_root(conn, root, label="work")
        scan_root(conn, registered)

        memory: EpisodicMemory | None = None
        if arm == "memory":
            memory = EpisodicMemory(conn, retention_limit=MEMORY_LIMIT)
        workflow = TaskWorkflow(
            conn,
            CognitiveController(conn),
            task_id_prefix=scenario.id,
            memory=memory,
        )
        result1 = workflow.run(scenario.task1)

        _MUTATIONS[scenario.mutation](root)
        if scenario.root_marked_missing:
            set_root_status(conn, registered.id, "missing")
        else:
            scan_root(conn, registered)

        result2 = workflow.run(scenario.task2)

        entry: dict[str, Any] = {
            "scenario_id": scenario.id,
            "mutation": scenario.mutation,
            "task1_status": result1.status,
            "task1_action": result1.proposed_action,
            "task1_verified": result1.verification_status == "verified",
            "status2": result2.status,
            "action2": result2.proposed_action,
            "executed2": result2.executed,
            "verification2": result2.verification_status,
            "reuse_decision2": result2.reuse_decision,
            "wall_seconds": round(result1.wall_seconds + result2.wall_seconds, 4),
        }
        ok, failures = scenario_score(scenario, result2)
        entry["success"] = ok
        entry["failures"] = list(failures)

        if memory is not None:
            stale = _stale_evidence_check(memory, scenario, entry)
            entry["stale_evidence_detected"] = stale
            entry["stale_rejection_observed"] = result2.reuse_decision == "memory_rejected_stale"
            entry["stale_adoption"] = result2.reuse_decision == "memory_adopted"
            entry["memory_stats"] = memory.stats()
        else:
            entry["stale_evidence_detected"] = None
            entry["stale_rejection_observed"] = None
            entry["stale_adoption"] = None
            entry["memory_stats"] = None
        return entry


def _stale_evidence_check(
    memory: EpisodicMemory, scenario: LifecycleScenario, entry: dict[str, Any]
) -> bool | None:
    """Re-confirm the task-1 verified episode against the CURRENT index.

    Returns None when no verified episode exists for task 1 (a fixture
    defect — task 1 must verify for the stale question to be meaningful).
    """

    rows = memory.retrieve(scenario.task1, category=classify_task(scenario.task1), limit=10)
    signature = normalize_task_signature(scenario.task1)
    for row in rows:
        episode = row.episode
        if episode.task_signature != signature or episode.outcome != "verified_success":
            continue
        fresh, _problems = memory.confirm_evidence(episode)
        return fresh
    return None


def summarize_lifecycle(arm: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-scenario records into frozen metric groups."""

    scored = [e for e in entries if e["stale_evidence_detected"] is not None]
    stale_expected = [e for e in entries if e["mutation"] != "none" or e.get("root_marked_missing")]
    fresh_control = [
        e for e in entries if e["mutation"] == "none" and not e.get("root_marked_missing")
    ]
    rejection_expected = [e for e in entries if e["stale_rejection_observed"] is not None]
    # scenarios whose expectations call for an observed stale rejection
    # (only reachable when the controller abstains on task 2)
    abstained_gate = [
        e
        for e in entries
        if e["status2"] == "abstained" and e["reuse_decision2"] != "memory_no_match"
    ]

    return {
        "arm": arm,
        "scenario_count": len(entries),
        "end_to_end_success": sum(1 for e in entries if e["success"]),
        "by_scenario": {e["scenario_id"]: e for e in entries},
        "wall_seconds": round(sum(e["wall_seconds"] for e in entries), 4),
        "latency_median": _median([e["wall_seconds"] for e in entries]),
        "latency_p95": _p95([e["wall_seconds"] for e in entries]),
        "stale_detection": (
            {
                "expected": len([e for e in stale_expected]),
                "detected": sum(1 for e in stale_expected if e["stale_evidence_detected"] is False),
                "fresh_control_detected_fresh": all(
                    e["stale_evidence_detected"] is True for e in fresh_control
                )
                if fresh_control
                else None,
            }
            if scored
            else None
        ),
        "stale_rejections_observed": (
            sum(1 for e in rejection_expected if e["stale_rejection_observed"])
            if rejection_expected
            else None
        ),
        "stale_adoptions": (
            sum(1 for e in rejection_expected if e["stale_adoption"])
            if rejection_expected
            else None
        ),
        "false_verified_claims": sum(
            1 for e in entries if e["status2"] == "verified" and not e["success"]
        ),
        "gate_runs": len(abstained_gate),
    }


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


def run_lifecycle_arm(arm: str) -> dict[str, Any]:
    """Run every scenario for one arm, each in a fresh temp workspace."""

    import tempfile

    entries: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="arceus-lifecycle-") as tmp_name:
        base = Path(tmp_name)
        for scenario in SCENARIOS:
            scenario_dir = base / scenario.id
            scenario_dir.mkdir()
            entries.append(run_scenario(arm, scenario, scenario_dir))
    summary = summarize_lifecycle(arm, entries)
    summary["benchmark_id"] = LIFECYCLE_BENCHMARK_ID
    summary["config"] = LIFECYCLE_CONFIG
    summary["scoring"] = LIFECYCLE_SCORING
    summary["frozen_at"] = FROZEN_AT
    return summary


def shutil_unused_guard() -> None:  # pragma: no cover - keeps import intent explicit
    _ = shutil
