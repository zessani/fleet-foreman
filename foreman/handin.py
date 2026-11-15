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


def explanations_md(day: Day, title: str | None = None, head: bool = True) -> str:
    """Each plan change in words. With a title, plans sit one heading level lower."""
    mark = "###" if title else "##"
    parts = ["# Why the plan changed", ""] if head else []
    if title:
        parts += [f"## {title}", ""]
    for item in day.foreman.explanations:
        when = clock(day.farm.day_start_min, item["t"])
        parts += [f"{mark} Plan {item['plan_no']} at {when}", "", item["headline"], "",
                  item["summary"], "", item["safety"], "",
                  f"Re-plan time: {item['ms']} ms from event to new plan.", ""]
        parts += [f"- {change}" for change in item["changes"]]
        parts.append("")
    return "\n".join(parts)


TEST_DAY_ROWS = [
    ("on_time", "Jobs on time"),
    ("wasted_min", "Wasted minutes"),
    ("idle_min", "Idle minutes"),
    ("violations", "Rule violations"),
    ("jobs_done", "Jobs completed"),
    ("travel_min", "Travel minutes"),
    ("service_min", "Yard service minutes"),
]


def day_name(row: dict, tractor_names: dict) -> str:
    """For example "example farm, Cedar breaks at minute 45", or "example farm, quiet day"."""
    if row["label"] == "quiet":
        return f"{row['farm']} farm, quiet day"
    tractor_id, minute = row["label"].split("@")
    who = tractor_names.get(row["farm"], {}).get(tractor_id, tractor_id)
    return f"{row['farm']} farm, {who} breaks at minute {minute}"


def test_days_section(days: list | None = None) -> tuple[str, dict]:
    """The "All test days" markdown and its data. days=None runs the 196 test days."""
    from foreman import sweep

    days = sweep.test_days() if days is None else days
    names: dict = {}
    for farm, _, raw in days:
        names.setdefault(farm, {t["id"]: t["name"] for t in raw["tractors"]})
    rows = sweep.run_days(days)
    farms = sorted({row["farm"] for row in rows})
    total_jobs = sum(row["jobs"] for row in rows)
    totals = {who: {key: sum(row[who][key] for row in rows) for key, _ in TEST_DAY_ROWS}
              for who in ("foreman", "baseline")}
    diff = {key: round(totals["foreman"][key] - totals["baseline"][key], 1)
            for key, _ in TEST_DAY_ROWS}
    wins = [day_name(row, names) for row in rows
            if row["baseline"]["on_time"] > row["foreman"]["on_time"]]

    lines = ["## All test days", "",
             f"{len(rows)} days on {len(farms)} farms: every farm run quiet and with each "
             f"tractor breaking at 10 different times, hidden speed factors off, "
             f"{total_jobs} jobs in total.", "",
             "| Number | Foreman | First come first served | Difference |", "|---|---|---|---|"]
    for key, label in TEST_DAY_ROWS:
        f, b = totals["foreman"][key], totals["baseline"][key]
        lines.append(f"| {label} | {f:g} | {b:g} | {diff[key]:+g} |")
    if not wins:
        sentence = ("On none of the test days does first come, first served finish more "
                    "jobs on time than Foreman.")
    else:
        count = "1 of the" if len(wins) == 1 else f"{len(wins)} of the"
        sentence = (f"On {count} {len(rows)} test days first come, first served finishes more "
                    f"jobs on time than Foreman: {'; '.join(wins)}.")
    lines += ["", sentence, ""]
    data = {"days": len(rows), "farms": len(farms), "jobs_total": total_jobs,
            "foreman": totals["foreman"], "baseline": totals["baseline"], "difference": diff,
            "days_baseline_more_on_time": wins}
    return "\n".join(lines), data


def readme_text(command: str, test_days: bool = False, builtin: bool = False) -> str:
    extra = ["comparison.md also has an \"All test days\" section: the 196 test days, "
             "Foreman against first come, first served."] if test_days else []
    more = ["scenario_large_farm.json and event_log_large_farm.jsonl: the same for the large farm."
            ] if builtin else []
    return "\n".join([
        "scenario.json: the farm file exactly as it was loaded.",
        "event_log.jsonl: every event of the day, one JSON object per line.",
        *more,
        "comparison.md and comparison.json: Foreman against first come, first served.",
        *extra,
        "Section list of comparison.md: the day itself, and with --test-days the "
        "\"All test days\" section (also under \"test_days\" in comparison.json).",
        "To make these files again: python -m foreman.handin",
        "With the 196 test days: python -m foreman.handin --test-days",
        "The busy day (scenarios/busy_day.json, written to handin/busy_day/): "
        "python -m foreman.handin --scenario busy_day",
        "explanations.md: each plan change of the day, explained in plain words.",
        f"README.md: this file. Made with: {command}",
    ]) + "\n"


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


BUILTIN = [
    ("sample_farm", "Sample farm", "Sample farm: 3 tractors, 10 jobs, Cedar fails at 09:30"),
    ("large_farm", "Large farm", "Large farm: 5 tractors and 25 jobs"),
]


