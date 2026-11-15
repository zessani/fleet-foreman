"""The live engine behind the server: one Day on a real-time clock.

It owns the start, pause, reset, and speed controls, builds the dashboard
state of CLAUDE.md section 8c, and produces the baseline comparison report.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from foreman.ai_base import ModelClient
from foreman.ai_crew import AiCrew
from foreman.day import Day
from foreman.model import clock
from foreman.nav import RoadMap
from foreman.why import why

ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = ROOT / "scenarios"
EVENT_LOG_PATH = ROOT / "runs" / "event_log.jsonl"
SPEEDS = (1, 4, 16, 60)  # simulated minutes per real second
DEFAULT_SPEED = 16
LOG_LINES_IN_STATE = 200


def find_scenario(name: str) -> Path | None:
    """A farm file by name, from scenarios/ or scenarios/farms/."""
    for folder in (SCENARIO_DIR / "farms", SCENARIO_DIR):
        path = folder / f"{name}.json"
        if path.exists():
            return path
    return None


def default_scenario() -> Path:
    """The sample farm if it is there, the example farm otherwise."""
    return find_scenario("sample_farm") or SCENARIO_DIR / "example_farm.json"


class Engine:
    def __init__(self, scenario: Path | None = None, log_path: Path | None = None,
                 client: ModelClient | None = None, learning=None,
                 lessons_path: Path | None = None,
                 agopenweb_urls: dict[str, str] | None = None,
                 agopenweb_origin: tuple[float, float] = (33.4484, -112.0740)) -> None:
        # The clock thread and the request handlers both touch the engine.
        self._lock = threading.RLock()
        self._log_path = log_path
        self._client = client          # the model behind the AI agents; None means they are off
        self.learning = learning       # learned work rates, kept across days
        self._lessons_path = lessons_path
        self._agopenweb_urls = agopenweb_urls
        self._agopenweb_origin = agopenweb_origin
        self.day: Day | None = None
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
            if self.day is not None:
                self.day.foreman.close()
            if self.learning is not None:
                self.learning.start_day()  # closes the day before, if it recorded anything
                self.learning.save()
            self.day = Day(self.raw, log_path=self._log_path, learning=self.learning,
                           ai=AiCrew(self._client), lessons_path=self._lessons_path,
                           agopenweb_urls=self._agopenweb_urls,
                           agopenweb_origin=self._agopenweb_origin)
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
            self.day.drain_ai()
            if self.running:
                self.advance(real_seconds * self.clock_speed)

    def advance(self, minutes: float) -> None:
        with self._lock:
            self.day.advance(minutes)
            if self.day.finished:
                self.running = False
                if self.learning is not None:
                    self.learning.save()

    @property
    def now(self) -> float:
        return self.day.now

    @property
    def finished(self) -> bool:
        return self.day.finished

    @property
    def clock_speed(self) -> int:
        """The clock drops to 1x while an AI agent is thinking, so its answer still matters."""
        return 1 if self.day.ai_thinking() else self.speed

    def why(self, job_id: str) -> dict:
        """Why this job went to its tractor, and why not to each of the others."""
        with self._lock:
            run = self.day.foreman
            return why(self.day.farm, self.day.road_map, run.world(), run.plan, job_id,
                       speed_factor=run.speed_factor)

    def learning_report(self) -> dict:
        """What Foreman has learned: estimate error by day, learned work rates, AI lessons."""
        with self._lock:
            memory = self.day.memory
            learned = self.learning.summary() if self.learning is not None else {"days": [], "factors": []}
            return {
                **learned,
                "lessons": memory.lessons[-30:],
                "downtime_estimates": {
                    "count": len(memory.estimates),
                    "avg_gap_min": memory.downtime_gap(),
                    "items": memory.estimates[-30:],
                },
            }

    def run_learning_days(self, days: int) -> list[dict]:
        """Run several days off screen, hidden speed factors on, so the estimates improve."""
        from foreman.learning import Learning, run_days

        with self._lock:
            if self.learning is None:
                self.learning = Learning()
            return run_days(self.raw, days, self.learning)

    def state(self) -> dict:
        with self._lock:
            day, farm = self.day, self.day.farm
            return {
                "now": round(day.now, 2),
                "clock": clock(farm.day_start_min, day.now),
                "running": self.running,
                "speed": self.speed,
                "clock_speed": self.clock_speed,
                "finished": day.finished,
                "scenario": self.scenario,
                "execution": "agopenweb" if day.foreman.live else "simulation",
                "farm": {
                    "name": farm.name,
                    "nodes": farm.raw["nodes"],
                    "roads": farm.raw.get("roads", []),
                    "implements": farm.raw["implements"],
                    "day_start_min": farm.day_start_min,
                    "horizon_min": farm.horizon_min,
                    "blocked": sorted(sorted(road) for road in day.blocked),
                },
                "foreman": {
                    **day.foreman.block(),
                    "explanation": day.foreman.explanation,
                    "safety": day.foreman.safety,
                    "log": day.memory.log.lines(LOG_LINES_IN_STATE),
                    "ai": self._ai_block(),
                    "reasoning": day.desk.crew.reasoning() if day.desk else [],
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

    def _ai_block(self) -> dict:
        day = self.day
        return {
            "enabled": day.desk is not None,
            "thinking": day.ai_thinking(),
            "model": day.desk.crew.model if day.desk else None,
            "estimates": len(day.memory.estimates),
            "downtime_gap_min": day.memory.downtime_gap(),
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
