"""Sweep the planner's idle weight across every test day and print the comparison.

Run with:  python -m foreman.sweep            (default weights)
           python -m foreman.sweep 0.4 1 2    (your own list)

A test day is one farm with either no breakdown or one tractor breaking at
one time. Hidden speed factors are off, so the result does not depend on which
tractor the factors happen to hit. Settings are ranked by: jobs on time, then
wasted minutes (travel + idle + service), then when the day finished.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from foreman import evaluator
from foreman.day import Day

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WEIGHTS = (0.2, 0.4, 0.7, 1.0, 1.5, 2.0, 3.0)
BREAK_TIMES = (3, 20, 45, 70, 100, 140, 180, 230, 300, 380)


def _load(relative: str) -> dict:
    with open(ROOT / relative, encoding="utf-8") as farm_file:
        raw = json.load(farm_file)
    raw["events"] = []
    return raw


def _ten_job_farm() -> dict:
    """The example farm grown to 8 fields and 10 jobs (the same farm the tests use)."""
    raw = _load("scenarios/example_farm.json")
    raw["name"] = "Example farm, ten jobs"
    raw["nodes"] += [
        {"id": "F5", "kind": "field", "name": "South Pivot", "x": 0.5, "y": -2.2, "area_ha": 14},
        {"id": "F6", "kind": "field", "name": "Mesa Top", "x": -2.4, "y": -1.0, "area_ha": 9},
        {"id": "F7", "kind": "field", "name": "Canal Strip", "x": 4.2, "y": 0.4, "area_ha": 11},
        {"id": "F8", "kind": "field", "name": "Home Quarter", "x": -0.8, "y": 2.9, "area_ha": 8},
    ]
    raw["roads"] += [["YARD", "F5"], ["YARD", "F6"], ["X2", "F7"], ["X1", "F8"]]
    raw["jobs"] += [
        {"id": "J6", "type": "spray", "field": "F5", "priority": 3, "deadline": 300},
        {"id": "J7", "type": "mow", "field": "F6", "priority": 2, "deadline": 540},
        {"id": "J8", "type": "till", "field": "F7", "priority": 3, "deadline": 420},
        {"id": "J9", "type": "plant", "field": "F7", "priority": 4, "deadline": 640, "after": "J8"},
        {"id": "J10", "type": "spray", "field": "F8", "priority": 5, "deadline": 200, "release": 30},
    ]
    return raw


def farms() -> dict[str, dict]:
    """Every farm we have. Add a farm here and it joins the sweep."""
    found = {"example": _load("scenarios/example_farm.json"), "ten": _ten_job_farm()}
    for path in sorted((ROOT / "scenarios" / "farms").glob("*.json")):
        found[path.stem.replace("_farm", "")] = _load(str(path.relative_to(ROOT)))
    return found


def test_days() -> list[tuple[str, str, dict]]:
    """(farm, label, farm dict) for every test day: quiet, and each tractor breaking at each time."""
    days = []
    for name, raw in farms().items():
        days.append((name, "quiet", raw))
        for tractor in raw["tractors"]:
            for minute in BREAK_TIMES:
                broken = copy.deepcopy(raw)
                broken["events"] = [{"at": minute, "type": "breakdown", "tractor": tractor["id"]}]
                days.append((name, f"{tractor['id']}@{minute}", broken))
    return days


def run_days(days: list[tuple[str, str, dict]]) -> list[dict]:
    """Run each day to the end with the current weights. One row per day."""
    rows = []
    for farm, label, raw in days:
        day = Day(raw, use_truth=False)
        day.advance(day.farm.horizon_min)
        rows.append({"farm": farm, "label": label, "jobs": len(day.farm.jobs),
                     "foreman": day.foreman.sim.totals(), "baseline": day.baseline.sim.totals()})
    return rows


def summarise(rows: list[dict], who: str = "foreman") -> dict:
    totals = [row[who] for row in rows]
    quiet = {row["farm"]: row[who] for row in rows if row["label"] == "quiet"}
    return {
        "on_time": sum(t["on_time"] for t in totals),
        "done": sum(t["jobs_done"] for t in totals),
        "wasted": sum(t["wasted_min"] for t in totals),
        "travel": sum(t["travel_min"] for t in totals),
        "idle": sum(t["idle_min"] for t in totals),
        "service": sum(t["service_min"] for t in totals),
        "finish": sum(t["finish_min"] for t in totals) / len(totals),
        "violations": sum(t["violations"] for t in totals),
        "quiet_wasted": {farm: t["wasted_min"] for farm, t in quiet.items()},
        "quiet_idle": {farm: t["idle_min"] for farm, t in quiet.items()},
        # Days where the baseline wasted fewer minutes than this dispatcher.
        "days_baseline_wastes_less": sum(
            1 for row in rows if row["baseline"]["wasted_min"] < row[who]["wasted_min"] - 0.05),
        "days_baseline_more_on_time": sum(
            1 for row in rows if row["baseline"]["on_time"] > row[who]["on_time"]),
    }


def rank(summary: dict) -> tuple:
    """Smaller is better: jobs on time first, then wasted minutes, then finish time."""
    return (-summary["on_time"], summary["wasted"], summary["finish"])


def sweep(weights: tuple[float, ...] = DEFAULT_WEIGHTS) -> list[tuple[float, dict]]:
    days = test_days()
    original = evaluator.W_WAIT
    results = []
    try:
        for weight in weights:
            evaluator.W_WAIT = weight
            results.append((weight, summarise(run_days(days))))
    finally:
        evaluator.W_WAIT = original
    return results


def main() -> None:
    weights = tuple(float(arg) for arg in sys.argv[1:]) or DEFAULT_WEIGHTS
    days = test_days()
    names = list(farms())
    total_jobs = sum(len(raw["jobs"]) for _, _, raw in days)
    print(f"{len(days)} test days on {len(names)} farms ({', '.join(names)}), {total_jobs} jobs, "
          "hidden factors off. Current idle weight in the code: "
          f"{evaluator.W_WAIT}.\n")
    header = ("| Idle weight | On time | Done | Wasted | Travel | Idle | Service | Mean finish | "
              "Violations | Days baseline wastes less | Days baseline more on time | "
              + " | ".join(f"Quiet {name}: wasted (idle)" for name in names) + " |")
    print(header)
    print("|" + "---|" * (header.count("|") - 1))

    def line(label: str, s: dict) -> str:
        quiet = " | ".join(f"{s['quiet_wasted'][n]:.0f} ({s['quiet_idle'][n]:.0f})" for n in names)
        return (f"| {label} | {s['on_time']} | {s['done']} | {s['wasted']:.0f} | {s['travel']:.0f} | "
                f"{s['idle']:.0f} | {s['service']:.0f} | {s['finish']:.0f} | {s['violations']} | "
                f"{s['days_baseline_wastes_less']} | {s['days_baseline_more_on_time']} | {quiet} |")

    results = sweep(weights)
    best = min(results, key=lambda item: rank(item[1]))[0]
    for weight, summary in results:
        print(line(f"{weight:g}{' (best)' if weight == best else ''}", summary))
    baseline = summarise(run_days(days), who="baseline")
    baseline["days_baseline_wastes_less"] = baseline["days_baseline_more_on_time"] = "-"
    print(line("baseline", baseline))
    print(f"\nBest by on time, then wasted, then finish: idle weight {best:g}.")


if __name__ == "__main__":
    main()
