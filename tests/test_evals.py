from __future__ import annotations

import inspect
import json
import os
import re
from dataclasses import fields
from pathlib import Path

import pytest
from evals import path_derived as path_derived_module
from evals.config import (
    ALL_SEEDS,
    BENCHMARK_VERSION,
    CONFIG_VERSION,
    DEFAULT_REPEATS,
    DEVELOPMENT_SEEDS,
    FIXTURE_VERSION,
    HELDOUT_SEEDS,
    TASKS_PER_SEED,
)
from evals.fixture import (
    FixtureModel,
    apply_post_index_mutations,
    build_fixture,
)
from evals.harness import run_evaluation
from evals.metrics import (
    SystemOutcome,
    score_task,
    summarize,
    summarize_categories,
)
from evals.path_derived import PathDerivedRetriever
from evals.retrieval import Retrieval, Retriever
from evals.retrievers import default_retrievers
from evals.run_benchmark import main as benchmark_main
from evals.tasks import (
    CATEGORIES,
    CATEGORY_COUNTS,
    BenchmarkTask,
    RetrievalSpec,
    build_tasks,
    missing_label,
)

from arceus_core import graph as graph_module
from arceus_core.database import open_database
from arceus_core.roots import add_root
from arceus_core.scanner import ScanOptions, scan_root


@pytest.fixture(scope="module")
def evaluation(tmp_path_factory) -> tuple[dict, Path]:
    output_dir = tmp_path_factory.mktemp("eval-artifact")
    output = output_dir / "results.json"
    artifact = run_evaluation(
        repeats=1,
        seeds=DEVELOPMENT_SEEDS[:2],
        output_path=output,
    )
    return artifact, output


def _make_task(
    expected: tuple[str, ...] = (),
    evidence: tuple[str, ...] = (),
    *,
    category: str = "exact_name",
    kind: str = "search",
    task_id: str = "unit-task",
) -> BenchmarkTask:
    return BenchmarkTask(
        id=task_id,
        category=category,
        kind=kind,
        baseline_text="query",
        params={},
        expected_targets=expected,
        expected_evidence=frozenset(evidence),
    )


def _make_spec() -> RetrievalSpec:
    return RetrievalSpec(
        id="unit-task",
        category="exact_name",
        kind="search",
        baseline_text="query",
    )


def _disk_entries(root: Path) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    dirs: set[str] = set()
    for base, _dirnames, filenames in os.walk(root, followlinks=False):
        relative_base = os.path.relpath(base, root)
        prefix = "" if relative_base == "." else Path(relative_base).as_posix()
        if prefix:
            dirs.add(prefix)
        for name in filenames:
            files.add(name if not prefix else f"{prefix}/{name}")
    return files, dirs


def _artifact_blob(artifact: dict) -> str:
    return json.dumps(artifact, ensure_ascii=False)


def test_frozen_configuration_is_disjoint_and_complete() -> None:
    assert len(DEVELOPMENT_SEEDS) == 5
    assert len(HELDOUT_SEEDS) == 10
    assert set(DEVELOPMENT_SEEDS).isdisjoint(HELDOUT_SEEDS)
    assert len(ALL_SEEDS) == 15
    assert TASKS_PER_SEED == 35
    assert sum(CATEGORY_COUNTS.values()) == TASKS_PER_SEED
    assert BENCHMARK_VERSION == "graph-retrieval-v2"
    assert FIXTURE_VERSION == "synthetic-fs-v2"
    assert CONFIG_VERSION == "graph-retrieval-v2-frozen-1"
    assert DEFAULT_REPEATS >= 1


def test_fixture_generation_is_deterministic_and_seed_specific(tmp_path: Path) -> None:
    seed = DEVELOPMENT_SEEDS[0]
    first = build_fixture(tmp_path / "one", seed)
    second = build_fixture(tmp_path / "two", seed)
    assert first.files == second.files
    assert first.dirs == second.dirs
    assert first.missing_files == second.missing_files
    assert first.layout == second.layout
    assert first.filler_file_count == second.filler_file_count

    different = build_fixture(tmp_path / "three", DEVELOPMENT_SEEDS[1])
    assert different.layout != first.layout
    assert different.files != first.files


