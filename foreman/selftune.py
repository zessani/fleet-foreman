"""The planner's self-tune: replay past days with nearby settings and keep only proven gains.

No model and no network are involved. At the end of a day, Foreman replays the days
it has already run with candidate settings (the allow-list TUNABLE: the idle weight and
the two stability costs, one changed at a time), using the same sweep code as `python -m foreman.sweep`. A candidate is adopted only
if it finishes at least as many jobs on time, wastes no more minutes, is strictly
better on one of the two, and breaks no rule. Held-out check: the past days are split by
position (every third day is held out). Candidates are scored, and the winner picked, on the
tuning days only. The single winner is then scored once on the held-out days and adopted only
if it wins there too by the same rule. It never adopts anything on fewer than MIN_DAYS past days.

Commands:  python -m foreman.selftune report [--days N] [--save]
           python -m foreman.selftune farms
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
from contextlib import contextmanager
from typing import Callable

from foreman import evaluator, planner, sweep

ROOT = Path(__file__).resolve().parent.parent
HELD_EVERY = 3        # every third past day (the 3rd, 6th, 9th ...) is held out
MIN_HELD_OUT = 2      # a held-out check on one day would be too thin
MIN_TUNING = 3        # and so would tuning on fewer than three
HISTORY_KEPT = 12     # replay at most the latest days, so a tune stays fast
WASTE_NOISE = 0.01    # wasted minutes must drop by at least 1% to count as better

# The allow-list: the only settings the self-tune may change. Each has its default, its
# allowed range and the offsets tried around the current value. These are tastes about
# trade-offs (how much idle time to accept, how readily a job moves to another tractor),
# never safety. FUEL_MARGIN, FUEL_RESERVE, REFUEL_BELOW, SWAP_MIN, REFUEL_MIN, the cost of
# leaving a job out (W_UNPLANNED), the lateness weights and every check in foreman/safety.py
# are deliberately absent, so no code path here can reach them. A settings file that names
# anything else is refused.
TUNABLE = {
    "idle_weight": {"default": 0.7, "range": (0.2, 2.0), "offsets": (-0.2, -0.1, 0.15, 0.3),
                    "label": "the idle weight"},
    "stability_strict": {"default": 500.0, "range": (100.0, 1500.0), "offsets": (-150.0, 300.0),
                         "label": "the stability cost after a breakdown"},
    "stability_light": {"default": 15.0, "range": (5.0, 60.0), "offsets": (-10.0, 15.0),
                        "label": "the stability cost when the plan may rebalance"},
}
DEFAULTS = {name: spec["default"] for name, spec in TUNABLE.items()}
# Where each adopted value lives, for apply() and for restoring after a trial.
_CONSTANTS = (("idle_weight", evaluator, "W_WAIT"), ("stability_strict", planner, "CHANGE_STRICT"),
              ("stability_light", planner, "CHANGE_LIGHT"))


def check_settings(settings: dict) -> dict:
    """The settings if every name is on the allow-list and every value is in range, else ValueError."""
    for name, value in settings.items():
        if name not in TUNABLE:
            raise ValueError(f"Self-tune refuses to tune {name!r}: it is not on the allow-list "
                             f"({', '.join(TUNABLE)}).")
        low, high = TUNABLE[name]["range"]
        if not isinstance(value, (int, float)) or not low <= value <= high:
            raise ValueError(f"Self-tune setting {name!r} must be between {low:g} and {high:g}, got {value!r}.")
    return dict(settings)


def candidates(current: float, name: str = "idle_weight") -> list[float]:
    """One setting's current value first, then a few nearby values, clamped and without repeats."""
    low, high = TUNABLE[name]["range"]
    found = [round(current, 2)]
    for offset in TUNABLE[name]["offsets"]:
        value = round(min(high, max(low, current + offset)), 2)
        if value not in found:
            found.append(value)
    return found


def candidate_settings(current: dict) -> list[dict]:
    """The current settings first, then each candidate with exactly one setting changed."""
    found = [dict(current)]
    for name in TUNABLE:
        for value in candidates(current[name], name)[1:]:
            found.append({**current, name: value})
    return found


def changed_setting(old: dict, new: dict) -> str | None:
    return next((name for name in TUNABLE if old[name] != new[name]), None)


@contextmanager
def trial(settings: dict):
    """Try a whole candidate in this thread only. A live plan in another thread never sees it."""
    with evaluator.trial_idle_weight(settings["idle_weight"]), \
            planner.trial_stability(settings["stability_strict"], settings["stability_light"]):
        yield


