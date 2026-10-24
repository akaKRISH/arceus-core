"""Dataset integrity tests for the prompt-development and held-out suites.

Ground truth is checkable independently of the controller: these tests verify
that the ``context`` facts match the actual fixture files that the runners
build from the same module constants (needle precedence, ambiguity counting,
fixture novelty), so labels are derived from the described inventory."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.mark.parametrize(
    ("module_name", "fixture_attr", "tasks_attr"),
    [
        ("prompt_dev_tasks", "DEV_FIXTURE_FILES", "dev_tasks"),
        ("prompt_heldout_tasks", "HELDOUT_FIXTURE_FILES", "heldout_tasks"),
    ],
)
def test_dataset_facts_match_fixture(module_name: str, fixture_attr: str, tasks_attr: str):
    module = __import__(f"evals.{module_name}", fromlist=[fixture_attr, tasks_attr])
    files: dict[str, str] = getattr(module, fixture_attr)
    tasks = getattr(module, tasks_attr)()

    # Every task belongs to a valid category and every expected action is a
    # supported read-only action.
    from evals.controller_tasks import CATEGORIES, VALID_ACTIONS

    ids = [task.id for task in tasks]
    assert len(ids) == len(set(ids))
    for task in tasks:
        assert task.category in CATEGORIES
        assert task.expected_action in VALID_ACTIONS

    # Cases are not copies of the v0 evaluation examples.
    v0_creator = (
        "budget.xlsx",
        "readme.md",
        "docs/readme.md",
        "report_q1.xlsx",
        "report_draft.xlsx",
        "src/main.py",
        "src/util/helper.py",
        "project/src/main.py",
        "project/src/utils.py",
    )
    overlap = set(files) & set(v0_creator)
    assert not overlap, f"fixture copies v0 examples: {overlap}"


def test_dev_ground_truth_grounds(tmp_path) -> None:
    """Needle precedence and ambiguity in the dev fixture are real."""

    from evals.prompt_dev_tasks import DEV_FIXTURE_FILES, dev_facts_for_tests

    facts = dev_facts_for_tests()
    todo_needle = facts["todo_search_needle"]
    paths = sorted(path for path in DEV_FIXTURE_FILES if todo_needle in path.lower())
    assert paths == sorted(facts["todo_distinct_paths"])
    assert len(paths) >= 2, "'open the todo list' must genuinely be ambiguous"

    export_needle = facts["export_search_needle"]
    exports = [p for p in DEV_FIXTURE_FILES if export_needle in p.lower()]
    assert len(exports) >= 2

    for absent_needle in (facts["payroll_search_needle"], facts["weather_search_needle"]):
        assert not any(absent_needle in p.lower() for p in DEV_FIXTURE_FILES), (
            "abstain tasks must be genuine no-evidence tasks"
        )

    for present_needle in (facts["march_search_needle"],):
        matches = [p for p in DEV_FIXTURE_FILES if present_needle in p.lower()]
        assert len(matches) == 1, "single-file search tasks must be uniquely resolved"


def test_dev_and_heldout_suites_are_separate() -> None:
    from evals.prompt_dev_tasks import DEV_FIXTURE_FILES, dev_tasks
    from evals.prompt_heldout_tasks import HELDOUT_FIXTURE_FILES, heldout_tasks

    assert not (set(DEV_FIXTURE_FILES) & set(HELDOUT_FIXTURE_FILES))
    assert not ({t.id for t in dev_tasks()} & {t.id for t in heldout_tasks()})


def test_v0_suite_is_untouched() -> None:
    """Preservation check: the frozen v0 benchmark definition stays intact."""

    from evals.controller_tasks import CONTROLLER_TASK_VERSION, controller_tasks

    assert CONTROLLER_TASK_VERSION == "controller-action-v0"
    assert len(controller_tasks()) == 12
    ids = [task.id for task in controller_tasks()]
    expected_head = "ctrl-01-name-search"
    expected_tail = "ctrl-12-nonsense"
    assert ids[0] == expected_head
    assert ids[-1] == expected_tail