def test_all_seeds_have_varied_layouts_and_frozen_task_suites(tmp_path: Path) -> None:
    signatures: set[tuple[str, str, int]] = set()
    for seed in ALL_SEEDS:
        model = build_fixture(tmp_path / str(seed), seed)
        tasks = build_tasks(model)
        assert len(tasks) == TASKS_PER_SEED
        assert len({task.id for task in tasks}) == TASKS_PER_SEED
        counts = {category: 0 for category in CATEGORIES}
        for task in tasks:
            counts[task.category] += 1
        assert counts == CATEGORY_COUNTS
        signatures.add((model.layout.docs, model.layout.main_file, len(model.files)))
    assert len(signatures) == len(ALL_SEEDS)


def test_fixture_matches_ground_truth_model(tmp_path: Path) -> None:
    root = tmp_path / "fixture"
    model = build_fixture(root, DEVELOPMENT_SEEDS[1])
    files, dirs = _disk_entries(root)
    assert files == model.files | model.missing_files
    assert dirs == model.dirs

    apply_post_index_mutations(root, model)
    files, dirs = _disk_entries(root)
    assert files == model.files
    assert dirs == model.dirs
    assert all(not (root / rel).exists() for rel in model.missing_files)


def test_task_ground_truth_is_consistent_and_independent(tmp_path: Path) -> None:
    root = tmp_path / "fixture"
    model = build_fixture(root, DEVELOPMENT_SEEDS[2])
    tasks = {task.id: task for task in build_tasks(model)}
    apply_post_index_mutations(root, model)

    docs = tasks["children-01-docs"]
    on_disk = {
        f"{model.layout.docs}/{entry.name}" for entry in os.scandir(root / model.layout.docs)
    }
    assert set(docs.expected_targets) == on_disk

    main = tasks["ancestors-01-main"]
    parts = model.layout.main_file.split("/")
    expected_ancestors = {"/".join(parts[:depth]) for depth in range(1, len(parts))}
    assert set(main.expected_targets) == expected_ancestors

    mixed = tasks["subtree-03-mixed"]
    mixed_root = root / model.layout.mixed
    descendants = {path.relative_to(mixed_root).as_posix() for path in mixed_root.rglob("*")}
    expected_relative = {
        target.removeprefix(f"{model.layout.mixed}/") for target in mixed.expected_targets
    }
    assert expected_relative == {path for path in descendants if path.count("/") == 0}

    for task in tasks.values():
        assert task.expected_targets == tuple(sorted(task.expected_targets))
        for target in task.expected_targets:
            if task.kind == "missing":
                assert target in model.missing_files
                assert not (root / target).exists()
            else:
                assert target in model.entries()
                assert (root / target).exists()
        for label in task.expected_evidence:
            kind, _, payload = label.partition(":")
            if kind == "contains":
                parent, _, child = payload.partition("|")
                expected_parent = "" if parent == "." else parent
                assert model.parent(child) == expected_parent
            elif kind == "missing":
                assert payload in model.missing_files
            elif kind == "indexed":
                assert payload in model.entries()


def test_search_tasks_have_explicit_ambiguity_and_no_result_cases(tmp_path: Path) -> None:
    model = build_fixture(tmp_path / "fixture", DEVELOPMENT_SEEDS[3])
    tasks = {task.id: task for task in build_tasks(model)}

    for task_id in (
        "exact-01-unique",
        "exact-02-singleton",
        "exact-03-budget",
        "exact-04-test",
    ):
        assert len(tasks[task_id].expected_targets) == 1

    for task_id in (
        "ambiguous-01-report",
        "ambiguous-02-notes",
        "ambiguous-03-readme",
        "ambiguous-04-settings",
    ):
        assert len(tasks[task_id].expected_targets) >= 2

    for task_id in ("noresult-01-token", "noresult-02-extension", "noresult-03-prefix"):
        assert tasks[task_id].expected_targets == ()
        assert tasks[task_id].expected_evidence == frozenset()

    for task_id in ("missing-01-obsolete", "missing-02-removed"):
        task = tasks[task_id]
        assert task.expected_targets in (
            (model.layout.obsolete_file,),
            (model.layout.removed_file,),
        )
        assert task.expected_evidence == frozenset({missing_label(task.expected_targets[0])})


def test_metric_calculations_for_targets_and_evidence() -> None:
    task = _make_task(("a", "b"), ("edge:a|b", "edge:b|c"))
    retrieval = Retrieval(
        targets=("a", "extra"),
        evidence=frozenset({"edge:a|b", "edge:x|y"}),
    )
    score = score_task(task, retrieval)

    assert score.success is False
    assert score.top1 is None
    assert score.precision == pytest.approx(0.5)
    assert score.recall == pytest.approx(0.5)
    assert score.f1 == pytest.approx(0.5)
    assert score.evidence_precision == pytest.approx(0.5)
    assert score.evidence_recall == pytest.approx(0.5)


