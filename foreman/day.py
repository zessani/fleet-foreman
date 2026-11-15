"""One working day: the Foreman world and the baseline world on one clock.

Every event is applied to both worlds at the same simulated minute, and both
find out about a failure the same way (the status feed), so the comparison
between them is fair.
"""

from __future__ import annotations

import copy
import math
from dataclasses import replace
from pathlib import Path

from foreman.agents import Orchestrator
from foreman.baseline import plan_baseline
from foreman.memory import Memory
from foreman.model import farm_from_dict, job_from_dict
from foreman.nav import RoadMap
from foreman.run import Run
from foreman.state import JobState

WATCH_MIN = 1.0   # how often the status feed is read, in simulated minutes
DRIFT_MIN = 15.0  # a job finishing this far behind plan triggers a re-plan
TIME_EPS = 1e-6
EVENT_TYPES = ("breakdown", "repair", "block_road", "open_road", "set_priority", "add_job")


class Day:
    def __init__(self, raw: dict, use_truth: bool = True, extra_events: list[dict] | None = None,
                 log_path: Path | None = None) -> None:
        # Events can change the job list, so each day works on its own copy of the farm.
        self.farm = farm_from_dict(copy.deepcopy(raw))
        self.road_map = RoadMap(self.farm.raw)
        self.blocked: set[frozenset[str]] = set()
        self.memory = Memory(self.farm.day_start_min, log_path)
        self.foreman = Run(self.farm, self.road_map, self.blocked, use_truth)
        self.baseline = Run(self.farm, self.road_map, self.blocked, use_truth)
        self.crew = Orchestrator(self.foreman, self.memory)
        self.manual: list[dict] = []  # events applied by hand, each with the minute it happened
        self._pending = sorted(list(self.farm.events) + list(extra_events or []),
                               key=lambda event: event["at"])
        self._next_watch = WATCH_MIN
        self.finished = False

        self.memory.log.append(0.0, "Orchestrator", "jobs_submitted",
                               f"{len(self.farm.jobs)} jobs submitted by the operator.")
        self.crew.handle([{"type": "jobs_submitted"}])
        self._replan_baseline()

    @property
    def now(self) -> float:
        return self.foreman.sim.now

    def advance(self, minutes: float) -> None:
        """Move both worlds forward, stopping at every scripted event and status reading."""
        target = min(self.now + minutes, self.farm.horizon_min)
        while not self.finished and self.now < target - TIME_EPS:
            next_event = self._pending[0]["at"] if self._pending else math.inf
            step = min(target, self._next_watch, next_event) - self.now
            if step > TIME_EPS:
                self.foreman.sim.advance(step)
                self.baseline.sim.advance(step)
            while self._pending and self._pending[0]["at"] <= self.now + TIME_EPS:
                self._apply(self._pending.pop(0))
            if self.now >= self._next_watch - TIME_EPS:
                self._next_watch += WATCH_MIN
                self._read_status_feed()
            self._check_finished()

    def apply(self, event: dict) -> dict:
        """Apply an event from the operator or the dashboard, now, to both worlds."""
        error = self._problem_with(event)
        if error:
            return {"ok": False, "error": error}
        self.manual.append({**event, "at": self.now})
        self._apply(event)
        return {"ok": True, "at": round(self.now, 2)}

    # ---- events -----------------------------------------------------------

    def _problem_with(self, event: dict) -> str | None:
        kind = event.get("type")
        if kind not in EVENT_TYPES:
            return f"type must be one of {', '.join(EVENT_TYPES)}"
        if kind in ("breakdown", "repair") and event.get("tractor") not in self.farm.tractors:
            return "unknown tractor"
        if kind in ("block_road", "open_road"):
            a, b = event.get("a"), event.get("b")
            if not (a in self.farm.nodes and b in self.farm.nodes) or a == b:
                return "a and b must be two node ids"
            roads = self.farm.raw.get("roads")
            if roads is not None and frozenset((a, b)) not in {frozenset(road) for road in roads}:
                return f"there is no road between {a} and {b}"
        if kind == "set_priority" and (event.get("job") not in self.farm.jobs
                                       or event.get("priority") not in (1, 2, 3, 4, 5)):
            return "needs a known job and a priority from 1 to 5"
        if kind == "add_job":
            job = event.get("job") or {}
            if not {"id", "type", "field"} <= job.keys() or job["id"] in self.farm.jobs:
                return "job needs a new id, a type, and a field"
            if job["field"] not in self.farm.nodes:
                return "unknown field"
        return None

    def _apply(self, event: dict) -> None:
        kind = event["type"]
        if kind == "breakdown":
            # Only the tractor changes here. Nobody is told: the Recovery agent
            # has to notice it from the status feed.
            for run in (self.foreman, self.baseline):
                tractor = run.sim.tractors[event["tractor"]]
                tractor.stall() if event.get("silent") else tractor.break_down()
        elif kind == "repair":
            for run in (self.foreman, self.baseline):
                run.sim.tractors[event["tractor"]].repair()
        elif kind in ("block_road", "open_road"):
            name = "road_blocked" if kind == "block_road" else "road_opened"
            self._operator_event({"type": name, "a": event["a"], "b": event["b"]})
        elif kind == "set_priority":
            job = self.farm.jobs[event["job"]]
            self.farm.jobs[job.id] = replace(job, priority=int(event["priority"]))
            self._operator_event({"type": "priority_changed", "job": job.id,
                                  "priority": int(event["priority"])})
        elif kind == "add_job":
            job = job_from_dict(event["job"], len(self.farm.jobs), self.farm.nodes,
                                self.farm.horizon_min)
            self.farm.jobs[job.id] = job
            for run in (self.foreman, self.baseline):
                run.sim.jobs[job.id] = JobState(job.id, job.area_ha)
            self.finished = False
            self._operator_event({"type": "job_added", "job": job.id})

    def _operator_event(self, event: dict) -> None:
        """An event both dispatchers are told about directly."""
        self.crew.handle([event])
        self._replan_baseline()

    # ---- the loop ---------------------------------------------------------

    def _read_status_feed(self) -> None:
        self.crew.recovery.note_progress(self.foreman.sim.events)
        drift = self._drift_events(self.foreman.sim.events)
        self.foreman.sim.events.clear()
        events = self.crew.recovery.watch() + drift
        if events:
            # Everything seen in the same reading shares one re-plan.
            self.crew.handle(events)

        self.baseline.sim.events.clear()
        baseline_events = self.baseline.watch.poll(self.now, self.baseline.statuses())
        for event in baseline_events:
            if event.get("cause") == "stall":
                self.baseline.sim.tractors[event["tractor"]].break_down()
        if baseline_events:
            self._replan_baseline()

    def _drift_events(self, sim_events: list[dict]) -> list[dict]:
        """Jobs that just finished well behind plan, while other work is still waiting."""
        if self.foreman.sim.all_done():
            return []
        drift = []
        for event in sim_events:
            planned = self.foreman.rows.get(event["job"])
            if event["type"] == "job_done" and planned is not None:
                behind = event["t"] - planned["finish"]
                if behind > DRIFT_MIN:
                    drift.append({"type": "drift", "job": event["job"],
                                  "behind_min": round(behind, 1)})
        return drift

    def _replan_baseline(self) -> None:
        baseline = self.baseline
        baseline.send(plan_baseline(self.farm, self.road_map, baseline.world(), baseline.plan))

    def _check_finished(self) -> None:
        all_done = self.foreman.sim.all_done() and self.baseline.sim.all_done()
        if self.finished or not (all_done or self.now >= self.farm.horizon_min - TIME_EPS):
            return
        self.finished = True
        self.crew.recovery.note_progress(self.foreman.sim.events)
        self.foreman.sim.events.clear()
        done = self.foreman.sim.totals()["jobs_done"]
        self.memory.log.append(self.now, "Orchestrator", "day_complete",
                               f"Day complete: {done} of {len(self.farm.jobs)} jobs finished.")
