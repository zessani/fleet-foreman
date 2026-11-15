"""Runs the Foreman world and the baseline world on one clock.

Both worlds get the same farm, the same clock, and the same events, so the
comparison between them is fair. The engine also builds the dashboard state
of CLAUDE.md section 8c.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from foreman.baseline import plan_baseline
from foreman.model import Farm, clock, load_farm
from foreman.nav import RoadMap
from foreman.planner import plan
from foreman.sim import Simulation
from foreman.state import Plan, Stop

SCENARIO_DIR = Path(__file__).resolve().parent.parent / "scenarios"
SPEEDS = (1, 4, 16, 60)  # simulated minutes per real second
DEFAULT_SPEED = 16
TRACTOR_COLORS = ("#E8A33D", "#4C9BE8", "#5DBB63", "#C76BD6", "#E86A5C", "#49C5B6")
LOG_LINES_IN_STATE = 200

Planner = Callable[..., Plan]


def default_scenario() -> Path:
    """Vishwa's sample farm once it exists, the example farm until then."""
    sample = SCENARIO_DIR / "sample_farm.json"
    return sample if sample.exists() else SCENARIO_DIR / "example_farm.json"


class Run:
    """One world: a simulation, the dispatcher planning for it, and its current plan."""

    def __init__(self, farm: Farm, road_map: RoadMap, planner: Planner) -> None:
        self.farm = farm
        self.road_map = road_map
        self.planner = planner
        self.sim = Simulation(farm, road_map)
        self.plan: Plan | None = None
        self.rows: dict[str, dict] = {}  # job id -> its latest plan row, kept after it is done

    def replan(self) -> Plan:
        """Plan from the current state and send each tractor its new to-do list."""
        self.plan = self.planner(self.farm, self.road_map, self.sim.world_state(), self.plan)
        for tractor_id, stops in self.plan.routes.items():
            self.sim.tractors[tractor_id].dispatch([stop.to_dispatch() for stop in stops])
            for stop in stops:
                self.rows[stop.job] = _plan_row(stop)
        return self.plan

    def block(self) -> dict:
        """The tractors, jobs, plan, and totals of section 8c for this world."""
        return {
            "tractors": [self._tractor_row(i, t) for i, t in enumerate(sorted(self.sim.tractors))],
            "jobs": [self._job_row(job_id) for job_id in self.farm.jobs],
            "plan": sorted(self.rows.values(), key=lambda row: (row["start"], row["tractor"])),
            "totals": self.sim.totals(),
        }

    def _tractor_row(self, index: int, tractor_id: str) -> dict:
        tractor = self.farm.tractors[tractor_id]
        status = self.sim.tractors[tractor_id].status()
        return {
            "id": tractor_id, "name": tractor.name,
            "x": round(status["x"], 4), "y": round(status["y"], 4),
            "status": status["state"], "job": status["job"],
            "fuel_pct": round(100.0 * status["fuel"] / tractor.fuel_capacity),
            "mounted": status["mounted"],
            "color": TRACTOR_COLORS[index % len(TRACTOR_COLORS)],
            "heading": round(status["heading"], 1), "node": status["node"],
        }

    def _job_row(self, job_id: str) -> dict:
        job, state = self.farm.jobs[job_id], self.sim.jobs[job_id]
        held_reason = self.plan.held.get(job_id) if self.plan else None
        planned = self.rows.get(job_id)
        row = {
            "id": job_id, "type": job.type, "field": job.field, "priority": job.priority,
            "deadline": job.deadline,
            "pct": round(100.0 * (1.0 - state.remaining_ha / job.area_ha)) if job.area_ha else 100,
            "status": "held" if held_reason and state.status != "done" else state.status,
            "tractor": state.tractor or (planned["tractor"] if planned else None),
        }
        if row["status"] == "held":
            row["reason"] = held_reason
        return row


def _plan_row(stop: Stop) -> dict:
    return {
        "job": stop.job, "tractor": stop.tractor, "implement": stop.implement,
        "start": round(stop.start, 1), "finish": round(stop.finish, 1),
        "late_min": round(stop.late_min, 1), "path": stop.path,
    }