def test_perfect_and_empty_scores() -> None:
    task = _make_task(("a",), ("indexed:a",))
    perfect = score_task(task, Retrieval(("a",), frozenset({"indexed:a"})))
    assert perfect.success is True
    assert perfect.top1 is True
    assert perfect.precision == perfect.recall == perfect.f1 == 1.0

    empty = score_task(task, Retrieval((), frozenset()))
    assert empty.success is False
    assert empty.top1 is False
    assert empty.precision == empty.recall == empty.f1 == 0.0


def test_no_result_denominator_uses_task_kind_not_empty_targets() -> None:
    no_result = _make_task((), (), category="no_result", kind="no_result", task_id="no-result")
    empty_children = _make_task(
        (),
        (),
        category="children",
        kind="children",
        task_id="empty-children",
    )
    clean = score_task(no_result, Retrieval((), frozenset()))
    empty = score_task(empty_children, Retrieval((), frozenset()))
    outcomes = [
        SystemOutcome(no_result, Retrieval((), frozenset()), clean, (1.0,)),
        SystemOutcome(empty_children, Retrieval((), frozenset()), empty, (1.0,)),
    ]
    summary = summarize(outcomes)
    assert summary["no_result"]["task_count"] == 1
    assert summary["no_result"]["false_positive_rate"] == 0.0
    assert summary["success"]["count"] == 2


def test_summarize_categories_keeps_task_level_weights() -> None:
    search_task = _make_task(("a",))
    child_task = _make_task(("b", "c"), category="children", kind="children")
    outcomes = [
        SystemOutcome(
            search_task,
            Retrieval(("a",), frozenset({"indexed:a"})),
            score_task(search_task, Retrieval(("a",), frozenset({"indexed:a"}))),
            (2.0,),
        ),
        SystemOutcome(
            child_task,
            Retrieval((), frozenset()),
            score_task(child_task, Retrieval((), frozenset())),
            (4.0,),
        ),
    ]
    categories = summarize_categories(outcomes)
    assert categories["exact_name"]["task_count"] == 1
    assert categories["children"]["task_count"] == 1
    assert categories["children"]["success"]["count"] == 0
    assert categories["exact_name"]["latency_ms"]["median"] == 2.0


def test_artifact_contains_three_systems_for_every_task(evaluation) -> None:
    artifact, _ = evaluation
    assert artifact["experiment"]["systems"] == [
        "path_only",
        "path_derived",
        "graph_assisted",
    ]
    assert artifact["experiment"]["seed_count"] == 2
    assert artifact["experiment"]["task_instances"] == 70
    task_ids = {(seed["seed"], task["id"]) for seed in artifact["seeds"] for task in seed["tasks"]}
    assert len(task_ids) == 70
    for seed in artifact["seeds"]:
        for task in seed["tasks"]:
            assert set(task["systems"]) == {
                "path_only",
                "path_derived",
                "graph_assisted",
            }
            assert task["expected_targets"] == sorted(task["expected_targets"])
            assert task["expected_evidence"] == sorted(task["expected_evidence"])


def test_path_derived_and_graph_match_ground_truth(evaluation) -> None:
    artifact, _ = evaluation
    for seed in artifact["seeds"]:
        for task in seed["tasks"]:
            expected = set(task["expected_targets"])
            expected_evidence = set(task["expected_evidence"])
            for name in ("path_derived", "graph_assisted"):
                result = task["systems"][name]
                assert set(result["targets"]) == expected, (seed["seed"], task["id"], name)
                assert set(result["evidence"]) == expected_evidence, (
                    seed["seed"],
                    task["id"],
                    name,
                )
                assert result["success"] is True
                assert result["deterministic"] is True

    assert artifact["aggregates"]["path_derived"]["success"]["rate"] == 1.0
    assert artifact["aggregates"]["graph_assisted"]["success"]["rate"] == 1.0
    assert artifact["aggregates"]["path_only"]["success"]["rate"] < 1.0


def test_path_derived_has_no_graph_dependency() -> None:
    source = inspect.getsource(path_derived_module)
    assert "arceus_core.graph" not in source
    assert "from arceus_core import graph" not in source
    assert "arceus_core.paths" not in source


