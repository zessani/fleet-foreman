"""The planner's self-tune: replay past days with nearby settings and keep only proven gains.

No model and no network are involved. At the end of a day, Foreman replays the days
it has already run with a few candidate idle weights (the one setting tuned here),
using the same sweep code as `python -m foreman.sweep`. A candidate is adopted only
if it finishes at least as many jobs on time, wastes no more minutes, is strictly
better on one of the two, and breaks no rule. It never adopts anything on fewer than
MIN_DAYS past days.

Commands:  python -m foreman.selftune report [--days N] [--save]
           python -m foreman.selftune reset
           python -m foreman.selftune show
"""

from __future__ import annotations

import copy
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from foreman import evaluator, sweep

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = {"idle_weight": 0.7}
MIN_DAYS = 5          # never adopt on fewer past days than this
HISTORY_KEPT = 12     # replay at most the latest days, so a tune stays fast
WEIGHT_RANGE = (0.2, 2.0)
OFFSETS = (-0.2, -0.1, 0.15, 0.3)
WASTE_NOISE = 0.01    # wasted minutes must drop by at least 1% to count as better


def candidates(current: float) -> list[float]:
    """The current value first, then a few nearby values, clamped and without repeats."""
    low, high = WEIGHT_RANGE
    found = [round(current, 2)]
    for offset in OFFSETS:
        value = round(min(high, max(low, current + offset)), 2)
        if value not in found:
            found.append(value)
    return found


def _wasted_better(candidate: dict, current: dict) -> bool:
    return candidate["wasted"] < current["wasted"] * (1 - WASTE_NOISE)


def beats(candidate: dict, current: dict) -> bool:
    """Not worse on either number, and clearly better on at least one, with no violations."""
    if candidate["violations"] > 0:
        return False
    if candidate["on_time"] < current["on_time"] or candidate["wasted"] > current["wasted"]:
        return False
    return candidate["on_time"] > current["on_time"] or _wasted_better(candidate, current)


def choose(current_score: dict, candidate_scores: list[dict]) -> dict | None:
    """The best candidate that beats the current score, or None. Scores carry idle_weight."""
    winners = [score for score in candidate_scores if beats(score, current_score)]
    if not winners:
        return None
    return min(winners, key=lambda s: (-s["on_time"], s["wasted"],
                                       abs(s["idle_weight"] - current_score["idle_weight"])))


def _percent_less(before: float, after: float) -> int:
    return round(100 * (before - after) / before) if before else 0


def _adopted_sentence(days: int, old: dict, new: dict) -> str:
    gain = new["on_time"] - old["on_time"]
    parts = []
    if gain:
        parts.append(f"{gain} more job{'s' if gain != 1 else ''} on time")
    if _wasted_better(new, old):
        parts.append(f"{_percent_less(old['wasted'], new['wasted'])}% fewer wasted minutes")
    return (f"Self-tune replayed {days} past days and moved the idle weight from "
            f"{old['idle_weight']:g} to {new['idle_weight']:g}: {' and '.join(parts)}, "
            "zero rule violations.")


def score_weight(weight: float, days: list[tuple[str, str, dict]]) -> dict:
    """Replay the days with one idle weight, in this thread only. A live plan never sees it."""
    with evaluator.trial_idle_weight(weight):
        summary = sweep.summarise(sweep.run_days(days))
    return {"idle_weight": weight, "on_time": summary["on_time"], "wasted": round(summary["wasted"], 1),
            "done": summary["done"], "violations": summary["violations"]}


