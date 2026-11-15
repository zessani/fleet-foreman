"""The live engine behind the server: one Day on a real-time clock.

It owns the start, pause, reset, and speed controls, builds the dashboard
state of CLAUDE.md section 8c, and produces the baseline comparison report.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from foreman.day import Day
from foreman.model import clock

ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = ROOT / "scenarios"
EVENT_LOG_PATH = ROOT / "runs" / "event_log.jsonl"
SPEEDS = (1, 4, 16, 60)  # simulated minutes per real second
DEFAULT_SPEED = 16
LOG_LINES_IN_STATE = 200


def default_scenario() -> Path:
    """Vishwa's sample farm once it exists, the example farm until then."""
    sample = SCENARIO_DIR / "sample_farm.json"
    return sample if sample.exists() else SCENARIO_DIR / "example_farm.json"


class Engine:
    def __init__(self, scenario: Path | None = None, log_path: Path | None = None) -> None:
        # The clock thread and the request handlers both touch the engine.
        self._lock = threading.RLock()
        self._log_path = log_path
        self.running = False
        self.speed = DEFAULT_SPEED
        self.load(scenario or default_scenario())

    def load(self, scenario: Path) -> None:
        with self._lock:
            with open(scenario, encoding="utf-8") as farm_file:
                self.raw = json.load(farm_file)
            self.scenario = scenario.stem
            self.reset()

    def reset(self) -> None:
        with self._lock:
            self.running = False
            self.day = Day(self.raw, log_path=self._log_path)
            self._full_days: dict[tuple[bool, int], dict] = {}  # report cache

    def control(self, action: str | None, speed: float | None = None) -> dict:
        with self._lock:
            if speed is not None:
                if speed not in SPEEDS:
                    return {"ok": False, "error": f"speed must be one of {list(SPEEDS)}"}
                self.speed = int(speed)
            if action == "start":
                self.running = not self.day.finished
            elif action == "pause":
                self.running = False
            elif action == "reset":
                self.reset()
            elif action is not None:
                return {"ok": False, "error": "action must be start, pause, or reset"}
            return {"ok": True, "running": self.running, "speed": self.speed}

    def event(self, event: dict) -> dict:
        """Apply an event to both worlds at the current simulated minute."""
        with self._lock:
            return self.day.apply(event)

    def tick(self, real_seconds: float) -> None:
        """Called by the server clock. Moves the day forward if running."""
        with self._lock:
            if self.running:
                self.advance(real_seconds * self.speed)

    def advance(self, minutes: float) -> None:
        with self._lock:
            self.day.advance(minutes)
            if self.day.finished:
                self.running = False

    @property
    def now(self) -> float:
        return self.day.now

    @property
    def finished(self) -> bool:
        return self.day.finished

    def state(self) -> dict:
        with self._lock:
            day, farm = self.day, self.day.farm
            return {
                "now": round(day.now, 2),
                "clock": clock(farm.day_start_min, day.now),
                "running": self.running,
                "speed": self.speed,
                "finished": day.finished,
                "scenario": self.scenario,
                "farm": {
                    "name": farm.name,
                    "nodes": farm.raw["nodes"],
                    "roads": farm.raw.get("roads", []),
                    "blocked": sorted(sorted(road) for road in day.blocked),
                },
                "foreman": {
                    **day.foreman.block(),
                    "explanation": day.foreman.explanation,
                    "safety": day.foreman.safety,
                    "log": day.memory.log.lines(LOG_LINES_IN_STATE),
                },
                "baseline": day.baseline.block(),
            }

    def report(self) -> dict:
        """Foreman against the baseline: same farm, same clock, same events."""
        with self._lock:
            day = self.day
            replans = day.foreman.replans
            return {
                "scenario": self.scenario,
                "farm": day.farm.name,
                "jobs": len(day.farm.jobs),
                # The main result: a full day with hidden speed factors off, so the
                # comparison does not depend on which tractor the factors happen to hit.
                "main": self._full_day(False),
                "with_hidden_factors": self._full_day(True),
                # The run on screen right now, which may still be in progress.
                "live": {
                    "clock": clock(day.farm.day_start_min, day.now),
                    "finished": day.finished,
                    **_compare(day.foreman.sim.totals(), day.baseline.sim.totals()),
                },
                "plan_ms": replans[-1]["ms"] if replans else None,
                "replans": replans,
            }

    def log_lines(self) -> list[dict]:
        with self._lock:
            return self.day.memory.log.lines()

    def _full_day(self, use_truth: bool) -> dict:
        """Run the whole day off screen, with the same events, and compare the totals."""
        key = (use_truth, len(self.day.manual))
        if key not in self._full_days:
            day = Day(self.raw, use_truth, extra_events=self.day.manual)
            day.advance(day.farm.horizon_min)
            self._full_days[key] = {
                "hidden_factors": use_truth,
                **_compare(day.foreman.sim.totals(), day.baseline.sim.totals()),
            }
        return self._full_days[key]


def _compare(foreman: dict, baseline: dict) -> dict:
    return {
        "foreman": foreman,
        "baseline": baseline,
        "difference": {key: round(foreman[key] - baseline[key], 1) for key in foreman},
    }