def test_path_derived_runs_with_graph_api_disabled(tmp_path: Path, monkeypatch) -> None:
    fixture_root = tmp_path / "fixture"
    model = build_fixture(fixture_root, DEVELOPMENT_SEEDS[0])
    db_path = tmp_path / "index.sqlite3"
    with open_database(db_path) as conn:
        root = add_root(conn, fixture_root)
        scan_root(conn, root, ScanOptions())
        apply_post_index_mutations(fixture_root, model)
        scan_root(conn, root, ScanOptions())

        def fail(*_args, **_kwargs):
            raise AssertionError("path-derived retrieval called the graph API")

        for name in ("children", "subtree", "ancestors", "lookup_entry", "resolve_path"):
            monkeypatch.setattr(graph_module, name, fail)

        retriever = PathDerivedRetriever()
        for task in build_tasks(model):
            result = retriever.retrieve(conn, root, task.retrieval_spec())
            assert set(result.targets) == set(task.expected_targets), task.id


def test_retrieval_spec_contains_no_ground_truth(evaluation) -> None:
    artifact, _ = evaluation
    names = {field.name for field in fields(RetrievalSpec)}
    assert names == {"id", "category", "kind", "baseline_text", "params"}
    assert not any("expected" in name for name in names)

    task = _make_task(("hidden",), ("indexed:hidden",), task_id="isolation")
    spec = task.retrieval_spec()
    assert spec.id == "isolation"
    assert not hasattr(spec, "expected_targets")
    assert not hasattr(spec, "expected_evidence")
    assert artifact["seeds"][0]["ground_truth"]["validated_against_disk"] is True


def test_path_only_cannot_enumerate_children(evaluation) -> None:
    artifact, _ = evaluation
    for seed in artifact["seeds"]:
        tasks = {task["id"]: task for task in seed["tasks"]}
        for task_id in ("children-01-docs", "children-02-module", "children-03-empty"):
            task = tasks[task_id]
            assert task["systems"]["path_only"]["success"] is False
            assert task["systems"]["path_derived"]["success"] is True
            assert task["systems"]["graph_assisted"]["success"] is True


def test_evidence_attribution_includes_structural_relations(evaluation) -> None:
    artifact, _ = evaluation
    for seed in artifact["seeds"]:
        tasks = {task["id"]: task for task in seed["tasks"]}
        children = tasks["children-01-docs"]
        assert any(
            item.startswith("contains:")
            for item in children["systems"]["graph_assisted"]["evidence"]
        )
        assert any(
            item.startswith("contains:") for item in children["systems"]["path_derived"]["evidence"]
        )
        assert not any(
            item.startswith("contains:") for item in children["systems"]["path_only"]["evidence"]
        )
        ancestors = tasks["ancestors-01-main"]
        main_file = ancestors["params"]["file"]
        assert any(
            label.startswith("contains:") and label.endswith(f"|{main_file}")
            for label in ancestors["systems"]["graph_assisted"]["evidence"]
        )

        missing = tasks["missing-01-obsolete"]
        assert missing["systems"]["graph_assisted"]["evidence"] == [
            missing_label(missing["params"]["path"])
        ]
        assert missing["systems"]["path_derived"]["evidence"] == [
            missing_label(missing["params"]["path"])
        ]


def test_paired_comparisons_and_split_summaries(evaluation) -> None:
    artifact, _ = evaluation
    comparisons = artifact["paired_comparisons"]["all"]
    assert comparisons["graph_assisted_vs_path_only"]["pair_count"] == 70
    assert comparisons["graph_assisted_vs_path_only"]["improvements"] > 0
    assert comparisons["path_derived_vs_path_only"]["improvements"] > 0
    assert comparisons["graph_assisted_vs_path_derived"]["improvements"] == 0
    assert comparisons["graph_assisted_vs_path_derived"]["regressions"] == 0
    assert comparisons["graph_assisted_vs_path_derived"]["ties_success"] == 70

    development = artifact["splits"]["development"]
    assert development["seed_count"] == 2
    assert development["task_instances"] == 70
    assert development["per_seed_success"][str(DEVELOPMENT_SEEDS[0])]["path_derived"]


def test_result_artifact_is_valid_and_has_no_absolute_paths(evaluation) -> None:
    artifact, output = evaluation
    assert output.is_file()
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded["experiment"]["benchmark_version"] == BENCHMARK_VERSION
    assert loaded["experiment"]["fixture_version"] == FIXTURE_VERSION
    assert loaded["experiment"]["config_version"] == CONFIG_VERSION
    assert loaded["experiment"]["seed_count"] == 2
    assert loaded["experiment"]["task_instances"] == 70
    assert loaded["environment"]["python"]

    blob = _artifact_blob(loaded)
    assert re.search(r"[A-Za-z]:[\\/]", blob) is None
    assert "/tmp/" not in blob
    assert "AppData" not in blob
    assert "Users" not in blob


