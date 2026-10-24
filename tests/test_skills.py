"""Phase 6 tests: declarative skill registration, promotion, and selection.

A skill is never verified by authorship alone: promotion demands distinct,
independently verified runs with zero recorded failures under the explicit
``MIN_VERIFIED_RUNS`` policy, and selection re-checks preconditions against
the current index every time.
"""

from __future__ import annotations

import pytest

from arceus_core.errors import MemoryStoreError
from arceus_core.memory import EpisodicMemory
from arceus_core.scanner import scan_root
from arceus_core.skills import MIN_VERIFIED_RUNS, SKILL_POLICY_VERSION, SkillAction, SkillRegistry


@pytest.fixture
def registry(conn, root, root_dir, make_files):
    make_files(
        root_dir,
        {
            "notes/todo.md": "- note",
            "archive/report_2026.xlsx": "",
            "inbox/letter.txt": "",
        },
    )
    scan_root(conn, root)
    return SkillRegistry(conn)


def _register(registry: SkillRegistry, skill_id: str = "list-notes", **overrides):
    fields = dict(
        id=skill_id,
        purpose="List the notes directory when a task asks for its contents",
        supported_categories=("structural",),
        preconditions=[{"kind": "entry_present", "rel_path": "notes"}],
        action_sequence=[
            SkillAction(action="graph_children", arguments={"path": "notes"}),
        ],
        verification_procedure=("verifier confirms every child names 'notes' as parent",),
        provenance="seeded from three independently verified benchmark episodes",
    )
    fields.update(overrides)
    return registry.register(**fields)


# -- registration validation (allow-list enforcement) -------------------------


def test_valid_registration_creates_candidate(registry) -> None:
    skill = _register(registry)
    assert skill.status == "candidate"
    assert skill.version == 1
    assert skill.required_permissions == ("read_only",)
    assert skill.input_limits["max_task_chars"] > 0
    assert registry.get(skill.id) is not None
    assert [s.id for s in registry.list()] == [skill.id]
    assert registry.get(skill.id).provenance == skill.provenance
    assert SKILL_POLICY_VERSION.startswith("skill-policy-v")


@pytest.mark.parametrize(
    "override",
    [
        {"id": "Bad ID!"},
        {"id": "ab"},  # too short
        {"version": 0},
        {"purpose": "   "},
        {"supported_categories": ()},
        {"required_permissions": ("admin",)},
        {"verification_procedure": ()},
        {"expected_outputs": ""},
        {"action_sequence": []},
        # Multi-step sequences exceed the bounded one-action workflow.
        {
            "action_sequence": [
                SkillAction(action="graph_children", arguments={"path": "notes"}),
                SkillAction(action="search_files", arguments={"query": "x"}),
            ]
        },
        # Non-allow-list / non-tool actions are refused.
        {"action_sequence": [SkillAction(action="run_shell", arguments={})]},
        {"action_sequence": [SkillAction(action="delete_files", arguments={"path": "a"})]},
        {"action_sequence": [SkillAction(action="abstain", arguments={"reason": "x"})]},
        # Wrong argument shapes fail the shared controller validation.
        {"action_sequence": [SkillAction(action="search_files", arguments={"query": 5})]},
        {"action_sequence": [SkillAction(action="graph_children", arguments={})]},
        {"preconditions": [{"kind": "exec_code", "payload": "os.system('x')"}]},
        {"preconditions": [{"kind": "entry_present"}]},
        {"input_limits": {"max_task_chars": 0}},
        {"provenance": "tiny"},
    ],
)
def test_invalid_registration_is_rejected(registry, override) -> None:
    kwargs = dict(
        id="list-notes",
        purpose="List notes",
        supported_categories=("structural",),
        action_sequence=[SkillAction(action="graph_children", arguments={"path": "notes"})],
        verification_procedure=("verifier check",),
        provenance="created by deterministic evaluation harness",
    )
    kwargs.update(override)
    with pytest.raises(MemoryStoreError):
        registry.register(**kwargs)


