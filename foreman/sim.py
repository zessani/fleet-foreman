"""Tractor simulator (CLAUDE.md sections 8b, 10, 12, 13).

Time moves in fixed SUB_STEP_MIN steps so results never depend on wall-clock
timing. Inside a step a tractor chains phases (travel, service, wait, work)
and records exact fractional times. The referee counts rule breaks at the
moment work starts and never blocks anything.
"""

from __future__ import annotations

import math
from dataclasses import replace

from foreman.model import (
    FUEL_RESERVE, REFUEL_MIN, SWAP_MIN, Farm, Tractor, pick_implement, truth_factor,
)
from foreman.nav import RoadMap
from foreman.state import JobState, TractorState, WorldState

SUB_STEP_MIN = 0.25
EPS = 1e-9


class SimTractor:
    def __init__(self, tractor: Tractor, sim: "Simulation") -> None:
        self.t = tractor
        self.sim = sim
        self.id = tractor.id
        self.node = tractor.node
        self.x, self.y = sim.xy(tractor.node)
        self.heading = 0.0
        self.fuel = tractor.fuel
        self.mounted = tractor.mounted
        self.broken = False
        self.stalled = False           # silent failure: reports "working" but covers no ground
        self.fault_at: float | None = None  # when it really failed; used to measure detection time
        self.stops: list[dict] = []
        self.cur: dict | None = None   # stop being executed
        self.phase: str | None = None  # travel | service | wait | work
        self.idx = 0                   # index in cur["path"] of the last node reached
        self.leg_t = 0.0               # minutes spent on the current leg
        self.queue: list[list] = []    # pending [kind, minutes_left] service items
        self.rate = 0.0                # work rate in ha/min for the job in progress
        # idle: no task while there is work it could do. unassigned: no task while any job remains.
        self.minutes = {"work": 0.0, "travel": 0.0, "service": 0.0, "wait": 0.0, "idle": 0.0,
                        "unassigned": 0.0}
        self.km = 0.0
        self.plan_no = 0               # number of the newest plan this tractor accepted

    # ---- adapter ----------------------------------------------------------

    def status(self) -> dict:
        job = self.cur["job"] if self.cur and not self.cur.get("abort") else None
        return {
            "id": self.id, "x": self.x, "y": self.y, "node": self._heading_to(),
            "heading": self.heading, "state": self._state(), "job": job,
            "pct": self._pct(), "fuel": self.fuel, "mounted": self.mounted,
        }

    def dispatch(self, stops: list[dict], plan_no: int | None = None) -> None:
        # A command from an older plan may arrive late. It must never undo a newer one.
        if plan_no is not None:
            if plan_no < self.plan_no:
                return
            self.plan_no = plan_no
        new = [dict(s) for s in stops]
        if self.phase == "work":
            # Keep working; never do the same job twice.
            if new and new[0]["job"] == self.cur["job"]:
                new.pop(0)
        elif self.cur is not None:
            self._abort_current()
        self.stops = new

    def stop(self) -> None:
        if self.phase == "work":
            self._release_job()
            self._clear_current()
        self.dispatch([])

    def planning_state(self) -> TractorState:
        now = self.sim.now
        node, free_at, fuel, mounted = self.node, now, self.fuel, self.mounted
        if self.phase == "travel" and self.leg_t > EPS:
            km, minutes = self._leg()
            node, free_at = self.cur["path"][self.idx + 1], now + minutes - self.leg_t
            fuel -= km * (1 - self.leg_t / minutes) * self.t.burn_km
        elif self.phase == "service":
            kind, left = self.queue[0]
            free_at = now + left
            fuel, mounted = self._after_service(kind)
        busy = self.cur["job"] if self.phase == "work" else None
        return TractorState(self.id, node, free_at, fuel, mounted, not self.broken, busy)

    def break_down(self) -> None:
        if self.fault_at is None:
            self.fault_at = self.sim.now
        if self.phase == "work":
            self._release_job()
        if self.cur is not None and self.leg_t > EPS:
            self.node = self._heading_to() if self.leg_t * 2 >= self._leg()[1] else self.node
        self.stops = []
        self._clear_current()
        self.broken = True

    def repair(self) -> None:
        self.broken = False
        self.stalled = False
        self.fault_at = None

    def stall(self) -> None:
        """Fail silently: the status still says working, but nothing gets done."""
        self.stalled = True
        if self.fault_at is None:
            self.fault_at = self.sim.now

    # ---- state helpers ----------------------------------------------------

    def _state(self) -> str:
        if self.broken:
            return "broken"
        return {"travel": "traveling", "service": "servicing", "wait": "waiting",
                "work": "working"}.get(self.phase, "idle")

    def _pct(self) -> float:
        if self.phase != "work":
            return 0.0
        area = self.sim.farm.jobs[self.cur["job"]].area_ha
        return 100.0 * (1 - self.sim.jobs[self.cur["job"]].remaining_ha / area) if area else 100.0

    def _heading_to(self) -> str:
        if self.phase == "travel" and self.leg_t > EPS:
            return self.cur["path"][self.idx + 1]
        return self.node

    def _leg(self) -> tuple[float, float]:
        """(km, minutes) of the leg being driven."""
        a, b = self.cur["path"][self.idx], self.cur["path"][self.idx + 1]
        km = self.sim.road_map.distance(a, b)
        return km, km / self.t.speed_kmh * 60.0

    def _after_service(self, kind: str) -> tuple[float, str]:
        if kind == "swap":
            return self.fuel, self.cur["implement"]
        return self.t.fuel_capacity, self.mounted

    def _clear_current(self) -> None:
        self.cur, self.phase, self.queue = None, None, []
        self.idx, self.leg_t = 0, 0.0

    def _release_job(self) -> None:
        job = self.sim.jobs[self.cur["job"]]
        job.status, job.tractor = "pending", None

    def _abort_current(self) -> None:
        """Drop the current stop, but finish a road leg or service in progress."""
        if self.phase == "travel" and self.leg_t > EPS:
            self.cur["abort"] = True
            self.cur["path"] = self.cur["path"][: self.idx + 2]
        elif self.phase == "service":
            self.cur["abort"] = True
            self.queue = self.queue[:1]
            self.cur["path"] = self.cur["path"][: self.idx + 1]
        else:
            self._clear_current()

    # ---- time stepping ----------------------------------------------------

    def step(self, dt: float, t: float) -> None:
        """Spend dt minutes starting at time t, chaining phases as time allows."""
        rem = dt
        while rem > EPS and not self.broken:
            if self.cur is None and not self._begin_next_stop():
                if self.sim.open_jobs():
                    self.minutes["unassigned"] += rem
                if self.sim.has_doable_work(self, t):
                    self.minutes["idle"] += rem
                return
            used = self._run_phase(rem, t)
            t, rem = t + used, rem - used

    def _begin_next_stop(self) -> bool:
        if not self.stops:
            return False
        self.cur = self.stops.pop(0)
        self.cur["serviced"] = False
        self.cur["fuel_flag"] = False
        self.cur["path"] = list(self.cur.get("path") or [self.node])
        self.idx, self.leg_t, self.phase = 0, 0.0, "travel"
        self._at_node()
        return True

    def _run_phase(self, rem: float, t: float) -> float:
        if self.phase == "travel":
            return self._travel(rem)
        if self.phase == "service":
            return self._service(rem)
        if self.phase == "wait":
            return self._wait(rem, t)
        return self._work(rem, t)

    def _at_node(self) -> None:
        """On reaching a node: service if it is the yard and not done yet."""
        stop = self.cur
        if self.node != self.sim.farm.depot or stop["serviced"] or stop.get("abort"):
            return
        stop["serviced"] = True
        if stop.get("swap"):
            self.queue.append(["swap", SWAP_MIN])
        if stop.get("refuel"):
            self.queue.append(["refuel", REFUEL_MIN])
        if self.queue:
            self.phase = "service"

    def _travel(self, rem: float) -> float:
        path = self.cur["path"]
        if self.idx >= len(path) - 1:
            self._arrived()
            return 0.0
        km, minutes = self._leg()
        used = min(rem, minutes - self.leg_t)
        self.leg_t += used
        driven = km * used / minutes if minutes > 0 else 0.0
        self.km += driven
        self.fuel -= driven * self.t.burn_km
        self.minutes["travel"] += used
        self._move(path[self.idx], path[self.idx + 1], self.leg_t / minutes if minutes > 0 else 1.0)
        self._check_fuel(self.sim.now_for(self, rem, used))
        if self.leg_t >= minutes - EPS:
            self.idx += 1
            self.leg_t = 0.0
            self.node = path[self.idx]
            self.x, self.y = self.sim.xy(self.node)
            self._at_node()
        return used

    def _move(self, a: str, b: str, frac: float) -> None:
        (ax, ay), (bx, by) = self.sim.xy(a), self.sim.xy(b)
        self.x, self.y = ax + (bx - ax) * frac, ay + (by - ay) * frac
        if (ax, ay) != (bx, by):
            self.heading = math.degrees(math.atan2(by - ay, bx - ax))

    def _arrived(self) -> None:
        if self.cur.get("abort") or not self.cur.get("job"):
            self._clear_current()
        else:
            self.phase = "wait"

    def _service(self, rem: float) -> float:
        item = self.queue[0]
        used = min(rem, item[1])
        item[1] -= used
        self.minutes["service"] += used
        if item[1] <= EPS:
            self.fuel, self.mounted = self._after_service(item[0])
            self.queue.pop(0)
            if not self.queue:
                self.phase = "travel"
        return used

    def _wait(self, rem: float, t: float) -> float:
        if self.sim.can_start(self, t):
            self._start_work(t)
            return 0.0
        # Waiting snaps to sub-step boundaries: the rest of this step is spent waiting.
        self.minutes["wait"] += rem
        return rem

    def _start_work(self, t: float) -> None:
        job, state = self.sim.farm.jobs[self.cur["job"]], self.sim.jobs[self.cur["job"]]
        self.sim.referee_work_start(self, job, state, t)
        implement = self.sim.farm.implements[self.mounted]
        factor = truth_factor(self.sim.farm, self.id, job.type, job.field) if self.sim.use_truth else 1.0
        self.rate = implement.ha_per_hour / 60.0 * factor
        state.status, state.tractor = "active", self.id
        if state.started_at is None:
            state.started_at = t
        self.sim.events.append({"t": t, "type": "job_started", "tractor": self.id, "job": job.id})
        self.phase = "work"

    def _work(self, rem: float, t: float) -> float:
        state = self.sim.jobs[self.cur["job"]]
        if self.stalled:
            return rem  # time passes, the field does not get covered
        need = state.remaining_ha / self.rate if self.rate > 0 else math.inf
        used = min(rem, need)
        covered = min(state.remaining_ha, self.rate * used)
        self.sim.worked_ha[state.id] = self.sim.worked_ha.get(state.id, 0.0) + covered
        state.remaining_ha = max(0.0, state.remaining_ha - self.rate * used)
        self.fuel -= self.t.burn_work * used
        self.minutes["work"] += used
        self._check_fuel(t + used)
        if used >= need - EPS:
            state.remaining_ha, state.status = 0.0, "done"
            state.done_at, state.tractor = t + used, self.id
            self.sim.events.append({"t": t + used, "type": "job_done", "tractor": self.id, "job": state.id})
            self._clear_current()
        return used

    def _check_fuel(self, t: float) -> None:
        if self.cur is None or self.cur["fuel_flag"]:
            return
        if self.fuel < FUEL_RESERVE * self.t.fuel_capacity:
            self.cur["fuel_flag"] = True
            self.sim.violate(t, "fuel", self.id, self.cur.get("job"),
                             f"fuel {self.fuel:.1f} below reserve")


