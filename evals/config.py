"""Frozen configuration for the Phase 3.1 held-out evaluation.

Development seeds were available while the fixture, task suite, and the
``path_derived`` baseline were implemented. Held-out seeds must not be used to
adjust retrieval logic. Changing either list, the task counts, or the versions
below defines a new experiment and requires a new artifact.
"""

from __future__ import annotations

BENCHMARK_VERSION = "graph-retrieval-v2"
FIXTURE_VERSION = "synthetic-fs-v2"
CONFIG_VERSION = "graph-retrieval-v2-frozen-1"

# Five development seeds, used for implementation and debugging only.
DEVELOPMENT_SEEDS: tuple[int, ...] = (
    20261023,
    20261024,
    20261025,
    20261026,
    20261027,
)

# Ten held-out seeds. These are fixed before running any held-out evaluation.
HELDOUT_SEEDS: tuple[int, ...] = (
    731001,
    731002,
    731003,
    731004,
    731005,
    731006,
    731007,
    731008,
    731009,
    731010,
)

ALL_SEEDS: tuple[int, ...] = DEVELOPMENT_SEEDS + HELDOUT_SEEDS

TASKS_PER_SEED = 35
DEFAULT_REPEATS = 3
EVAL_SEARCH_LIMIT = 200
EVAL_TREE_LIMIT = 500

SUITE_DESCRIPTIONS: dict[str, str] = {
    "development": "five seeds used while implementing and debugging the suite",
    "heldout": "ten frozen seeds not used to tune retrieval logic",
    "all": "all fifteen seeds, reported separately by split",
}


def seeds_for_suite(suite: str) -> tuple[int, ...]:
    """Return the frozen seed list for a named suite."""

    if suite == "development":
        return DEVELOPMENT_SEEDS
    if suite == "heldout":
        return HELDOUT_SEEDS
    if suite == "all":
        return ALL_SEEDS
    raise ValueError(f"unknown suite: {suite!r}")


def split_for_seed(seed: int) -> str:
    """Return 'development' or 'heldout' for a configured seed."""

    if seed in DEVELOPMENT_SEEDS:
        return "development"
    if seed in HELDOUT_SEEDS:
        return "heldout"
    raise ValueError(f"seed {seed} is not part of the frozen configuration")


__all__ = [
    "ALL_SEEDS",
    "BENCHMARK_VERSION",
    "CONFIG_VERSION",
    "DEFAULT_REPEATS",
    "DEVELOPMENT_SEEDS",
    "EVAL_SEARCH_LIMIT",
    "EVAL_TREE_LIMIT",
    "FIXTURE_VERSION",
    "HELDOUT_SEEDS",
    "SUITE_DESCRIPTIONS",
    "TASKS_PER_SEED",
    "seeds_for_suite",
    "split_for_seed",
]