class Engine:
    def __init__(self, scenario: Path | None = None) -> None:
        # The clock thread and the request handlers both touch the engine.
        self._lock = threading.RLock()
        self.running = False
        self.speed = DEFAULT_SPEED
        self.load(scenario or default_scenario())

    def load(self, scenario: Path) -> None:
        with self._lock:
            self.farm = load_farm(scenario)
            self.scenario = scenario.stem
            self.reset()

    def reset(self) -> None:
        with self._lock:
            self.running = False
            self.finished = False
            self.road_map = RoadMap(self.farm.raw)
            self.foreman = Run(self.farm, self.road_map, plan)
            self.baseline = Run(self.farm, self.road_map, plan_baseline)
            self.log: list[dict] = []
            self.explanation: dict = {}
            self._submit_jobs()

    def control(self, action: str | None, speed: float | None = None) -> dict:
        with self._lock:
            if speed is not None:
                if speed not in SPEEDS:
                    return {"ok": False, "error": f"speed must be one of {list(SPEEDS)}"}
                self.speed = int(speed)
            if action == "start":
                self.running = not self.finished
            elif action == "pause":
                self.running = False
            elif action == "reset":
                self.reset()
            elif action is not None:
                return {"ok": False, "error": "action must be start, pause, or reset"}
            return {"ok": True, "running": self.running, "speed": self.speed}

    def tick(self, real_seconds: float) -> None:
        """Called by the server clock. Moves both worlds forward if running."""
        with self._lock:
            if self.running:
                self.advance(real_seconds * self.speed)

    def advance(self, minutes: float) -> None:
        with self._lock:
            if self.finished:
                return
            minutes = min(minutes, self.farm.horizon_min - self.now)
            self.foreman.sim.advance(minutes)
            self.baseline.sim.advance(minutes)
            self.baseline.sim.events.clear()  # only the Foreman world is logged
            self._log_sim_events()
            all_done = self.foreman.sim.all_done() and self.baseline.sim.all_done()
            if all_done or self.now >= self.farm.horizon_min - 1e-6:
                self.finished = True
                self.running = False
                done = self.foreman.sim.totals()["jobs_done"]
                self._log("Orchestrator", "day_complete",
                          f"Day complete: {done} of {len(self.farm.jobs)} jobs finished.")

    @property
    def now(self) -> float:
        return self.foreman.sim.now

    def state(self) -> dict:
        with self._lock:
            farm = self.farm
            return {
                "now": round(self.now, 2),
                "clock": clock(farm.day_start_min, self.now),
                "running": self.running,
                "speed": self.speed,
                "finished": self.finished,
                "scenario": self.scenario,
                "farm": {
                    "name": farm.name,
                    "nodes": farm.raw["nodes"],
                    "roads": farm.raw.get("roads", []),
                    "blocked": [],
                },
                "foreman": {
                    **self.foreman.block(),
                    "explanation": self.explanation,
                    # The Safety gate is Phase 2. Until then nothing is claimed.
                    "safety": {"passed": None, "checks": []},
                    "log": self.log[-LOG_LINES_IN_STATE:],
                },
                "baseline": self.baseline.block(),
            }

    def report(self) -> dict:
        """Foreman against the baseline: same farm, same clock, same events."""
        with self._lock:
            foreman, baseline = self.foreman.sim.totals(), self.baseline.sim.totals()
            return {
                "scenario": self.scenario,
                "farm": self.farm.name,
                "clock": clock(self.farm.day_start_min, self.now),
                "finished": self.finished,
                "jobs": len(self.farm.jobs),
                "foreman": foreman,
                "baseline": baseline,
                "difference": {key: round(foreman[key] - baseline[key], 1) for key in foreman},
                "plan_ms": round(self.foreman.plan.ms, 1) if self.foreman.plan else None,
            }

    def log_lines(self) -> list[dict]:
        with self._lock:
            return list(self.log)

    def _submit_jobs(self) -> None:
        """The jobs_submitted event: plan both worlds from the start of the day."""
        new_plan = self.foreman.replan()
        self.baseline.replan()
        planned = sum(len(route) for route in new_plan.routes.values())
        self._log("Orchestrator", "jobs_submitted",
                  f"{len(self.farm.jobs)} jobs submitted. Routing: Fleet, Navigation, "
                  "Safety, dispatch.")
        self._log("Fleet", "plan",
                  f"Planned {planned} jobs across {len(new_plan.routes)} tractors in "
                  f"{new_plan.ms:.1f} ms.")
        for job_id, reason in new_plan.held.items():
            self._log("Fleet", "job_held", f"{self._job_label(job_id)} is held: {reason}.")
        self._log("Orchestrator", "dispatch", "Plan sent to the tractors.")
        self.explanation = self._explain_first_plan(new_plan, planned)

    def _explain_first_plan(self, new_plan: Plan, planned: int) -> dict:
        late = [stop for stop in new_plan.stops() if stop.late_min > 0]
        deadlines = ("All deadlines are met." if not late
                     else f"{len(late)} job(s) will miss their deadline.")
        held = f" {len(new_plan.held)} held." if new_plan.held else ""
        return {
            "headline": f"Day plan ready at {clock(self.farm.day_start_min, new_plan.created_at)}.",
            "summary": f"{planned} jobs planned across {len(new_plan.routes)} tractors.{held} "
                       f"{deadlines}",
            "changes": [self._describe_stop(stop) for stop in new_plan.stops()],
            "ms": round(new_plan.ms, 1),
        }

    def _describe_stop(self, stop: Stop) -> str:
        farm = self.farm
        at = lambda minute: clock(farm.day_start_min, minute)  # noqa: E731
        job = farm.jobs[stop.job]
        margin = job.deadline - stop.finish
        timing = (f"{margin:.0f} min before its {at(job.deadline)} deadline" if margin >= 0
                  else f"{-margin:.0f} min past its {at(job.deadline)} deadline")
        return (f"{self._job_label(stop.job)}: {farm.tractors[stop.tractor].name} with the "
                f"{farm.implements[stop.implement].name}, {at(stop.start)} to {at(stop.finish)}, "
                f"{timing}.")

    def _job_label(self, job_id: str) -> str:
        job = self.farm.jobs[job_id]
        return f"{job.type.capitalize()} {self.farm.nodes[job.field].name}"

    def _log_sim_events(self) -> None:
        for event in self.foreman.sim.events:
            name = self.farm.tractors[event["tractor"]].name
            label = self._job_label(event["job"])
            if event["type"] == "job_started":
                self._log("Recovery", "job_started", f"{name} started: {label}.", event["t"])
            elif event["type"] == "job_done":
                self._log("Recovery", "job_done", f"{name} finished: {label}.", event["t"])
        self.foreman.sim.events.clear()

    def _log(self, agent: str, event: str, message: str, t: float | None = None) -> None:
        t = self.now if t is None else t
        self.log.append({
            "t": round(t, 2), "clock": clock(self.farm.day_start_min, t),
            "agent": agent, "event": event, "message": message,
        })