class Simulation:
    def __init__(self, farm: Farm, road_map: RoadMap, use_truth: bool = True) -> None:
        self.farm = farm
        self.road_map = road_map
        self.use_truth = use_truth
        self.now = 0.0
        self.tractors: dict[str, SimTractor] = {}
        self.jobs: dict[str, JobState] = {
            j.id: JobState(j.id, j.area_ha) for j in farm.jobs.values()
        }
        self.violations: list[dict] = []
        self.events: list[dict] = []
        self.worked_ha: dict[str, float] = {}  # job id -> hectares covered so far, by anyone
        for tractor_id in sorted(farm.tractors):
            self.tractors[tractor_id] = SimTractor(farm.tractors[tractor_id], self)
        # (tractor, job type) pairs where the tractor owns an implement it can pull.
        self._can_do: set[tuple[str, str]] = {
            (tractor.id, job_type)
            for tractor in farm.tractors.values()
            for job_type in {implement.type for implement in farm.implements.values()}
            if pick_implement(farm, tractor, job_type) is not None
        }

    def advance(self, minutes: float) -> None:
        steps, left = divmod(minutes + EPS, SUB_STEP_MIN)
        for _ in range(int(steps)):
            self._step(SUB_STEP_MIN)
        if left - EPS > 0:
            self._step(left - EPS)

    def _step(self, dt: float) -> None:
        for tractor_id in sorted(self.tractors):
            self.tractors[tractor_id].step(dt, self.now)
        self.now += dt

    def world_state(self) -> WorldState:
        return WorldState(
            now=self.now,
            tractors={i: self.tractors[i].planning_state() for i in sorted(self.tractors)},
            jobs={i: replace(j) for i, j in self.jobs.items()},
        )

    def all_done(self) -> bool:
        return not self.open_jobs()

    def open_jobs(self) -> bool:
        return any(j.status != "done" for j in self.jobs.values())

    def has_doable_work(self, tractor: SimTractor, t: float) -> bool:
        """Is there a job this tractor could start now: right implement, released,
        its predecessor done, and nobody already working it?"""
        for job_id, state in self.jobs.items():
            if state.status != "pending":
                continue
            if (tractor.id, self.farm.jobs[job_id].type) in self._can_do and self._ready(job_id, t):
                return True
        return False

    def xy(self, node: str) -> tuple[float, float]:
        n = self.farm.nodes[node]
        return n.x, n.y

    def now_for(self, tractor: SimTractor, rem: float, used: float) -> float:
        """Approximate clock inside a step, for tagging fuel violations."""
        return self.now + SUB_STEP_MIN - rem + used

    # ---- start rules ------------------------------------------------------

    def _ready(self, job_id: str, t: float) -> bool:
        job = self.farm.jobs[job_id]
        # done_at <= t: a job finished later inside this step must not be seen early.
        after = self.jobs[job.after] if job.after else None
        after_ok = after is None or (after.status == "done" and after.done_at <= t + EPS)
        return t >= job.release and after_ok

    def _worker_in(self, field: str, exclude: str) -> str | None:
        for tractor_id, tractor in self.tractors.items():
            if tractor_id != exclude and tractor.phase == "work" \
                    and self.farm.jobs[tractor.cur["job"]].field == field:
                return tractor_id
        return None

    def can_start(self, tractor: SimTractor, t: float) -> bool:
        job = self.farm.jobs[tractor.cur["job"]]
        if not self._ready(job.id, t) or self._worker_in(job.field, tractor.id):
            return False
        if any(s.done_at is not None and s.done_at > t + EPS and self.farm.jobs[s.id].field == job.field
               for s in self.jobs.values()):
            return False
        # Lower id goes first among tractors that could also start here.
        for other_id in sorted(self.tractors):
            other = self.tractors[other_id]
            if other_id >= tractor.id:
                break
            if other.phase == "wait" and self.farm.jobs[other.cur["job"]].field == job.field \
                    and self._ready(other.cur["job"], t):
                return False
        return True

    # ---- referee ----------------------------------------------------------

    def violate(self, t: float, rule: str, tractor: str, job: str | None, detail: str) -> None:
        self.violations.append({"t": t, "rule": rule, "tractor": tractor, "job": job, "detail": detail})

    def referee_work_start(self, tractor: SimTractor, job, state: JobState, t: float) -> None:
        implement = self.farm.implements[tractor.mounted]
        if (implement.type != job.type or tractor.mounted not in tractor.t.implements
                or implement.min_hp > tractor.t.hp):
            self.violate(t, "implement", tractor.id, job.id, f"{tractor.mounted} cannot do {job.type}")
        other = self._worker_in(job.field, tractor.id)
        if other:
            self.violate(t, "field_busy", tractor.id, job.id, f"{other} is working {job.field}")
        if job.after and self.jobs[job.after].status != "done":
            self.violate(t, "order", tractor.id, job.id, f"{job.after} is not done")
        if state.status == "done" or any(
                o.phase == "work" and o.cur["job"] == job.id
                for o in self.tractors.values() if o is not tractor):
            self.violate(t, "repeat", tractor.id, job.id, "job already done or in progress")
        if tractor.broken:
            self.violate(t, "broken", tractor.id, job.id, "tractor is broken")

    # ---- totals -----------------------------------------------------------

    def totals(self) -> dict:
        mins = [t.minutes for t in self.tractors.values()]
        total = {k: sum(m[k] for m in mins) for k in mins[0]} if mins else {}
        done = [j for j in self.jobs.values() if j.status == "done"]
        on_time = sum(1 for j in done if j.done_at <= self.farm.jobs[j.id].deadline)
        busy = sum(total.get(k, 0.0) for k in ("work", "travel", "wait", "service"))
        return {
            "jobs_done": len(done),
            "on_time": on_time,
            "travel_min": round(total.get("travel", 0.0), 1),
            "idle_min": round(total.get("idle", 0.0) + total.get("wait", 0.0), 1),
            "unassigned_min": round(total.get("unassigned", 0.0), 1),
            "service_min": round(total.get("service", 0.0), 1),
            "working_share": round(100.0 * total.get("work", 0.0) / busy, 1) if busy else 0.0,
            "km": round(sum(t.km for t in self.tractors.values()), 1),
            "violations": len(self.violations),
        }

    def tractor_seconds(self) -> dict:
        return {i: {k: v * 60.0 for k, v in t.minutes.items()} for i, t in self.tractors.items()}
