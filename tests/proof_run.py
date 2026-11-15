"""Reproducible Phase 3 proof run over distinct random farms.

Run from the repository root::

    python -m tests.proof_run --farms 200 --seed 3000

Every generated farm contains a random tractor breakdown. The report compares
Foreman with first-come-first-served and includes the required safety, travel,
idle, and re-plan-time measurements.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from typing import Any

from foreman.day import Day
from tools.make_farm import make_farm

DEFAULT_FARMS = 200
DEFAULT_SEED = 3000


def farm_for_run(index: int, seed: int) -> dict:
    """A deterministic mix of small and challenge-sized farms."""
    return make_farm(
        tractors=2 + index % 3,
        fields=5 + index % 8,
        jobs=6 + index % 10,
        seed=seed + index,
    )


def run_one(raw: dict) -> dict[str, Any]:
    day = Day(raw, use_truth=False)
    day.advance(day.farm.horizon_min)
    return {
        "foreman": day.foreman.sim.totals(),
        "baseline": day.baseline.sim.totals(),
        "replan_ms": [row["ms"] for row in day.foreman.replans],
        "jobs": len(day.farm.jobs),
    }


def _aggregate(rows: list[dict], world: str) -> dict[str, float | int]:
    totals = [row[world] for row in rows]
    keys = ("jobs_done", "on_time", "travel_min", "idle_min", "service_min",
            "wasted_min", "violations")
    return {
        key: round(sum(total[key] for total in totals), 2)
        for key in keys
    }


def run_proof(count: int = DEFAULT_FARMS, seed: int = DEFAULT_SEED,
              progress: bool = False) -> dict[str, Any]:
    if count < 1:
        raise ValueError("farm count must be at least 1")
    started = time.perf_counter()
    rows = []
    for index in range(count):
        row = run_one(farm_for_run(index, seed))
        rows.append(row)
        if progress and ((index + 1) % 25 == 0 or index + 1 == count):
            print(f"completed {index + 1}/{count} random farms", flush=True)

    foreman = _aggregate(rows, "foreman")
    baseline = _aggregate(rows, "baseline")
    replans = [value for row in rows for value in row["replan_ms"]]
    ordered = sorted(replans)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] if ordered else 0.0
    return {
        "random_farms": count,
        "seed_first": seed,
        "seed_last": seed + count - 1,
        "jobs": sum(row["jobs"] for row in rows),
        "foreman": foreman,
        "baseline": baseline,
        "comparison": {
            "on_time_gain": foreman["on_time"] - baseline["on_time"],
            "travel_min_saved": round(baseline["travel_min"] - foreman["travel_min"], 2),
            "idle_min_saved": round(baseline["idle_min"] - foreman["idle_min"], 2),
            "days_foreman_at_least_as_many_on_time": sum(
                row["foreman"]["on_time"] >= row["baseline"]["on_time"] for row in rows
            ),
            "days_foreman_wasted_no_more": sum(
                row["foreman"]["wasted_min"] <= row["baseline"]["wasted_min"] + 0.05
                for row in rows
            ),
        },
        "replan": {
            "dispatches": len(replans),
            "mean_ms": round(statistics.fmean(replans), 3) if replans else 0.0,
            "p95_ms": round(p95, 3),
            "max_ms": round(max(replans), 3) if replans else 0.0,
        },
        "runtime_seconds": round(time.perf_counter() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Ahmed's Phase 3 random-farm proof")
    parser.add_argument("--farms", type=int, default=DEFAULT_FARMS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--quiet", action="store_true", help="hide progress messages")
    args = parser.parse_args()
    report = run_proof(args.farms, args.seed, progress=not args.quiet)
    print(json.dumps(report, indent=2))
    return 1 if report["foreman"]["violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