def test_duplicate_skill_id_rejected(registry) -> None:
    _register(registry)
    with pytest.raises(MemoryStoreError):
        _register(registry)


# -- promotion policy ----------------------------------------------------------


def _episode_id(conn, marker: str) -> int:
    """A real (existing) episode row — skill_runs references episodes."""

    episode = EpisodicMemory(conn).record(
        f"validation session {marker}",
        action="search_files",
        origin="deterministic",
        executed=True,
        verification_status="verified",
        task_completed=True,
        config_version="test-config",
    )
    return episode.id


def _promotable_runs(registry: SkillRegistry, skill_id: str, *, verified_runs: int, failed: int):
    conn = registry._conn
    for index in range(verified_runs):
        registry.record_run(
            skill_id,
            episode_id=_episode_id(conn, f"{skill_id}-ok-{index}"),
            verified=True,
            outcome="verified",
        )
    for index in range(failed):
        registry.record_run(
            skill_id,
            episode_id=_episode_id(conn, f"{skill_id}-bad-{index}"),
            verified=False,
            outcome="failed",
        )


def test_promotion_requires_minimum_verified_runs(registry) -> None:
    skill = _register(registry)
    ok, reason = registry.promote(skill.id)
    assert not ok and "needs 3" in reason

    _promotable_runs(registry, skill.id, verified_runs=MIN_VERIFIED_RUNS - 1, failed=0)
    ok, reason = registry.promote(skill.id)
    assert not ok and "needs" in reason
    assert registry.get(skill.id).status == "candidate"

    registry.record_run(
        skill.id,
        episode_id=_episode_id(registry._conn, "list-notes-final"),
        verified=True,
        outcome="verified",
    )
    ok, reason = registry.promote(skill.id)
    assert ok and reason is None
    assert registry.get(skill.id).status == "verified"
    assert registry.get(skill.id).status_reason == "promotion policy satisfied"


def test_any_recorded_failure_disqualifies_promotion(registry) -> None:
    skill = _register(registry)
    _promotable_runs(registry, skill.id, verified_runs=MIN_VERIFIED_RUNS, failed=1)
    ok, reason = registry.promote(skill.id)
    assert not ok and "not verified" in reason
    assert registry.get(skill.id).status == "candidate"


def test_duplicate_runs_do_not_inflate_evidence(registry) -> None:
    skill = _register(registry)
    episode_id = _episode_id(registry._conn, "repeat")
    for _ in range(5):
        registry.record_run(skill.id, episode_id=episode_id, verified=True, outcome="verified")
    stats = registry.run_stats(skill.id)
    assert stats == {"runs": 1, "verified": 1}
    ok, reason = registry.promote(skill.id)
    assert not ok  # one distinct episode repeated is not three validations


def test_promotion_of_unknown_skill_raises(registry) -> None:
    with pytest.raises(MemoryStoreError):
        registry.promote("no-such-skill")


# -- status lifecycle -----------------------------------------------------------


def test_status_lifecycle_enforces_reviewable_transitions(registry) -> None:
    skill = _register(registry)
    # candidate cannot be manually flipped to verified (policy gate only)
    with pytest.raises(MemoryStoreError):
        registry.set_status(skill.id, "verified", "manual flip")
    with pytest.raises(MemoryStoreError):
        registry.set_status(skill.id, "disabled", "pause")

    _promotable_runs(registry, skill.id, verified_runs=MIN_VERIFIED_RUNS, failed=0)
    assert registry.promote(skill.id)[0]

    disabled = registry.set_status(skill.id, "disabled", "paused for review")
    assert disabled.status == "disabled"
    re_enabled = registry.set_status(skill.id, "verified", "review passed")
    assert re_enabled.status == "verified"

    revoked = registry.set_status(skill.id, "revoked", "found unsafe in audit")
    assert revoked.status == "revoked"
    # Revocation is terminal.
    with pytest.raises(MemoryStoreError):
        registry.set_status(skill.id, "verified", "undelete")
    # A reason is always required.
    _register(registry, skill_id="other-skill")
    _promotable_runs(registry, "other-skill", verified_runs=MIN_VERIFIED_RUNS, failed=0)
    assert registry.promote("other-skill")[0]
    with pytest.raises(MemoryStoreError):
        registry.set_status("other-skill", "disabled", "  ")


