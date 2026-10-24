#!/usr/bin/env python3
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.lifecycle_tasks import run_lifecycle_arm
from evals.skill_tasks import run_skills_arm


def main():
    reports = Path(__file__).resolve().parents[1] / "reports"
    reports.mkdir(exist_ok=True)

    results = {
        "phase": "6.1",
        "lifecycle": {},
        "skills": {},
    }

    # Run lifecycle for both arms
    for arm in ["baseline", "memory"]:
        results["lifecycle"][arm] = run_lifecycle_arm(arm)
        entry = results["lifecycle"][arm]
        print(f"Lifecycle {arm}: {entry['end_to_end_success']}/{entry['scenario_count']}")

    # Run skills for all arms
    for arm in ["baseline", "memory_only", "skills_only", "memory_skills"]:
        results["skills"][arm] = run_skills_arm(arm)
        entry = results["skills"][arm]
        print(f"Skills {arm}: {entry['end_to_end_success']}/{entry['task_count']}")

    out_path = reports / "phase61_benchmarks.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
