"""Measure whether the resilience step changes any day plan, and whether the day ends better.

Run with:  python -m foreman.resilience_report [--seeds N]

Every day is run twice in this process, once with the step on and once with it
off, with hidden speed factors off and no model. The step is switched off by
swapping foreman.planner.improve_within_tolerance for a function that returns
an unchanged choice, and the original is put back afterwards. The days are the
196 test days of foreman.sweep plus random farms (make_farm seeds 1..N), each
run quiet and with each tractor breaking at three fixed times.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import time
from typing import Iterator

from foreman import planner
from foreman.day import Day
from foreman.resilience import ResilienceChoice, improve_within_tolerance
from foreman.sweep import test_days
from tools.make_farm import make_farm

DEFAULT_SEEDS = 40
BREAK_TIMES = (60, 180, 300)
WASTE_MARGIN = 0.01  # same jobs on time: "better" needs at least 1% fewer wasted minutes
Day_ = tuple[str, str, dict]


def _unchanged(*_args, **_kwargs) -> ResilienceChoice:
    """What the planner gets when the step is off: no alternative, nothing moved."""
    return ResilienceChoice(0.0, 0.0, 0.0, 0.0, 0, False)


@contextlib.contextmanager
def step_enabled(enabled: bool) -> Iterator[None]:
    """Turn the resilience step on or off for the block, then restore it."""
    original = planner.improve_within_tolerance
    planner.improve_within_tolerance = original if enabled else _unchanged
    try:
        yield
    finally:
        planner.improve_within_tolerance = original


@contextlib.contextmanager
def recording(changes: list[dict]) -> Iterator[None]:
    """Record every plan the step changed: when, the routes before and after, its own numbers."""
    wrapped = planner.improve_within_tolerance

    def record(farm, road_map, world, evaluator, score, order, locked):
        before = {tractor: list(route) for tractor, route in order.items()}
        choice = wrapped(farm, road_map, world, evaluator, score, order, locked)
        if choice.changed:
            changes.append({"at": world.now, "before": before,
                            "after": {t: list(r) for t, r in order.items()},
                            "numbers": choice.to_dict()})
        return choice

    planner.improve_within_tolerance = record
    try:
        yield
    finally:
        planner.improve_within_tolerance = wrapped


def run_one(raw: dict, enabled: bool) -> dict:
    """One full day. Returns Foreman's totals and the plans the step changed."""
    changes: list[dict] = []
    with step_enabled(enabled), recording(changes):
        day = Day(copy.deepcopy(raw), use_truth=False)
        day.advance(day.farm.horizon_min)
    totals = day.foreman.sim.totals()
    return {"on_time": totals["on_time"], "done": totals["jobs_done"],
            "wasted": totals["wasted_min"], "finish": totals["finish_min"],
            "violations": totals["violations"], "changes": changes}


def random_days(seeds: int) -> list[Day_]:
    days: list[Day_] = []
    for seed in range(1, seeds + 1):
        raw = make_farm(seed=seed)
        raw["events"] = []
        name = f"random{seed}"
        days.append((name, "quiet", raw))
        for tractor in raw["tractors"]:
            for minute in BREAK_TIMES:
                broken = copy.deepcopy(raw)
                broken["events"] = [{"at": minute, "type": "breakdown", "tractor": tractor["id"]}]
                days.append((name, f"{tractor['id']}@{minute}", broken))
    return days


def verdict(on: dict, off: dict) -> str:
    """better, same or worse for the step being on, judged on jobs on time, then wasted minutes."""
    if on["on_time"] != off["on_time"]:
        return "better" if on["on_time"] > off["on_time"] else "worse"
    margin = WASTE_MARGIN * max(off["wasted"], 1.0)
    if on["wasted"] < off["wasted"] - margin:
        return "better"
    if on["wasted"] > off["wasted"] + margin:
        return "worse"
    return "same"


def compare(days: list[Day_]) -> list[dict]:
    rows = []
    for farm, label, raw in days:
        on, off = run_one(raw, True), run_one(raw, False)
        rows.append({"farm": farm, "label": label, "on": on, "off": off,
                     "plans_changed": len(on["changes"]), "verdict": verdict(on, off)})
    return rows


def summarise(rows: list[dict]) -> dict:
    changed = [row for row in rows if row["plans_changed"]]
    count = lambda kind: sum(1 for row in changed if row["verdict"] == kind)
    totals = {side: {key: sum(row[side][key] for row in rows)
                     for key in ("on_time", "done", "wasted", "violations")}
              for side in ("on", "off")}
    differing = [row for row in rows if row["on"] != row["off"] and
                 (row["on"]["on_time"], row["on"]["wasted"], row["on"]["finish"])
                 != (row["off"]["on_time"], row["off"]["wasted"], row["off"]["finish"])]
    differing.sort(key=lambda row: (-(row["on"]["on_time"] - row["off"]["on_time"]),
                                    row["on"]["wasted"] - row["off"]["wasted"]))
    return {"days": len(rows), "changed": len(changed), "better": count("better"),
            "same": count("same"), "worse": count("worse"),
            "plans_changed": sum(row["plans_changed"] for row in rows),
            "differing": differing, "totals": totals}


def _line(row: dict) -> str:
    on, off = row["on"], row["off"]
    return (f"{row['farm']:>10} {row['label']:>8}  plans changed {row['plans_changed']}  "
            f"on time {on['on_time']} vs {off['on_time']}  wasted {on['wasted']:.1f} vs "
            f"{off['wasted']:.1f}  finish {on['finish']:.0f} vs {off['finish']:.0f}  "
            f"-> {row['verdict']}")


def print_report(summary: dict, seconds: float) -> None:
    print(f"Days: {summary['days']}. Days where the step changed at least one plan: "
          f"{summary['changed']} ({summary['plans_changed']} plans).")
    print(f"Of those, ended better {summary['better']}, same {summary['same']}, "
          f"worse {summary['worse']} (on = step on, off = step off).")
    print("\nDays whose outcome differs, best first:")
    for row in summary["differing"]:
        print("  " + _line(row))
    if not summary["differing"]:
        print("  none")
    for side in ("on", "off"):
        t = summary["totals"][side]
        print(f"\nTotals, step {side}: {t['on_time']} jobs on time, {t['done']} done, "
              f"{t['wasted']:.0f} wasted minutes, {t['violations']} violations.")
    print(f"\nTook {seconds:.0f} s.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, default=DEFAULT_SEEDS,
                        help="random farms make_farm(seed=1..N) to add to the test days")
    args = parser.parse_args()
    started = time.perf_counter()
    rows = compare(test_days() + random_days(args.seeds))
    print_report(summarise(rows), time.perf_counter() - started)


if __name__ == "__main__":
    main()
