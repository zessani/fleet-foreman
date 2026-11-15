"""Run the whole demo day off screen and write the hand-in files into one folder.

No AI model and no network, so the files are the same every time (apart from
the re-plan timings, which are measured on the clock).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from foreman.day import Day
from foreman.engine import ROOT, default_scenario, find_scenario
from foreman.model import clock

DEFAULT_OUT = ROOT / "handin"

# (key, label) in the order the table shows them.
ROWS = [
    ("jobs_done", "Jobs completed"),
    ("on_time", "Jobs on time"),
    ("travel_min", "Travel time in minutes"),
    ("idle_min", "Idle time in minutes"),
    ("service_min", "Yard service in minutes"),
    ("wasted_min", "Wasted minutes: travel + idle + service"),
    ("km", "Distance driven in km"),
    ("working_share", "Working share of the day in %"),
    ("finish_min", "Day finished at"),
    ("violations", "Rule violations"),
]
NUMBER_KEYS = [key for key, _ in ROWS if key != "finish_min"]


def describe_event(event: dict, day: Day) -> str:
    """One scripted event in plain words, for example "09:30 Cedar breaks down"."""
    when = clock(day.farm.day_start_min, event["at"])
    tractor = day.farm.tractors.get(event.get("tractor", ""))
    who = tractor.name if tractor else event.get("tractor", "")
    kind = event["type"]
    if kind == "breakdown":
        return f"{when} {who} breaks down"
    if kind == "repair":
        return f"{when} {who} is repaired"
    return f"{when} {kind.replace('_', ' ')}"


def differences(foreman: dict, baseline: dict) -> dict:
    """Foreman minus baseline for every plain number."""
    return {key: round(foreman[key] - baseline[key], 1) for key in NUMBER_KEYS}


def format_value(key: str, value: float, total_jobs: int) -> str:
    if key == "jobs_done":
        return f"{int(value)} of {total_jobs}"
    return f"{value:g}"


def finish_text(totals: dict, total_jobs: int, day: Day) -> str:
    if totals["jobs_done"] < total_jobs:
        return "not finished"
    return clock(day.farm.day_start_min, totals["finish_min"])


def comparison_table(day: Day, foreman: dict, baseline: dict, diff: dict) -> str:
    total_jobs = len(day.farm.jobs)
    lines = ["| Number | Foreman | First come first served | Difference |", "|---|---|---|---|"]
    for key, label in ROWS:
        if key == "finish_min":
            f, b = finish_text(foreman, total_jobs, day), finish_text(baseline, total_jobs, day)
            # A gap in finish time only means something when both days finished.
            both = "not finished" not in (f, b)
            gap = f"{round(foreman[key] - baseline[key], 1):+g} min" if both else "n/a"
        else:
            f = format_value(key, foreman[key], total_jobs)
            b = format_value(key, baseline[key], total_jobs)
            gap = f"{diff[key]:+g}"
        lines.append(f"| {label} | {f} | {b} | {gap} |")
    return "\n".join(lines)


def comparison_md(day: Day, scenario_path: Path, hidden: bool, events: list[str],
                  table: str) -> str:
    head = [
        f"# {day.farm.name}: Foreman against first come, first served",
        "",
        f"Farm: {day.farm.name}",
        f"Scenario file: {scenario_path.name}",
        f"Hidden speed factors: {'on' if hidden else 'off'}",
        "",
        "Scripted events:",
    ]
    head += [f"- {text}" for text in events] or ["- none"]
    tail = ["", "Re-plan times:"]
    for plan in day.foreman.replans:
        when = clock(day.farm.day_start_min, plan["t"])
        tail.append(f"- Plan {plan['plan_no']} at {when}: {plan['ms']} ms from event to new plan")
    return "\n".join(head + ["", table] + tail) + "\n"


def explanations_md(day: Day) -> str:
    parts = ["# Why the plan changed", ""]
    for item in day.foreman.explanations:
        when = clock(day.farm.day_start_min, item["t"])
        parts += [f"## Plan {item['plan_no']} at {when}", "", item["headline"], "",
                  item["summary"], "", item["safety"], "",
                  f"Re-plan time: {item['ms']} ms from event to new plan.", ""]
        parts += [f"- {change}" for change in item["changes"]]
        parts.append("")
    return "\n".join(parts)


def readme_text(command: str) -> str:
    return "\n".join([
        "scenario.json: the farm file exactly as it was loaded.",
        "event_log.jsonl: every event of the day, one JSON object per line.",
        "comparison.md and comparison.json: Foreman against first come, first served.",
        "explanations.md: each plan change of the day, explained in plain words.",
        f"README.md: this file. Made with: {command}",
    ]) + "\n"


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_handin(scenario_path: Path, out_dir: Path, use_truth: bool = False,
                 command: str | None = None) -> dict:
    """Run the day, write the folder, and return the comparison data."""
    scenario_path, out_dir = Path(scenario_path), Path(out_dir)
    raw = json.loads(scenario_path.read_text(encoding="utf-8"))
    day = Day(raw, use_truth=use_truth)
    day.advance(day.farm.horizon_min)

    foreman, baseline = day.foreman.sim.totals(), day.baseline.sim.totals()
    diff = differences(foreman, baseline)
    events = [describe_event(e, day) for e in day.farm.events]
    table = comparison_table(day, foreman, baseline, diff)
    data = {
        "scenario": scenario_path.name, "farm": day.farm.name, "hidden_factors": use_truth,
        "events": events, "foreman": foreman, "baseline": baseline, "difference": diff,
        "replans": day.foreman.replans,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scenario.json").write_text(scenario_path.read_text(encoding="utf-8"),
                                           encoding="utf-8")
    with open(out_dir / "event_log.jsonl", "w", encoding="utf-8") as log_file:
        for line in day.memory.log.lines():
            log_file.write(json.dumps(line) + "\n")
    (out_dir / "comparison.md").write_text(
        comparison_md(day, scenario_path, use_truth, events, table), encoding="utf-8")
    write_json(out_dir / "comparison.json", data)
    (out_dir / "explanations.md").write_text(explanations_md(day), encoding="utf-8")
    command = command or "python -m foreman.handin"
    (out_dir / "README.md").write_text(readme_text(command), encoding="utf-8")
    data["table"] = table
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m foreman.handin")
    parser.add_argument("--scenario", help="farm name, for example sample_farm")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="folder to write")
    parser.add_argument("--hidden-factors", action="store_true",
                        help="run with the farm's hidden speed factors on")
    args = parser.parse_args(argv)

    if args.scenario:
        path = find_scenario(args.scenario)
        if path is None:
            print(f"No farm called '{args.scenario}'. Look in the scenarios folder.")
            return 1
    else:
        path = default_scenario()
    out = Path(args.out)
    command = "python -m foreman.handin" + "".join(
        f" {part}" for part in (sys.argv[1:] if argv is None else argv))
    data = write_handin(path, out, use_truth=args.hidden_factors, command=command)
    print(data["table"])
    print(f"\nHand-in folder: {out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