def _read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class SelfTune:
    def __init__(self, folder: Path | None = None) -> None:
        self.folder = folder
        self._settings = dict(DEFAULTS)
        self._days: list[dict] = []
        self._log: list[dict] = []
        self._counter = 0
        if folder is not None:
            folder.mkdir(parents=True, exist_ok=True)
            settings_path = folder / "settings.json"
            if settings_path.exists():
                self._settings.update(json.loads(settings_path.read_text(encoding="utf-8")))
            self._days = _read_lines(folder / "days.jsonl")[-HISTORY_KEPT:]
            self._log = _read_lines(folder / "selftune.jsonl")

    # settings

    def settings(self) -> dict:
        return dict(self._settings)

    def apply(self) -> None:
        evaluator.W_WAIT = self._settings["idle_weight"]

    def reset(self) -> dict:
        """Back to the defaults. Day history stays; the reset is logged."""
        self._settings = dict(DEFAULTS)
        self._save_settings()
        self.apply()
        sentence = f"Settings reset to the defaults: idle weight {DEFAULTS['idle_weight']:g}."
        self._write_log({"at": self._stamp(), "days": len(self._days), "current": DEFAULTS["idle_weight"],
                         "tried": [], "decision": "reset", "adopted": None,
                         "reason": sentence, "sentence": sentence})
        return self.settings()

    # history

    def record_day(self, label: str, raw: dict, events: list[dict]) -> None:
        entry = {"label": label, "raw": copy.deepcopy(raw), "events": copy.deepcopy(events)}
        self._days = (self._days + [entry])[-HISTORY_KEPT:]
        if self.folder is not None:
            with open(self.folder / "days.jsonl", "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")

    def day_count(self) -> int:
        return len(self._days)

    def _replay_days(self) -> list[tuple[str, str, dict]]:
        """Each past day as a farm with its scripted events plus the ones applied by hand."""
        days = []
        for entry in self._days:
            raw = copy.deepcopy(entry["raw"])
            raw["events"] = list(raw.get("events", [])) + list(entry["events"])
            days.append((str(entry["label"]), "replay", raw))
        return days

    # tuning

    def tune(self, score: Callable[[float, list], dict] | None = None) -> dict:
        """Replay the past days with each candidate, then adopt or keep. Logs one record."""
        score = score or score_weight
        current = self._settings["idle_weight"]
        count = len(self._days)
        record = {"at": self._stamp(), "days": count, "current": current, "tried": [],
                  "decision": "kept", "adopted": None}
        if count < MIN_DAYS:
            record["reason"] = f"Self-tune needs {MIN_DAYS} past days before it changes anything; it has {count}."
            record["sentence"] = record["reason"]
            return self._write_log(record)

        days = self._replay_days()
        saved = evaluator.W_WAIT
        try:
            record["tried"] = [score(weight, days) for weight in candidates(current)]
        finally:
            evaluator.W_WAIT = saved
        old = record["tried"][0]
        winner = choose(old, record["tried"][1:])
        if winner is None:
            record["reason"] = ("No candidate finished more jobs on time with fewer wasted minutes "
                                "and zero rule violations.")
            record["sentence"] = (f"Self-tune replayed {count} past days with {len(record['tried'])} settings "
                                  f"and kept the idle weight at {current:g}: no candidate finished more "
                                  "jobs on time with fewer wasted minutes.")
        else:
            self._settings["idle_weight"] = winner["idle_weight"]
            self._save_settings()
            record.update(decision="adopted", adopted=winner["idle_weight"])
            record["sentence"] = _adopted_sentence(count, old, winner)
            record["reason"] = record["sentence"]
        self.apply()
        return self._write_log(record)

    def latest(self) -> dict | None:
        return self._log[-1] if self._log else None

    def history(self, limit: int = 10) -> list[dict]:
        return self._log[-limit:]

    # files

    def _stamp(self) -> str:
        self._counter += 1
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _save_settings(self) -> None:
        if self.folder is not None:
            (self.folder / "settings.json").write_text(json.dumps(self._settings), encoding="utf-8")

    def _write_log(self, record: dict) -> dict:
        self._log.append(record)
        if self.folder is not None:
            with open(self.folder / "selftune.jsonl", "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
        return record


# the report

def _report_farm(k: int) -> tuple[str, dict]:
    """Day k's farm: the two shipped farms, then random farms with fixed seeds."""
    if k == 0:
        name, path = "sample_farm", ROOT / "scenarios" / "farms" / "sample_farm.json"
    elif k == 1:
        name, path = "example_farm", ROOT / "scenarios" / "example_farm.json"
    else:
        tools = str(ROOT / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        from make_farm import make_farm
        return f"random_{100 + k}", make_farm(seed=100 + k)
    return name, json.loads(path.read_text(encoding="utf-8"))


def run_report(days: int = 8, folder: Path | None = None, out=print) -> dict:
    """Run days off screen, self-tuning after each. Starts from the defaults."""
    saved = evaluator.W_WAIT
    try:
        return _run_report(days, folder, out)
    finally:
        evaluator.W_WAIT = saved  # the report must not leave its settings in this process


def _run_report(days: int, folder: Path | None, out) -> dict:
    tuner = SelfTune(folder)
    tuner.reset()
    started = tuner.settings()["idle_weight"]
    adoptions, rows = [], []
    for k in range(days):
        farm, raw = _report_farm(k)
        used = tuner.settings()["idle_weight"]
        with evaluator.trial_idle_weight(used):
            result = sweep.summarise(sweep.run_days([(farm, "day", copy.deepcopy(raw))]))
        tuner.record_day(farm, raw, [])
        began = time.monotonic()
        record = tuner.tune()
        seconds = round(time.monotonic() - began, 1)
        rows.append({"day": k + 1, "farm": farm, "idle_weight": used, "on_time": result["on_time"],
                     "wasted": round(result["wasted"], 1), "sentence": record["sentence"], "seconds": seconds})
        out(f"Day {k + 1} ({farm}): idle weight {used:g}, {result['on_time']} jobs on time, "
            f"{result['wasted']:.0f} wasted minutes. {record['sentence']} [{seconds}s]")
        if record["decision"] == "adopted":
            old = record["tried"][0]
            new = next(t for t in record["tried"] if t["idle_weight"] == record["adopted"])
            adoptions.append({"day": k + 1, "from": record["current"], "to": record["adopted"],
                              "before": old, "after": new})
    final = tuner.settings()["idle_weight"]
    out(f"\nStarted with idle weight {started:g}.")
    for item in adoptions:
        out(f"After day {item['day']}: {item['from']:g} to {item['to']:g}. Replay before: "
            f"{item['before']['on_time']} on time, {item['before']['wasted']:.0f} wasted. "
            f"After: {item['after']['on_time']} on time, {item['after']['wasted']:.0f} wasted.")
    if not adoptions:
        out(f"Nothing was adopted: {final:g} was already the best of the settings tried on every replay.")
    out(f"Final idle weight: {final:g}.")
    return {"started": started, "final": final, "adoptions": adoptions, "days": rows}


def main(argv: list[str]) -> None:
    command = argv[0] if argv else "show"
    folder = ROOT / "runs" / "selftune"
    if command == "report":
        count = int(argv[argv.index("--days") + 1]) if "--days" in argv else 8
        run_report(count, folder if "--save" in argv else None)
    elif command == "reset":
        print("Settings:", SelfTune(folder).reset())
    elif command == "show":
        tuner = SelfTune(folder)
        latest = tuner.latest()
        print("Settings:", tuner.settings())
        print(latest["sentence"] if latest else "No self-tune has run yet.")
    else:
        print("Usage: python -m foreman.selftune report [--days N] [--save] | reset | show")


if __name__ == "__main__":
    main(sys.argv[1:])
