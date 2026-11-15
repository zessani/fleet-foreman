"""One world: a simulation, its current plan, and what its tractors were last told.

The Foreman world and the baseline world are both a Run. They differ only in
who decides the plan.
"""

from __future__ import annotations

from foreman.model import Farm
from foreman.nav import RoadMap
from foreman.sim import Simulation
from foreman.state import Plan, Stop, WorldState
from foreman.watch import StatusWatch

TRACTOR_COLORS = ("#E8A33D", "#4C9BE8", "#5DBB63", "#C76BD6", "#E86A5C", "#49C5B6")


class Run:
    def __init__(self, farm: Farm, road_map: RoadMap, blocked: set[frozenset[str]],
                 use_truth: bool = True) -> None:
        self.farm = farm
        self.road_map = road_map
        self.blocked = blocked  # closed roads, shared by both worlds
        self.sim = Simulation(farm, road_map, use_truth)
        self.watch = StatusWatch()
        self.plan: Plan | None = None
        self.plan_no = 0                 # counts up with every dispatched plan
        self.rows: dict[str, dict] = {}  # job id -> its latest plan row, kept after it is done
        self.explanation: dict = {}
        self.explanations: list[dict] = []  # every plan change of the day, oldest first
        self.safety: dict = {"passed": None, "checks": []}
        self.replans: list[dict] = []    # one record per dispatched plan, for the proof run
        self.speed_factor = None         # learned work-rate factors, when the learning loop is on
        # Caution added by the AI agents. The planner still decides every assignment.
        self.returning: dict[str, float] = {}  # broken tractor -> minute it is expected back
        self.had: dict[str, list[str]] = {}    # broken tractor -> the jobs it had when it failed
        self.ai_holds: dict[str, str] = {}     # job -> why the AI Safety agent holds it
        self.spread_chain = False              # keep dependent jobs off one tractor

    @property
    def now(self) -> float:
        return self.sim.now

    def world(self) -> WorldState:
        """The current state for planning. Tractors taken out of service are unavailable."""
        world = self.sim.world_state()
        for tractor_id in self.watch.down:
            world.tractors[tractor_id].available = False
        return world

    def statuses(self) -> dict[str, dict]:
        return {tractor_id: tractor.status() for tractor_id, tractor in self.sim.tractors.items()}

    def send(self, new_plan: Plan) -> None:
        """Dispatch a plan: every working tractor gets its to-do list and the plan number."""
        self.plan = new_plan
        self.plan_no += 1
        for tractor_id, stops in new_plan.routes.items():
            self.sim.tractors[tractor_id].dispatch([stop.to_dispatch() for stop in stops],
                                                   self.plan_no)
            for stop in stops:
                self.rows[stop.job] = _plan_row(stop)
        for job_id in new_plan.held:
            self.rows.pop(job_id, None)

    def block(self) -> dict:
        """The tractors, jobs, plan, and totals of CLAUDE.md section 8c for this world."""
        return {
            "tractors": [self._tractor_row(i, t) for i, t in enumerate(sorted(self.sim.tractors))],
            "jobs": [self._job_row(job_id) for job_id in self.farm.jobs],
            "plan": sorted(self.rows.values(), key=lambda row: (row["start"], row["tractor"])),
            "plan_no": self.plan_no,
            "totals": self.sim.totals(),
        }

    def _tractor_row(self, index: int, tractor_id: str) -> dict:
        tractor = self.farm.tractors[tractor_id]
        status = self.sim.tractors[tractor_id].status()
        return {
            "id": tractor_id, "name": tractor.name,
            "x": round(status["x"], 4), "y": round(status["y"], 4),
            "status": "broken" if tractor_id in self.watch.down else status["state"],
            "job": status["job"],
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
            # Unrounded, so a map can move a tractor smoothly through its field.
            "pct_exact": round(100.0 * (1.0 - state.remaining_ha / job.area_ha), 3) if job.area_ha else 100.0,
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