@contextmanager
def constants_kept():
    """Whatever happens inside, the module constants are back to their old values afterwards."""
    saved = [(module, attribute, getattr(module, attribute)) for _, module, attribute in _CONSTANTS]
    try:
        yield
    finally:
        for module, attribute, value in saved:
            setattr(module, attribute, value)


def _wasted_better(candidate: dict, current: dict) -> bool:
    return candidate["wasted"] < current["wasted"] * (1 - WASTE_NOISE)


def beats(candidate: dict, current: dict) -> bool:
    """Not worse on either number, and clearly better on at least one, with no violations."""
    if candidate["violations"] > 0:
        return False
    if candidate["on_time"] < current["on_time"] or candidate["wasted"] > current["wasted"]:
        return False
    return candidate["on_time"] > current["on_time"] or _wasted_better(candidate, current)


def _distance(score: dict, current: dict) -> float:
    """How far a candidate is from the current settings, as a share of the allowed range."""
    name = changed_setting(current, score["settings"])
    if name is None:
        return 0.0
    low, high = TUNABLE[name]["range"]
    return abs(score["settings"][name] - current[name]) / (high - low)


def choose(current_score: dict, candidate_scores: list[dict]) -> dict | None:
    """The best candidate that beats the current score, or None. Scores carry their settings."""
    winners = [score for score in candidate_scores if beats(score, current_score)]
    if not winners:
        return None
    return min(winners, key=lambda s: (-s["on_time"], s["wasted"], _distance(s, current_score["settings"])))


def split_days(count: int) -> tuple[list[int], list[int]]:
    """Positions of the tuning days and of the held-out days. Fixed by position, never by score."""
    held = [k for k in range(count) if k % HELD_EVERY == HELD_EVERY - 1]
    return [k for k in range(count) if k not in held], held


def _enough_days(count: int) -> bool:
    tuning, held = split_days(count)
    return len(held) >= MIN_HELD_OUT and len(tuning) >= MIN_TUNING


# The smallest history that leaves enough days on both sides: 6 days (4 to tune on, 2 held out).
MIN_DAYS = next(n for n in range(1, 100) if _enough_days(n))
HISTORY_KEPT = max(HISTORY_KEPT, MIN_DAYS)


def _percent_less(before: float, after: float) -> int:
    return round(100 * (before - after) / before) if before else 0


def _gain_phrase(old: dict, new: dict) -> str:
    parts = []
    gain = new["on_time"] - old["on_time"]
    if gain:
        parts.append(f"{gain} more job{'s' if gain != 1 else ''} on time")
    if new["wasted"] < old["wasted"]:
        parts.append(f"{_percent_less(old['wasted'], new['wasted'])}% fewer wasted minutes")
    return ", ".join(parts) or "no change"


def _lead(tried: int, tuning_days: int, held_days: int) -> str:
    return (f"Self-tune tried {tried} settings on {tuning_days} past days and checked the best one on "
            f"{held_days} days it had not tuned on.")


def _adopted_sentence(tried: int, tuning: dict, held: dict) -> str:
    name = changed_setting(tuning["current"]["settings"], tuning["winner"]["settings"])
    old_value = tuning["current"]["settings"][name]
    new_value = tuning["winner"]["settings"][name]
    return (f"{_lead(tried, tuning['days'], held['days'])} It moved {TUNABLE[name]['label']} from "
            f"{old_value:g} to {new_value:g}: {_gain_phrase(tuning['current'], tuning['winner'])} on the "
            f"tuning days and {_gain_phrase(held['current'], held['winner'])} on the held-out days, "
            "no fewer jobs on time, zero rule violations.")


def _held_out_failure(winner: dict, current: dict) -> str:
    """Why the winner did not hold on the held-out days, in plain words."""
    if winner["violations"] > 0:
        return f"{winner['violations']} rule violations"
    if winner["on_time"] < current["on_time"]:
        lost = current["on_time"] - winner["on_time"]
        return f"{lost} fewer job{'s' if lost != 1 else ''} on time"
    if winner["wasted"] > current["wasted"]:
        return f"{_percent_less(current['wasted'], winner['wasted']) * -1}% more wasted minutes"
    return "no clear gain, a tie within 1%"


def _rejected_sentence(tried: int, tuning: dict, held: dict) -> str:
    winner = tuning["winner"]
    name = changed_setting(tuning["current"]["settings"], winner["settings"])
    return (f"{_lead(tried, tuning['days'], held['days'])} The best one on the tuning days "
            f"({TUNABLE[name]['label']} {winner['settings'][name]:g}) did not hold on the "
            f"{held['days']} held-out days ({_held_out_failure(held['winner'], held['current'])}), "
            "so every setting stays as it was.")