def run_scenario(path: Path, use_truth: bool) -> dict:
    path = Path(path)
    day = Day(json.loads(path.read_text(encoding="utf-8")), use_truth=use_truth)
    day.advance(day.farm.horizon_min)
    foreman, baseline = day.foreman.sim.totals(), day.baseline.sim.totals()
    diff = differences(foreman, baseline)
    events = [describe_event(e, day) for e in day.farm.events]
    data = {"scenario": path.name, "farm": day.farm.name, "hidden_factors": use_truth,
            "events": events, "foreman": foreman, "baseline": baseline, "difference": diff,
            "replans": day.foreman.replans}
    return {"day": day, "path": path, "events": events, "data": data,
            "table": comparison_table(day, foreman, baseline, diff)}


def write_builtin(out_dir: Path, use_truth: bool = False, command: str | None = None,
                  test_days: bool = False, days: list | None = None) -> dict:
    """Both built-in scenarios in one folder, then the test days if asked."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md = ["# Foreman against first come, first served", ""]
    explain = ["# Why the plan changed", ""]
    scenarios: dict = {}
    for name, title, description in BUILTIN:
        path = find_scenario(name)
        run = run_scenario(path, use_truth)
        day = run["day"]
        suffix = "" if name == "sample_farm" else f"_{name}"
        (out_dir / f"scenario{suffix}.json").write_text(path.read_text(encoding="utf-8"),
                                                        encoding="utf-8")
        with open(out_dir / f"event_log{suffix}.jsonl", "w", encoding="utf-8") as log_file:
            for line in day.memory.log.lines():
                log_file.write(json.dumps(line) + "\n")
        md += [f"## {title}", "", description, "", f"Scenario file: {path.name}",
               f"Hidden speed factors: {'on' if use_truth else 'off'}", "", "Scripted events:"]
        md += [f"- {text}" for text in run["events"]] or ["- none"]
        md += ["", run["table"], "", "Re-plan times:"]
        for plan in day.foreman.replans:
            when = clock(day.farm.day_start_min, plan["t"])
            md.append(f"- Plan {plan['plan_no']} at {when}: {plan['ms']} ms from event to new plan")
        md.append("")
        explain.append(explanations_md(day, title, head=False))
        scenarios[name] = run["data"]
    result = {"scenarios": scenarios}
    text = "\n".join(md)
    if test_days:
        section, result["test_days"] = test_days_section(days)
        text += "\n" + section
    (out_dir / "comparison.md").write_text(text, encoding="utf-8")
    write_json(out_dir / "comparison.json", result)
    (out_dir / "explanations.md").write_text("\n".join(explain), encoding="utf-8")
    command = command or "python -m foreman.handin"
    (out_dir / "README.md").write_text(readme_text(command, test_days, builtin=True),
                                       encoding="utf-8")
    return result


def write_handin(scenario_path: Path, out_dir: Path, use_truth: bool = False,
                 command: str | None = None, test_days: bool = False,
                 days: list | None = None) -> dict:
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

    section = ""
    if test_days:
        section, data["test_days"] = test_days_section(days)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scenario.json").write_text(scenario_path.read_text(encoding="utf-8"),
                                           encoding="utf-8")
    with open(out_dir / "event_log.jsonl", "w", encoding="utf-8") as log_file:
        for line in day.memory.log.lines():
            log_file.write(json.dumps(line) + "\n")
    (out_dir / "comparison.md").write_text(
        comparison_md(day, scenario_path, use_truth, events, table)
        + ("\n" + section if section else ""), encoding="utf-8")
    write_json(out_dir / "comparison.json", data)
    (out_dir / "explanations.md").write_text(explanations_md(day), encoding="utf-8")
    command = command or "python -m foreman.handin"
    (out_dir / "README.md").write_text(readme_text(command, test_days), encoding="utf-8")
    data["table"] = table
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m foreman.handin")
    parser.add_argument("--scenario", help="farm name, for example sample_farm")
    parser.add_argument("--out", default=None,
                        help="folder to write (default: handin/, or handin/NAME for --scenario NAME)")
    parser.add_argument("--test-days", action="store_true",
                        help="also run the 196 test days (about a minute)")
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
    builtin = not args.scenario
    if args.out:
        out = Path(args.out)
    elif args.scenario and path != default_scenario():
        out = DEFAULT_OUT / args.scenario
    else:
        out = DEFAULT_OUT
    if args.test_days:
        print("Running the 196 test days, this takes about a minute.", flush=True)
    command = "python -m foreman.handin" + "".join(
        f" {part}" for part in (sys.argv[1:] if argv is None else argv))
    if builtin:
        write_builtin(out, use_truth=args.hidden_factors, command=command,
                      test_days=args.test_days)
        text = (out / "comparison.md").read_text(encoding="utf-8")
        print(text)
        print(f"Hand-in folder: {out.resolve()}")
        return 0
    data = write_handin(path, out, use_truth=args.hidden_factors, command=command,
                       test_days=args.test_days)
    print(data["table"])
    print(f"\nHand-in folder: {out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
