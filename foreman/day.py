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

from foreman.ai_crew import AiCrew
from foreman.ai_desk import AiDesk
from foreman.faults import fault_sample
from foreman.roles import Orchestrator
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
                 log_path: Path | None = None, learning=None, ai: AiCrew | None = None,
                 lessons_path: Path | None = None) -> None:
        # Events can change the job list, so each day works on its own copy of the farm.
        self.farm = farm_from_dict(copy.deepcopy(raw))
        self.road_map = RoadMap(self.farm.raw)
        self.blocked: set[frozenset[str]] = set()
        self.memory = Memory(self.farm.day_start_min, log_path, lessons_path)
        self.foreman = Run(self.farm, self.road_map, self.blocked, use_truth)
        self.baseline = Run(self.farm, self.road_map, self.blocked, use_truth)
        # The learning loop is Foreman's own memory; the baseline does not learn.
        self.learning = learning
        if learning is not None:
            self.foreman.speed_factor = learning.factor
        self.crew = Orchestrator(self.foreman, self.memory)
        # The AI agents are optional. With none, every decision is rule-based.
        self.desk = AiDesk(self, ai) if ai is not None and ai.enabled else None
        self.faults: dict[str, dict] = {}  # broken tractor -> its fault report and real repair time
        self._faults_sent = 0
        self.manual: list[dict] = []  # events applied by hand, each with the minute it happened
        self._pending = sorted(list(self.farm.events) + list(extra_events or []),
                               key=lambda event: event["at"])
        self._next_watch = WATCH_MIN
        self.finished = False

        self.memory.log.append(0.0, "Orchestrator", "jobs_submitted",
                               f"{len(self.farm.jobs)} jobs submitted by the operator.")
        self.handle([{"type": "jobs_submitted"}])
        self._replan_baseline()

    @property
    def now(self) -> float:
        return self.foreman.sim.now

    def handle(self, events: list[dict]):
        """Send events through the rule-based roles, then ask the AI agents about the result."""
        plan_before = self.foreman.plan_no
        routes_before = self.foreman.plan.job_order() if self.foreman.plan else {}
        for event in events:
            if event["type"] == "breakdown":
                # Remember its work, in case the planner later decides to wait for it.
                self.foreman.had[event["tractor"]] = [
                    job_id for job_id in routes_before.get(event["tractor"], [])
                    if self.foreman.sim.jobs[job_id].status != "done"]
        cycle = self.crew.handle(events)
        if self.desk is not None:
            self.desk.after_breakdowns([e for e in events if e["type"] == "breakdown"],
                                       routes_before)
            if self.foreman.plan_no != plan_before:
                self.desk.after_dispatch()
            if self.desk.crew.sync:
                self.desk.drain()
        return cycle

    def drain_ai(self) -> None:
        """Apply any AI agent answers that have arrived."""
        if self.desk is not None:
            self.desk.drain()

    def ai_thinking(self) -> bool:
        return self.desk is not None and self.desk.crew.thinking()

    def schedule(self, event: dict) -> None:
        """Queue an event for a later minute."""
        self._pending.append(event)
        self._pending.sort(key=lambda item: item["at"])

    def note_actual_downtime(self, tractor_id: str) -> None:
        """Once a tractor is back, save how long the repair really took next to the estimate,
        so the AI Recovery agent's next estimate for this kind of fault can be better."""
        fault = self.faults.get(tractor_id)
        if (fault is None or fault.get("noted") or fault.get("repaired_at") is None
                or fault.get("estimate_min") is None):
            return
        fault["noted"] = True
        actual = round(fault["repaired_at"] - fault["at"])
        self.memory.add_lesson({
            "tractor": self.farm.tractors[tractor_id].name, "report": fault["report"][:160],
            "estimated_min": fault["estimate_min"], "actual_downtime_min": actual,
            "lesson": f"This fault took {actual} min to repair. The estimate was "
                      f"{fault['estimate_min']} min.",
        })

    def job_label(self, job_id: str) -> str:
        job = self.farm.jobs[job_id]
        return f"{job.type.capitalize()} {self.farm.nodes[job.field].name}"

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
        if event["type"] == "breakdown" and "report" not in event:
            # A breakdown from the dashboard comes with the next sample fault report.
            event = {**event, **fault_sample(self._faults_sent)}
            self._faults_sent += 1
        self.manual.append({**event, "at": self.now})
        self._apply(event)
        reply = {"ok": True, "at": round(self.now, 2)}
        if event.get("report"):
            reply["report"] = event["report"]
        return reply

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
            # Only the tractor changes here. Nobody is told: the recovery monitor
            # has to notice it from the status feed.
            for run in (self.foreman, self.baseline):
                tractor = run.sim.tractors[event["tractor"]]
                tractor.stall() if event.get("silent") else tractor.break_down()
            if event.get("report"):
                self.faults[event["tractor"]] = {"report": event["report"], "at": self.now,
                                                 "true_downtime": event.get("repair_after_min")}
                if event.get("repair_after_min") is not None:
                    # The simulated repair: the tractor comes back by itself, in both worlds.
                    self.schedule({"at": self.now + event["repair_after_min"], "type": "repair",
                                   "tractor": event["tractor"]})
        elif kind == "_downtime_expired":
            self._downtime_expired(event["tractor"])
        elif kind == "_hold_release":
            self._release_hold(event["job"])
        elif kind == "repair":
            for run in (self.foreman, self.baseline):
                run.sim.tractors[event["tractor"]].repair()
            if event["tractor"] in self.faults:
                self.faults[event["tractor"]].setdefault("repaired_at", self.now)
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
        self.handle([event])
        self._replan_baseline()

    def _downtime_expired(self, tractor_id: str) -> None:
        """The tractor was expected back by now. If it is still down, stop waiting for it."""
        if self.foreman.returning.pop(tractor_id, None) is None:
            return
        if tractor_id in self.foreman.watch.down:
            name = self.farm.tractors[tractor_id].name
            self.handle([{"type": "downtime_expired", "tractor": tractor_id,
                          "headline": f"{name} is not back by the estimated time. "
                                      "Its held work is moved."}])

    def _release_hold(self, job_id: str) -> None:
        if self.foreman.ai_holds.pop(job_id, None) is not None:
            self.handle([{"type": "hold_released", "job": job_id,
                          "headline": f"The hold on {self.job_label(job_id)} has ended."}])

    # ---- the loop ---------------------------------------------------------

    def _read_status_feed(self) -> None:
        self.crew.recovery.note_progress(self.foreman.sim.events)
        self._learn_from(self.foreman.sim.events)
        drift = self._drift_events(self.foreman.sim.events)
        self.foreman.sim.events.clear()
        events = self.crew.recovery.watch() + drift
        for event in events:
            if event["type"] == "repaired":
                self.foreman.returning.pop(event["tractor"], None)  # it is back: no more waiting
                self.note_actual_downtime(event["tractor"])
        if events:
            # Everything seen in the same reading shares one re-plan.
            self.handle(events)
        self.drain_ai()

        self.baseline.sim.events.clear()
        baseline_events = self.baseline.watch.poll(self.now, self.baseline.statuses())
        for event in baseline_events:
            if event.get("cause") == "stall":
                self.baseline.sim.tractors[event["tractor"]].break_down()
        if baseline_events:
            self._replan_baseline()

    def _learn_from(self, sim_events: list[dict]) -> None:
        """Feed what each work session really achieved to the learning loop."""
        if self.learning is None:
            return
        for event in sim_events:
            if event["type"] == "work_sample":
                self.learning.record(self.farm, event)

    def _drift_events(self, sim_events: list[dict]) -> list[dict]:
        """Jobs that just finished well behind plan, while other work is still waiting."""
        if self.foreman.sim.all_done():
            return []
        drift = []
        for event in sim_events:
            planned = self.foreman.rows.get(event.get("job"))
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
        self._learn_from(self.foreman.sim.events)
        self.foreman.sim.events.clear()
        done = self.foreman.sim.totals()["jobs_done"]
        self.memory.log.append(self.now, "Orchestrator", "day_complete",
                               f"Day complete: {done} of {len(self.farm.jobs)} jobs finished.")