def score_settings(settings: dict, days: list[tuple[str, str, dict]]) -> dict:
    """Replay the days with one candidate, in this thread only."""
    with trial(settings):
        summary = sweep.summarise(sweep.run_days(days))
    return {"settings": dict(settings), "on_time": summary["on_time"], "wasted": round(summary["wasted"], 1),
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
                # An older file holds only idle_weight; the other settings keep their defaults.
                self._settings.update(check_settings(json.loads(settings_path.read_text(encoding="utf-8"))))
            self._days = _read_lines(folder / "days.jsonl")[-HISTORY_KEPT:]
            self._log = _read_lines(folder / "selftune.jsonl")

    # settings

    def settings(self) -> dict:
        return dict(self._settings)

    def apply(self) -> None:
        """Write the adopted values to the module constants the planner reads."""
        for name, module, attribute in _CONSTANTS:
            setattr(module, attribute, self._settings[name])

    def reset(self) -> dict:
        """Back to the defaults. Day history stays; the reset is logged."""
        self._settings = dict(DEFAULTS)
        self._save_settings()
        self.apply()
        sentence = ("Settings reset to the defaults: " + ", ".join(
            f"{TUNABLE[name]['label']} {value:g}" for name, value in DEFAULTS.items()) + ".")
        self._write_log({"at": self._stamp(), "days": len(self._days), "current": self.settings(),
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

    def tune(self, score: Callable[[dict, list], dict] | None = None) -> dict:
        """Pick a winner on the tuning days, test it once on the held-out days, then adopt or keep."""
        score = score or score_settings
        current = self.settings()
        count = len(self._days)
        record = {"at": self._stamp(), "days": count, "current": current, "tried": [],
                  "decision": "kept", "adopted": None, "changed": None, "tuning": None, "held_out": None}
        if not _enough_days(count):
            tuning_n, held_n = (len(part) for part in split_days(MIN_DAYS))
            record["reason"] = (f"Self-tune needs {MIN_DAYS} past days ({tuning_n} to tune on, {held_n} held out) "
                                f"before it changes anything; it has {count}, so it needs {MIN_DAYS - count} more.")
            record["sentence"] = record["reason"]
            return self._write_log(record)

        tuning_at, held_at = split_days(count)
        everything = self._replay_days()
        tuning_days = [everything[k] for k in tuning_at]
        held_days = [everything[k] for k in held_at]
        with constants_kept():
            record["tried"] = [score(settings, tuning_days) for settings in candidate_settings(current)]
        old = record["tried"][0]
        winner = choose(old, record["tried"][1:])
        record["tuning"] = {"days": len(tuning_days), "current": old, "winner": winner}
        if winner is None:
            record["reason"] = ("No candidate finished more jobs on time with fewer wasted minutes "
                                "and zero rule violations on the tuning days.")
            record["sentence"] = (f"Self-tune tried {len(record['tried'])} settings on {len(tuning_days)} past days "
                                  "and kept every setting as it was: no candidate finished more "
                                  "jobs on time with fewer wasted minutes.")
        else:
            self._test_held_out(record, score, held_days)
        self.apply()
        return self._write_log(record)

    def _test_held_out(self, record: dict, score: Callable, held_days: list) -> None:
        """Score only the winner (and the current settings) on days it never tuned on."""
        old, winner = record["tuning"]["current"], record["tuning"]["winner"]
        with constants_kept():
            held_old = score(old["settings"], held_days)
            held_new = score(winner["settings"], held_days)
        held = {"days": len(held_days), "current": held_old, "winner": held_new}
        record["held_out"] = held
        tried = len(record["tried"])
        if beats(held_new, held_old):
            self._settings = dict(winner["settings"])
            self._save_settings()
            record.update(decision="adopted", adopted=dict(winner["settings"]),
                          changed=changed_setting(record["current"], winner["settings"]))
            record["sentence"] = _adopted_sentence(tried, record["tuning"], held)
            record["reason"] = record["sentence"]
        else:
            record["rejected"] = dict(winner["settings"])
            record["sentence"] = _rejected_sentence(tried, record["tuning"], held)
            record["reason"] = ("The winner on the tuning days did not also win on the held-out days: "
                                + _held_out_failure(held_new, held_old) + ".")

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


# the reports

def _report_farm(k: int) -> tuple[str, dict]:
    """Day k's farm: the two shipped farms, then random farms with fixed seeds."""
    if k == 0:
        name, path = "sample_farm", ROOT / "scenarios" / "farms" / "sample_farm.json"
    elif k == 1:
        name, path = "example_farm", ROOT / "scenarios" / "example_farm.json"
    else:
        return f"random_{100 + k}", _make_farm(100 + k)
    return name, json.loads(path.read_text(encoding="utf-8"))


def _make_farm(seed: int) -> dict:
    tools = str(ROOT / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    from make_farm import make_farm
    return make_farm(seed=seed)


def _run_one(raw: dict, settings: dict) -> dict:
    with trial(settings):
        return sweep.summarise(sweep.run_days([("day", "day", copy.deepcopy(raw))]))


def run_report(days: int = 8, folder: Path | None = None, out=print) -> dict:
    """Run days off screen, self-tuning after each. Starts from the defaults."""
    with constants_kept():  # the report must not leave its settings in this process
        return _run_report(days, folder, out)


def _run_report(days: int, folder: Path | None, out) -> dict:
    tuner = SelfTune(folder)
    tuner.reset()
    started = tuner.settings()
    adoptions, rows = [], []
    for k in range(days):
        farm, raw = _report_farm(k)
        used = tuner.settings()
        result = _run_one(raw, used)
        tuner.record_day(farm, raw, [])
        began = time.monotonic()
        record = tuner.tune()
        seconds = round(time.monotonic() - began, 1)
        rows.append({"day": k + 1, "farm": farm, "settings": used, "on_time": result["on_time"],
                     "wasted": round(result["wasted"], 1), "sentence": record["sentence"], "seconds": seconds})
        out(f"Day {k + 1} ({farm}): idle weight {used['idle_weight']:g}, {result['on_time']} jobs on time, "
            f"{result['wasted']:.0f} wasted minutes. {record['sentence']} [{seconds}s]")
        if record["decision"] == "adopted":
            adoptions.append(_adoption(k + 1, record))
    final = tuner.settings()
    out(f"\nStarted with {_show(started)}.")
    out(_adoption_lines(adoptions, "the current settings were the best of those tried on every replay"))
    out(f"Final settings: {_show(final)}.")
    return {"started": started, "final": final, "adoptions": adoptions, "days": rows}


def _show(settings: dict) -> str:
    return ", ".join(f"{name} {value:g}" for name, value in settings.items())


def _adoption(day: int, record: dict) -> dict:
    name = record["changed"]
    tuning, held = record["tuning"], record["held_out"]
    return {"day": day, "setting": name, "from": record["current"][name], "to": record["adopted"][name],
            "before": tuning["current"], "after": tuning["winner"],
            "tuning_days": tuning["days"], "held_days": held["days"],
            "held_before": held["current"], "held_after": held["winner"]}


def _scores(label: str, before: dict, after: dict) -> str:
    return (f"{label}: before {before['on_time']} on time, {before['wasted']:,.0f} wasted minutes; "
            f"after {after['on_time']} on time, {after['wasted']:,.0f} wasted minutes")


def _adoption_lines(adoptions: list[dict], nothing: str) -> str:
    if not adoptions:
        return f"Nothing was adopted: {nothing}."
    return "\n".join(
        f"After day {a['day']}: {a['setting']} moved from {a['from']:g} to {a['to']:g}. "
        f"{_scores('Tuning days', a['before'], a['after'])}. "
        f"{_scores('Held-out days', a['held_before'], a['held_after'])}."
        for a in adoptions)


# One quiet day, then eight days where one tractor breaks. Tractors take turns in farm order
# and the minutes are fixed, so the run is the same every time. A stability cost only
# matters when there is a re-plan, which a breakdown forces.
FARM_HISTORY_BREAKS = (45, 100, 70, 140, 180, 230, 20, 120)
# Four more days that are never in the history: the tractors start one place later in the
# farm order and the minutes are ones the history never used.
UNSEEN_BREAKS = (60, 90, 160, 210)
FARM_NAMES = ("large_farm", "random_101", "random_102", "random_103")


def _history_farm(name: str) -> dict:
    if name == "large_farm":
        raw = json.loads((ROOT / "scenarios" / "farms" / "large_farm.json").read_text(encoding="utf-8"))
    else:
        raw = _make_farm(int(name.split("_")[1]))
    raw["events"] = []
    return raw


def _breakdown_days(raw: dict, minutes: tuple[int, ...], first_tractor: int) -> list[dict]:
    tractors = [t["id"] for t in raw["tractors"]]
    days = []
    for k, minute in enumerate(minutes):
        day = copy.deepcopy(raw)
        day["events"] = [{"at": minute, "type": "breakdown", "tractor": tractors[(first_tractor + k) % len(tractors)]}]
        days.append(day)
    return days


def farm_history(raw: dict) -> list[dict]:
    """Nine days of the same farm: quiet, then one tractor breaking at a fixed minute each day."""
    return [copy.deepcopy(raw)] + _breakdown_days(raw, FARM_HISTORY_BREAKS, 0)


def unseen_days(raw: dict) -> list[dict]:
    return _breakdown_days(raw, UNSEEN_BREAKS, 1)


def _held_out_verdict(adoptions: list[dict], rejected: int) -> str:
    """One plain line: did a gain hold on the days the tune did not see?"""
    if adoptions:
        held = [f"{_gain_phrase(a['held_before'], a['held_after'])} (after day {a['day']})" for a in adoptions]
        return "Gain held on the held-out days: " + "; ".join(held) + "."
    if rejected:
        return (f"No gain held: {rejected} setting{'s' if rejected != 1 else ''} won on the tuning days "
                "and lost or tied on the held-out days, so nothing was adopted.")
    return "No gain to check: no setting beat the defaults even on the tuning days."


def _never_seen(raw: dict, final: dict, out) -> dict:
    """Final settings against the defaults on days that were never in the history."""
    days = [("unseen", "replay", day) for day in unseen_days(raw)]
    with constants_kept():
        mine, default = score_settings(final, days), score_settings(dict(DEFAULTS), days)
    out(f"  never-seen {len(days)} days, final settings: {mine['on_time']} on time, {mine['wasted']:,.0f} wasted minutes")
    out(f"  never-seen {len(days)} days, default settings: {default['on_time']} on time, "
        f"{default['wasted']:,.0f} wasted minutes")
    return {"final": mine, "default": default}


def run_farm_report(name: str, out=print) -> dict:
    """Nine days on one farm, each planned with the settings current then, self-tuning after each."""
    began = time.monotonic()
    tuner = SelfTune(None)  # in memory only: the saved settings in runs/ are never touched
    tuner.reset()
    started = tuner.settings()
    raw_farm = _history_farm(name)
    adoptions, rejected = [], 0
    out(f"{name}\n  started with {_show(started)}")
    for k, raw in enumerate(farm_history(raw_farm)):
        _run_one(raw, tuner.settings())  # the day as it ran, with the settings current at that moment
        tuner.record_day(f"{name} day {k + 1}", raw, [])
        record = tuner.tune()
        if record["held_out"] is None:
            continue
        tuning, held = record["tuning"], record["held_out"]
        verdict = "ADOPTED" if record["decision"] == "adopted" else "REJECTED"
        changed = changed_setting(tuning["current"]["settings"], tuning["winner"]["settings"])
        out(f"  after day {k + 1} (tuned on {tuning['days']} days, held out {held['days']}): {verdict} "
            f"{changed} {tuning['current']['settings'][changed]:g} to {tuning['winner']['settings'][changed]:g}")
        out("    " + _scores("tuning days", tuning["current"], tuning["winner"]))
        out("    " + _scores("held-out days", held["current"], held["winner"]))
        if record["decision"] == "adopted":
            adoptions.append(_adoption(k + 1, record))
        else:
            rejected += 1
    verdict_line = _held_out_verdict(adoptions, rejected)
    out(f"  {verdict_line}")
    out(f"  final settings: {_show(tuner.settings())}")
    unseen = _never_seen(raw_farm, tuner.settings(), out)
    seconds = round(time.monotonic() - began, 1)
    out(f"  seconds taken: {seconds}\n")
    return {"farm": name, "started": started, "final": tuner.settings(), "adoptions": adoptions,
            "rejected": rejected, "verdict": verdict_line, "never_seen": unseen, "seconds": seconds}


def run_farms(out=print) -> list[dict]:
    with constants_kept():
        return [run_farm_report(name, out) for name in FARM_NAMES]


def main(argv: list[str]) -> None:
    command = argv[0] if argv else "show"
    folder = ROOT / "runs" / "selftune"
    if command == "report":
        count = int(argv[argv.index("--days") + 1]) if "--days" in argv else 8
        run_report(count, folder if "--save" in argv else None)
    elif command == "farms":
        run_farms()
    elif command == "reset":
        print("Settings:", SelfTune(folder).reset())
    elif command == "show":
        tuner = SelfTune(folder)
        latest = tuner.latest()
        print("Settings:", tuner.settings())
        print(latest["sentence"] if latest else "No self-tune has run yet.")
    else:
        print("Usage: python -m foreman.selftune report [--days N] [--save] | farms | reset | show")


if __name__ == "__main__":
    main(sys.argv[1:])
