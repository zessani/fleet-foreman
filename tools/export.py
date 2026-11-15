"""Export the hand-in files from a running Foreman server.

Start the server (python -m foreman.server), run the demo, then:

    python tools/export.py

It writes these files into exports/:
    log.json, log.csv          the event log: every decision and its reason
    comparison.csv             Foreman vs first come, first served, number by number
    comparison.md              the same comparison as readable tables
    report.json                the raw comparison report from the server
    <scenario>.json            a copy of the farm file that was run

It uses GET /api/state, and also GET /api/log and GET /api/report when the
server has them. Only the Python standard library is used.
"""

import argparse
import csv
import json
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIRS = (ROOT / "scenarios" / "farms", ROOT / "scenarios")

# (key, label, which way is better)
METRICS = [
    ("jobs_done", "Jobs done", "high"),
    ("on_time", "Jobs on time", "high"),
    ("working_share", "Working share of day (%)", "high"),
    ("travel_min", "Travel time (min)", "low"),
    ("idle_min", "Idle time (min)", "low"),
    ("km", "Distance driven (km)", "low"),
    ("violations", "Rule violations", "low"),
]

# Runs in the /api/report answer, in the order they are shown.
RUNS = [
    ("live", "Demo run (as shown on screen)"),
    ("main", "Full day, catalog speeds"),
    ("with_hidden_factors", "Full day, hidden real speeds"),
]


def fetch(url: str) -> str | None:
    """The body of a GET request, or None if the server does not have it."""
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError:
        return None


def improvement(key: str, better: str, foreman, baseline) -> tuple[str, str]:
    """Which side is better and by how much, in words. Same rules as the numbers panel."""
    if foreman is None or baseline is None:
        return "", ""
    if foreman == baseline:
        return ("both", "both zero") if key == "violations" and foreman == 0 else ("same", "same")
    foreman_wins = foreman > baseline if better == "high" else foreman < baseline
    winner = "Foreman" if foreman_wins else "First come"
    if key == "working_share":
        return winner, f"{'+' if foreman_wins else '-'}{abs(foreman - baseline):.1f} pts"
    if baseline == 0:
        return winner, "better" if foreman_wins else "worse"
    pct = round(abs(foreman - baseline) / abs(baseline) * 100)
    if better == "high":
        return winner, f"{'+' if foreman_wins else '-'}{pct}%"
    return winner, f"{pct}% less" if foreman_wins else f"{pct}% more"


def comparison_rows(runs: dict) -> list[dict]:
    rows = []
    for run_key, run_label in RUNS:
        run = runs.get(run_key)
        if not run:
            continue
        for key, label, better in METRICS:
            f = run["foreman"].get(key)
            b = run["baseline"].get(key)
            winner, gain = improvement(key, better, f, b)
            rows.append({"run": run_label, "metric": label, "foreman": f, "baseline": b,
                         "difference": None if f is None or b is None else round(f - b, 1),
                         "better": winner, "improvement": gain})
    return rows