def test_non_timing_outputs_are_repeatable(tmp_path: Path) -> None:
    seed = DEVELOPMENT_SEEDS[0]
    first = run_evaluation(repeats=1, seeds=(seed,), output_path=tmp_path / "first.json")
    second = run_evaluation(repeats=1, seeds=(seed,), output_path=tmp_path / "second.json")

    def core(artifact: dict) -> dict:
        return {
            "task_count": artifact["experiment"]["task_instances"],
            "aggregates": {
                name: {
                    "success": value["success"],
                    "top1": value["top1"],
                    "target_f1_macro": value["target_f1_macro"],
                    "evidence_f1_macro": value["evidence_f1_macro"],
                }
                for name, value in artifact["aggregates"].items()
            },
            "tasks": {
                (seed_report["seed"], task["id"]): {
                    name: {
                        "targets": result["targets"],
                        "evidence": result["evidence"],
                        "success": result["success"],
                    }
                    for name, result in task["systems"].items()
                }
                for seed_report in artifact["seeds"]
                for task in seed_report["tasks"]
            },
        }

    assert core(first) == core(second)


def test_workspace_is_cleaned_up(tmp_path: Path) -> None:
    workspace_parent = tmp_path / "workspaces"
    artifact = run_evaluation(
        repeats=1,
        seeds=(DEVELOPMENT_SEEDS[0],),
        workspace_parent=workspace_parent,
    )
    assert artifact["experiment"]["task_instances"] == TASKS_PER_SEED
    assert workspace_parent.is_dir()
    assert list(workspace_parent.iterdir()) == []


def test_cli_writes_artifact_and_prints_summary(tmp_path: Path, capsys) -> None:
    output = tmp_path / "cli-results.json"
    code = benchmark_main(["--suite", "development", "--repeats", "1", "--output", str(output)])
    captured = capsys.readouterr()
    assert code == 0
    assert "graph-retrieval-v2" in captured.out
    assert "path_only" in captured.out
    assert "path_derived" in captured.out
    assert "graph_assisted" in captured.out
    assert "development split" in captured.out
    assert "100.0%" in captured.out
    assert output.is_file()
    assert json.loads(output.read_text(encoding="utf-8"))["experiment"]["task_instances"] == 175


def test_retrieval_errors_are_measured_not_raised() -> None:
    class FailingRetriever(Retriever):
        name = "failing"

        def _retrieve(self, conn, root, task):
            raise RuntimeError("simulated retriever failure")

    class FailingArceusRetriever(FailingRetriever):
        def _retrieve(self, conn, root, task):
            from arceus_core.errors import QueryError

            raise QueryError("simulated query failure")

    # RuntimeError is a programming error and is not swallowed by design.
    with pytest.raises(RuntimeError):
        FailingRetriever().retrieve(None, None, _make_spec())

    # Expected ARCEUS errors become an empty, annotated measured result.
    result = FailingArceusRetriever().retrieve(None, None, _make_spec())
    assert result.targets == ()
    assert result.evidence == frozenset()
    assert "retrieval error" in result.notes
    assert "simulated query failure" in result.notes


def test_top1_denominator_matches_single_target_tasks(evaluation) -> None:
    artifact, _ = evaluation
    single_target_tasks = sum(
        1
        for seed in artifact["seeds"]
        for task in seed["tasks"]
        if len(task["expected_targets"]) == 1
    )
    for name in artifact["experiment"]["systems"]:
        assert artifact["aggregates"][name]["top1"]["task_count"] == single_target_tasks


def test_default_retrievers_are_three_distinct_systems() -> None:
    systems = default_retrievers()
    assert [system.name for system in systems] == [
        "path_only",
        "path_derived",
        "graph_assisted",
    ]
    for system in systems:
        assert isinstance(system, Retriever)


def test_fixture_model_helpers() -> None:
    model = FixtureModel(
        seed=1,
        version="test",
        files=frozenset({"a/b.txt", "c.txt"}),
        dirs=frozenset({"a"}),
        missing_files=frozenset(),
        filler_file_count=0,
        layout=None,  # type: ignore[arg-type]
    )
    assert model.parent("a/b.txt") == "a"
    assert model.parent("c.txt") == ""
    assert model.depth("a/b.txt") == 2
    assert model.entries() == frozenset({"a", "a/b.txt", "c.txt"})
