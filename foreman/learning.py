"""The learning loop: Foreman learns real work speeds from what the tractors achieve.

This module only tunes time and fuel estimates. The safety rules are fixed and
are never changed, loosened or learned here.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from foreman.day import Day
from foreman.model import Farm

MIN_FACTOR, MAX_FACTOR = 0.4, 1.6
MIN_SAMPLE_MIN = 0.5  # shorter sessions say nothing about speed
SEP = "|"


def _clamp(value: float) -> float:
    return max(MIN_FACTOR, min(MAX_FACTOR, value))


class Learning:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        # key "tractor|implement|field" -> [hectares, hectares the catalog rate would have covered
        # in the minutes worked, samples]; factor = first / second
        self.sums: dict[str, list[float]] = {}
        self.history: list[dict] = []
        self.days_run = 0
        # the day in progress: [gap_pct * minutes summed, minutes summed, samples]
        self.day_gap = [0.0, 0.0, 0]
        self._load()

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            sums = {str(k): [float(x) for x in v[:3]] for k, v in data["sums"].items()}
            history = [dict(d) for d in data["history"]]
            days_run = int(data["days_run"])
            day_gap = [float(x) for x in data["day_gap"][:3]]
            if len(day_gap) != 3 or any(len(v) != 3 for v in sums.values()):
                raise ValueError("bad shape")
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return  # a corrupt file starts empty
        self.sums, self.history, self.days_run = sums, history, days_run
        self.day_gap = [day_gap[0], day_gap[1], int(day_gap[2])]

    def _average(self, match) -> float | None:
        """Pooled factor over every key the predicate accepts."""
        hectares = hours = 0.0
        for key, (ha, expected, _) in self.sums.items():
            if match(*key.split(SEP)):
                hectares += ha
                hours += expected
        return hectares / hours if hours > 0 else None

    def factor(self, tractor_id: str, implement_id: str, field_id: str) -> float:
        # Exact key first, then the same implement on the same field, then the same pairing elsewhere.
        for match in (
            lambda t, i, f: (t, i, f) == (tractor_id, implement_id, field_id),
            lambda t, i, f: (i, f) == (implement_id, field_id),
            lambda t, i, f: (t, i) == (tractor_id, implement_id),
        ):
            value = self._average(match)
            if value is not None:
                return _clamp(value)
        return 1.0

    def record(self, farm: Farm, sample: dict) -> None:
        hectares, minutes = float(sample["hectares"]), float(sample["minutes"])
        if minutes < MIN_SAMPLE_MIN or hectares <= 0:
            return
        tractor, implement, field = sample["tractor"], sample["implement"], sample["field"]
        rate = farm.implements[implement].ha_per_hour
        # Score the estimate the planner had before this sample taught it anything.
        predicted = hectares / (rate * self.factor(tractor, implement, field)) * 60
        gap = 100 * abs(predicted - minutes) / minutes
        self.day_gap[0] += gap * minutes
        self.day_gap[1] += minutes
        self.day_gap[2] += 1
        entry = self.sums.setdefault(SEP.join((tractor, implement, field)), [0.0, 0.0, 0.0])
        entry[0] += hectares
        entry[1] += minutes / 60 * rate
        entry[2] += 1

    def _day_entry(self, number: int) -> dict:
        weighted, minutes, samples = self.day_gap
        return {"day": number, "gap_pct": weighted / minutes if minutes else 0.0, "samples": int(samples)}

    def start_day(self) -> None:
        if self.day_gap[2]:
            self.history.append(self._day_entry(len(self.history) + 1))
        self.day_gap = [0.0, 0.0, 0]
        self.days_run += 1

    def save(self) -> None:
        if self.path is None:
            return
        data = {"sums": self.sums, "history": self.history,
                "days_run": self.days_run, "day_gap": self.day_gap}
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(temp, self.path)  # atomic, so a crash never leaves half a file

    def summary(self) -> dict:
        days = [dict(d) for d in self.history]
        if self.day_gap[2]:
            days.append(self._day_entry(len(self.history) + 1))
        for day in days:
            day["gap_pct"] = round(day["gap_pct"], 1)
        factors = []
        for key, (hectares, expected, samples) in sorted(self.sums.items()):
            tractor, implement, field = key.split(SEP)
            factors.append({
                "tractor": tractor, "implement": implement, "field": field,
                "factor": round(_clamp(hectares / expected), 3),
                "samples": int(samples), "hectares": round(hectares, 3),
            })
        return {"days": days, "factors": factors, "days_run": self.days_run}


def run_days(raw: dict, days: int, learning: Learning, use_truth: bool = True) -> list[dict]:
    quiet = copy.deepcopy(raw)
    quiet["events"] = []  # learning days are quiet so results are about estimates, not breakdowns
    results = []
    for _ in range(days):
        learning.start_day()
        day = Day(quiet, use_truth=use_truth, learning=learning)
        day.advance(day.farm.horizon_min)
        totals = day.foreman.sim.totals()
        entry = learning._day_entry(len(results) + 1)
        results.append({
            **entry, "violations": totals["violations"], "jobs_done": totals["jobs_done"],
            "on_time": totals["on_time"], "finish_min": day.now,
        })
    learning.start_day()
    learning.save()
    return results