def write_log(lines: list[dict], out: Path) -> None:
    (out / "log.json").write_text(json.dumps(lines, indent=2) + "\n", encoding="utf-8")
    columns = ["t", "clock", "agent", "event", "message", "data"]
    with open(out / "log.csv", "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(columns)
        for line in lines:
            data = line.get("data")
            writer.writerow([line.get("t"), line.get("clock"), line.get("agent"),
                             line.get("event"), line.get("message"),
                             json.dumps(data) if data is not None else ""])


def write_comparison(rows: list[dict], state: dict, report: dict | None, out: Path) -> None:
    columns = ["run", "metric", "foreman", "baseline", "difference", "better", "improvement"]
    with open(out / "comparison.csv", "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    farm_name = (state.get("farm") or {}).get("name", "unknown farm")
    md = [f"# Foreman vs first come, first served", "",
          f"Farm: **{farm_name}**"
          + (f" (scenario `{state['scenario']}`)" if state.get("scenario") else ""),
          f"Clock at export: {state.get('clock', '?')}"
          + (" (day finished)" if state.get("finished") else ""), ""]
    for run_key, run_label in RUNS:
        run_rows = [row for row in rows if row["run"] == run_label]
        if not run_rows:
            continue
        md += [f"## {run_label}", "",
               "| Number | Foreman | First come, first served | Better | Improvement |",
               "|---|---:|---:|---|---|"]
        for row in run_rows:
            md.append(f"| {row['metric']} | {row['foreman']} | {row['baseline']} | "
                      f"{row['better']} | {row['improvement']} |")
        md.append("")

    replans = (report or {}).get("replans") or []
    if replans:
        md += ["## Re-plans", "",
               "| Minute | Events | Re-plan time (ms) | Jobs moved | Retimed | Held |",
               "|---:|---|---:|---:|---:|---:|"]
        for r in replans:
            md.append(f"| {r.get('t')} | {', '.join(r.get('events', []))} | {r.get('ms')} | "
                      f"{r.get('moved', '')} | {r.get('retimed', '')} | {r.get('held', '')} |")
        md.append("")
    (out / "comparison.md").write_text("\n".join(md), encoding="utf-8")


def copy_scenario(state: dict, out: Path, override: str | None) -> str:
    """Copy the farm file that was run. Returns the name of the file written."""
    if override:
        source = Path(override)
    else:
        name = state.get("scenario")
        source = next((d / f"{name}.json" for d in SCENARIO_DIRS
                       if name and (d / f"{name}.json").exists()), None)
    if source is not None and source.exists():
        shutil.copyfile(source, out / source.name)
        return source.name
    # The server did not say which file it ran: save the map it sent instead.
    (out / "scenario_from_state.json").write_text(
        json.dumps(state.get("farm", {}), indent=2) + "\n", encoding="utf-8")
    return "scenario_from_state.json (map only; pass --scenario for the full file)"


def main() -> int:
    parser = argparse.ArgumentParser(description="Save the hand-in files from a running server.")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="server address")
    parser.add_argument("--out", default=str(ROOT / "exports"), help="folder to write into")
    parser.add_argument("--scenario", help="farm file to copy, if not the one the server reports")
    args = parser.parse_args()
    base = args.url.rstrip("/")

    try:
        state = json.loads(fetch(base + "/api/state") or "{}")
    except urllib.error.URLError:
        print(f"Could not reach the server at {base}. Is python -m foreman.server running?")
        return 1
    if not state:
        print(f"The server at {base} did not return /api/state.")
        return 1

    # Full log from /api/log if there is one; otherwise the lines inside the state.
    log_text = fetch(base + "/api/log")
    if log_text is not None:
        log = [json.loads(line) for line in log_text.splitlines() if line.strip()]
    else:
        log = (state.get("foreman") or {}).get("log", [])

    # Comparison from /api/report if there is one; otherwise the totals in the state.
    report_text = fetch(base + "/api/report")
    report = json.loads(report_text) if report_text else None
    if report:
        runs = {key: report[key] for key, _ in RUNS if report.get(key)}
    else:
        runs = {"live": {"foreman": (state.get("foreman") or {}).get("totals", {}),
                         "baseline": (state.get("baseline") or {}).get("totals", {})}}

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_log(log, out)
    rows = comparison_rows(runs)
    write_comparison(rows, state, report, out)
    (out / "report.json").write_text(json.dumps(report or runs, indent=2) + "\n", encoding="utf-8")
    scenario_file = copy_scenario(state, out, args.scenario)

    print(f"Saved to {out}:")
    print(f"  log.json, log.csv      {len(log)} log lines")
    print(f"  comparison.csv/.md     {len(runs)} run(s), {len(rows)} numbers")
    print(f"  report.json")
    print(f"  {scenario_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
