"""Reproducible evaluation harness for Project ARCEUS.

Run the complete frozen experiment from the repository root:

    python -m evals.run_benchmark

The harness uses only synthetic temporary fixtures and the deterministic
metadata index. It makes no network calls and downloads no models.
"""

from __future__ import annotations

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

DEFAULT_SEED = DEVELOPMENT_SEEDS[0]

__all__ = [
    "ALL_SEEDS",
    "BENCHMARK_VERSION",
    "CONFIG_VERSION",
    "DEFAULT_REPEATS",
    "DEFAULT_SEED",
    "DEVELOPMENT_SEEDS",
    "FIXTURE_VERSION",
    "HELDOUT_SEEDS",
    "TASKS_PER_SEED",
]