# -- preconditions and observable selection -------------------------------------


def test_preconditions_evaluate_against_current_index(registry) -> None:
    skill = _register(registry)
    passed, failures = registry.check_preconditions(
        skill, task_category="structural", task_text="list what is in notes"
    )
    assert passed and failures == []

    # The entry disappears from the index: the precondition no longer holds.
    registry._conn.execute("UPDATE entries SET status='missing' WHERE rel_path LIKE 'notes%'")
    registry._conn.commit()
    passed, failures = registry.check_preconditions(
        skill, task_category="structural", task_text="list what is in notes"
    )
    assert not passed and any("not present" in f for f in failures)

    # Category and input-limit checks.
    passed, failures = registry.check_preconditions(
        skill, task_category="name_search", task_text="find x"
    )
    assert not passed and any("not in" in f for f in failures)
    passed, failures = registry.check_preconditions(
        skill, task_category="structural", task_text="x" * 9999
    )
    assert not passed and any("input limit" in f for f in failures)


def test_root_active_precondition(registry) -> None:
    skill = _register(
        registry,
        skill_id="root-gated",
        preconditions=[{"kind": "root_active", "label": None}],
    )
    passed, _ = registry.check_preconditions(
        skill, task_category="structural", task_text="list notes"
    )
    assert passed
    registry._conn.execute("UPDATE roots SET status='missing'")
    registry._conn.commit()
    passed, failures = registry.check_preconditions(
        skill, task_category="structural", task_text="list notes"
    )
    assert not passed and any("missing" in f for f in failures)


def test_evaluate_reports_every_rejection_state(registry) -> None:
    _register(registry)  # candidate: list-notes
    decision = registry.evaluate(task_category="structural", task_text="list what is in notes")
    assert decision.decision == "skill_not_verified"
    assert decision.skill_id == "list-notes"

    _promotable_runs(registry, "list-notes", verified_runs=MIN_VERIFIED_RUNS, failed=0)
    registry.promote("list-notes")
    decision = registry.evaluate(task_category="structural", task_text="list what is in notes")
    assert decision.decision == "skill_adoptable"
    assert decision.skill is not None and decision.skill.id == "list-notes"

    # Category without any skill: no match.
    decision = registry.evaluate(task_category="ancestor_retrieval", task_text="trace parents")
    assert decision.decision == "skill_no_match" and decision.skill_id is None

    # Precondition failure is observable, not silent.
    registry._conn.execute("UPDATE entries SET status='missing' WHERE rel_path LIKE 'notes%'")
    registry._conn.commit()
    decision = registry.evaluate(task_category="structural", task_text="list what is in notes")
    assert decision.decision == "skill_precondition_failed"
    assert decision.reasons and decision.skill_id == "list-notes"

    # Revocation is observable for tasks the skill would otherwise serve.
    registry._conn.execute("UPDATE entries SET status='present' WHERE rel_path LIKE 'notes%'")
    registry._conn.commit()
    registry.set_status("list-notes", "revoked", "audit found stale expectations")
    decision = registry.evaluate(task_category="structural", task_text="list what is in notes")
    assert decision.decision == "skill_revoked"
    assert "audit" in decision.reasons[0]


def test_unknown_skill_operations_raise(registry) -> None:
    with pytest.raises(MemoryStoreError):
        registry.record_run("ghost-skill", episode_id=1, verified=True, outcome="verified")
    with pytest.raises(MemoryStoreError):
        registry.set_status("ghost-skill", "revoked", "nope")
