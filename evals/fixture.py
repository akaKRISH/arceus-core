"""Deterministic synthetic fixture for the Phase 3.1 retrieval evaluation.

Each seed produces a different hierarchy: directory names, project nesting, a
possibly present package directory, file names, filler placement, and filler
count all vary. The fixture is generated entirely in a caller-provided
temporary directory. No user data is read.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from evals.config import FIXTURE_VERSION

WORDS: tuple[str, ...] = (
    "amber",
    "atlas",
    "basalt",
    "boreal",
    "cedar",
    "cinder",
    "delta",
    "dune",
    "ember",
    "fjord",
    "flint",
    "garnet",
    "glacier",
    "harbor",
    "heron",
    "indigo",
    "iris",
    "jasper",
    "juniper",
    "kelp",
    "kestrel",
    "lotus",
    "lumen",
    "meridian",
    "moss",
    "nickel",
    "nimbus",
    "onyx",
    "pine",
    "quartz",
    "raven",
    "sable",
    "tundra",
    "umber",
    "vale",
    "willow",
    "xenon",
    "yarrow",
    "zephyr",
)

PROJECT_ROOTS: tuple[str, ...] = ("projects", "work", "ventures", "studio")
MODULE_NAMES: tuple[str, ...] = ("src", "source", "app", "code")
TEST_DIR_NAMES: tuple[str, ...] = ("tests", "spec", "checks")
FILLER_EXTENSIONS: tuple[str, ...] = ("txt", "md", "py", "jpg", "log", "json")


@dataclass(frozen=True, slots=True)
class FixtureLayout:
    """Named anchors used by the task suite. These are not retrieval answers."""

    docs: str
    archive: str
    archive_year: str
    project_root: str
    project: str
    module: str
    package: str | None
    tests: str
    other: str
    photos_root: str
    photo_year: str
    trip: str
    mixed: str
    nested: str
    empty: str
    void: str
    unique_dir: str
    singleton_dir: str
    main_file: str
    test_file: str
    report_name: str
    note_stem: str
    readme_name: str
    settings_name: str
    budget_file: str
    unique_file: str
    singleton_file: str
    data_file: str
    gamma_file: str
    obsolete_file: str
    removed_file: str


@dataclass(frozen=True, slots=True)
class FixtureModel:
    """Ground-truth view of the fixture, independent of the index."""

    seed: int
    version: str
    files: frozenset[str]
    dirs: frozenset[str]
    missing_files: frozenset[str]
    filler_file_count: int
    layout: FixtureLayout

    def entries(self) -> frozenset[str]:
        """All present entries (files and directories) after fixture mutations."""

        return self.files | self.dirs

    def parent(self, rel_path: str) -> str:
        """Parent relative path, or '' for a top-level entry."""

        index = rel_path.rfind("/")
        return "" if index < 0 else rel_path[:index]

    def depth(self, rel_path: str) -> int:
        return 0 if rel_path == "" else rel_path.count("/") + 1


def _unique_label(rng: random.Random, used: set[str], *, separator: str = "-") -> str:
    for _ in range(1000):
        parts = [rng.choice(WORDS)]
        if rng.random() < 0.6:
            parts.append(rng.choice(WORDS))
        candidate = separator.join(parts)
        if candidate not in used:
            used.add(candidate)
            return candidate
    raise RuntimeError("could not generate a unique fixture label")


def _write_file(root: Path, rel_path: str, content: str) -> None:
    target = root / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _build_layout(rng: random.Random) -> FixtureLayout:
    used: set[str] = set()
    project_root = rng.choice(PROJECT_ROOTS)
    used.add(project_root)
    docs = _unique_label(rng, used)
    archive = _unique_label(rng, used)
    project = _unique_label(rng, used)
    other = _unique_label(rng, used)
    photos_root = _unique_label(rng, used)
    trip = _unique_label(rng, used)
    mixed = _unique_label(rng, used)
    nested = _unique_label(rng, used)
    empty = _unique_label(rng, used)
    void = _unique_label(rng, used)
    unique_dir = _unique_label(rng, used)
    singleton_dir = _unique_label(rng, used)

    module = rng.choice(MODULE_NAMES)
    tests = rng.choice(TEST_DIR_NAMES)
    package = _unique_label(rng, used) if rng.random() < 0.55 else None
    archive_year = str(rng.randint(2020, 2029))
    photo_year = str(rng.randint(2020, 2029))

    report_name = f"report-{_unique_label(rng, used)}.txt"
    note_stem = f"notes-{_unique_label(rng, used)}"
    budget_name = f"budget-{_unique_label(rng, used)}.xlsx"
    needle_name = f"needle-{_unique_label(rng, used)}.dat"
    singleton_name = f"singleton-{_unique_label(rng, used)}.log"
    test_name = f"test_{_unique_label(rng, used, separator='_')}.py"
    obsolete_name = f"obsolete-{_unique_label(rng, used)}.txt"
    removed_name = f"removed-{_unique_label(rng, used)}.py"

    project_path = f"{project_root}/{project}"
    module_path = f"{project_path}/{module}"
    main_path = f"{module_path}/main.py" if package is None else f"{module_path}/{package}/main.py"

    return FixtureLayout(
        docs=docs,
        archive=archive,
        archive_year=archive_year,
        project_root=project_root,
        project=project,
        module=module,
        package=package,
        tests=tests,
        other=other,
        photos_root=photos_root,
        photo_year=photo_year,
        trip=trip,
        mixed=mixed,
        nested=nested,
        empty=empty,
        void=void,
        unique_dir=unique_dir,
        singleton_dir=singleton_dir,
        main_file=main_path,
        test_file=f"{project_path}/{tests}/{test_name}",
        report_name=report_name,
        note_stem=note_stem,
        readme_name="README.md",
        settings_name="settings.json",
        budget_file=f"{docs}/{budget_name}",
        unique_file=f"{unique_dir}/{needle_name}",
        singleton_file=f"{singleton_dir}/{singleton_name}",
        data_file=f"{mixed}/{nested}/data.txt",
        gamma_file=f"{mixed}/{nested}/gamma.py",
        obsolete_file=f"{docs}/{obsolete_name}",
        removed_file=f"{module_path}/{removed_name}",
    )


def build_fixture(root: Path, seed: int) -> FixtureModel:
    """Create the fixture on disk and return its independent ground-truth model."""

    if not isinstance(seed, int):
        raise TypeError("seed must be an integer")

    root.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    layout = _build_layout(rng)

    project_path = f"{layout.project_root}/{layout.project}"
    module_path = f"{project_path}/{layout.module}"
    tests_path = f"{project_path}/{layout.tests}"
    archive_path = f"{layout.docs}/{layout.archive}"
    year_path = f"{archive_path}/{layout.archive_year}"
    photo_path = f"{layout.photos_root}/{layout.photo_year}/{layout.trip}"
    nested_path = f"{layout.mixed}/{layout.nested}"
    bulk_groups = tuple(f"bulk/group-{index:02d}" for index in range(rng.randint(8, 12)))

    directories = {
        layout.docs,
        archive_path,
        year_path,
        layout.project_root,
        project_path,
        module_path,
        tests_path,
        f"{layout.project_root}/{layout.other}",
        layout.photos_root,
        f"{layout.photos_root}/{layout.photo_year}",
        photo_path,
        layout.mixed,
        nested_path,
        layout.empty,
        f"{layout.empty}/nested-empty",
        layout.void,
        layout.unique_dir,
        layout.singleton_dir,
        "bulk",
        *bulk_groups,
    }
    if layout.package is not None:
        directories.add(f"{module_path}/{layout.package}")

    for directory in sorted(directories):
        (root / directory).mkdir(parents=True, exist_ok=True)

    files: set[str] = {
        # Documents and an archive with repeated names.
        f"{layout.docs}/{layout.report_name}",
        f"{layout.docs}/{layout.note_stem}.md",
        layout.budget_file,
        f"{archive_path}/{layout.report_name}",
        f"{year_path}/{layout.report_name}",
        # Two projects with repeated README, notes, and settings names.
        f"{project_path}/{layout.readme_name}",
        f"{project_path}/{layout.settings_name}",
        f"{module_path}/util.py",
        f"{module_path}/helpers.py",
        layout.main_file,
        layout.test_file,
        f"{layout.project_root}/{layout.other}/{layout.readme_name}",
        f"{layout.project_root}/{layout.other}/{layout.note_stem}.md",
        f"{layout.project_root}/{layout.other}/{layout.settings_name}",
        # Photo trip and mixed-extension tree.
        f"{photo_path}/photo-0001.jpg",
        f"{photo_path}/photo-0002.jpg",
        f"{photo_path}/photo-0003.jpg",
        f"{photo_path}/{layout.note_stem}.txt",
        f"{layout.mixed}/alpha.py",
        f"{layout.mixed}/beta.py",
        f"{layout.mixed}/readme.md",
        layout.gamma_file,
        layout.data_file,
        # Uniquely named files.
        layout.unique_file,
        layout.singleton_file,
    }

    filler_count = rng.randint(80, 140)
    for index in range(filler_count):
        group = bulk_groups[index % len(bulk_groups)]
        token = rng.getrandbits(32)
        extension = FILLER_EXTENSIONS[index % len(FILLER_EXTENSIONS)]
        rel_path = f"{group}/bulk-{token:08x}-{index:03d}.{extension}"
        files.add(rel_path)

    for rel_path in sorted(files):
        _write_file(root, rel_path, "synthetic fixture file; content is never read\n")

    missing_files = {layout.obsolete_file, layout.removed_file}
    for rel_path in sorted(missing_files):
        _write_file(root, rel_path, "deleted after the first scan\n")

    return FixtureModel(
        seed=seed,
        version=FIXTURE_VERSION,
        files=frozenset(files),
        dirs=frozenset(directories),
        missing_files=frozenset(missing_files),
        filler_file_count=filler_count,
        layout=layout,
    )


def apply_post_index_mutations(root: Path, model: FixtureModel) -> None:
    """Delete the files that must be observed as missing on the second scan."""

    for rel_path in sorted(model.missing_files):
        target = root / rel_path
        if not target.is_file():
            raise FileNotFoundError(f"fixture mutation target is missing: {rel_path}")
        target.unlink()


__all__ = [
    "FILLER_EXTENSIONS",
    "FixtureLayout",
    "FixtureModel",
    "apply_post_index_mutations",
    "build_fixture",
]
