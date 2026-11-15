"""Exact timing and cost of a set of routes.

Given which tractor does which jobs in which order, this works out when every
job starts and finishes, following the rules of CLAUDE.md section 10: road
travel, implement swap and refuel at the yard, the fuel reserve, release
times, job order within a field, and one machine per field at a time.

The planner and the baseline both score their plans here, and the simulator
follows the same rules, so predicted and simulated times agree.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from foreman.model import (FUEL_RESERVE, REFUEL_BELOW, REFUEL_MIN, SWAP_MIN, Farm,
                           pick_implement, work_minutes)
from foreman.nav import RoadMap
from foreman.state import Stop, WorldState

# Cost weights, most important first (CLAUDE.md section 11.4).
W_UNPLANNED = 100_000.0  # per priority point of a job left out
W_LATE_JOB = 2_000.0     # per priority point of a job that misses its deadline
W_LATE_MIN = 20.0        # per priority point, per minute late
W_DRIVE = 1.0            # per minute of travel or yard service
W_WAIT = 0.4             # per minute waiting at a field, or idle while jobs remain
W_EARLY = 0.01           # per priority point, per minute until a job finishes

# (tractor id, implement id, field id) -> multiplier on the catalog work rate.
SpeedFactor = Callable[[str, str, str], float]

Order = dict[str, list[str]]  # tractor id -> job ids in the order it does them


@dataclass
class Schedule:
    routes: dict[str, list[Stop]]
    cost: float
    travel_min: float
    service_min: float
    wait_min: float
    idle_min: float
    late_jobs: int
    makespan: float


@dataclass(frozen=True)
class _Leg:
    """Getting one tractor from where it is to the start of one job."""

    implement: str
    work_min: float
    via_yard: bool
    swap: bool
    refuel: bool
    km: float
    travel_min: float
    service_min: float
    arrive: float
    fuel_after: float  # fuel left when the job is finished


class Evaluator:
    def __init__(self, farm: Farm, road_map: RoadMap, world: WorldState,
                 speed_factor: SpeedFactor | None = None) -> None:
        self.farm = farm
        self.road_map = road_map
        self.world = world
        self.depot = farm.depot
        self.tractor_ids: list[str] = sorted(
            tractor_id for tractor_id, state in world.tractors.items() if state.available
        )
        self.open_jobs: set[str] = {
            job_id for job_id, state in world.jobs.items() if state.status != "done"
        }
        self._done_at: dict[str, float] = {
            job_id: (state.done_at or 0.0)
            for job_id, state in world.jobs.items() if state.status == "done"
        }

        # Distances are copied into a plain dict once: the search asks for them
        # tens of thousands of times per plan.
        nodes = list(farm.nodes)
        self._km: dict[tuple[str, str], float] = {
            (a, b): road_map.distance(a, b) for a in nodes for b in nodes
        }
        self._passes_yard: set[tuple[str, str]] = {
            (a, b) for a in nodes for b in nodes
            if a != b and self.depot in road_map.path(a, b)[1:-1]
        }

        # (tractor, job) -> (implement id, minutes of work left). Missing = cannot do it.
        self._option: dict[tuple[str, str], tuple[str, float]] = {}
        for tractor_id in self.tractor_ids:
            tractor = farm.tractors[tractor_id]
            for job_id in self.open_jobs:
                job = farm.jobs[job_id]
                implement = pick_implement(farm, tractor, job.type)
                if implement is None:
                    continue
                factor = speed_factor(tractor_id, implement.id, job.field) if speed_factor else 1.0
                minutes = work_minutes(implement, world.jobs[job_id].remaining_ha, factor)
                self._option[(tractor_id, job_id)] = (implement.id, minutes)

    def can_do(self, tractor_id: str, job_id: str) -> bool:
        return (tractor_id, job_id) in self._option

    def capable_tractors(self, job_id: str) -> list[str]:
        return [t for t in self.tractor_ids if (t, job_id) in self._option]

    def cost(self, order: Order) -> float | None:
        """Cost of these routes, or None if they cannot be carried out."""
        result = self._run(order, detail=False)
        return None if result is None else result.cost

    def schedule(self, order: Order) -> Schedule | None:
        """Full timing of these routes, or None if they cannot be carried out."""
        return self._run(order, detail=True)

    def _run(self, order: Order, detail: bool) -> Schedule | None:
        world, farm = self.world, self.farm
        now = world.now
        tractor_ids = self.tractor_ids
        count = len(tractor_ids)
        routes = [order.get(tractor_id, ()) for tractor_id in tractor_ids]
        cursor = [0] * count
        node = [world.tractors[t].node for t in tractor_ids]
        free = [max(now, world.tractors[t].free_at) for t in tractor_ids]
        fuel = [world.tractors[t].fuel for t in tractor_ids]
        mounted = [world.tractors[t].mounted for t in tractor_ids]

        legs: list[_Leg | None] = [None] * count
        remaining = 0
        for i in range(count):
            if routes[i]:
                remaining += len(routes[i])
                legs[i] = self._leg(tractor_ids[i], node[i], free[i], fuel[i], mounted[i], routes[i][0])
                if legs[i] is None:
                    return None

        field_free: dict[str, float] = {}
        finish_of = dict(self._done_at)
        stops: dict[str, list[Stop]] = {t: [] for t in tractor_ids}
        travel = service = wait = late_cost = early = 0.0
        late_jobs = 0

        # Jobs are fixed one at a time in order of start time, the same order a
        # clock would reach them. That is what makes field sharing and "after"
        # constraints between different tractors come out right.
        while remaining:
            best = -1
            best_start = math.inf
            for i in range(count):
                leg = legs[i]
                if leg is None:
                    continue
                job = farm.jobs[routes[i][cursor[i]]]
                start = leg.arrive if leg.arrive > job.release else job.release
                if job.after is not None:
                    after_finish = finish_of.get(job.after)
                    if after_finish is None:
                        if job.after in self.open_jobs:
                            continue  # its predecessor is not placed yet
                    elif after_finish > start:
                        start = after_finish
                field_ready = field_free.get(job.field, 0.0)
                if field_ready > start:
                    start = field_ready
                if start < best_start:
                    best, best_start = i, start
            if best < 0:
                return None  # every remaining job waits on one that never runs

            i = best
            leg = legs[i]
            job = farm.jobs[routes[i][cursor[i]]]
            finish = best_start + leg.work_min
            late = finish - job.deadline
            travel += leg.travel_min
            service += leg.service_min
            wait += best_start - leg.arrive
            early += job.priority * (finish - now)
            if late > 1e-6:
                late_jobs += 1
                late_cost += job.priority * (W_LATE_JOB + W_LATE_MIN * late)

            if detail:
                stops[tractor_ids[i]].append(self._stop(
                    tractor_ids[i], job.id, job.field, node[i], free[i], leg, best_start, finish,
                    max(0.0, late)))

            field_free[job.field] = finish
            finish_of[job.id] = finish
            node[i], free[i], fuel[i], mounted[i] = job.field, finish, leg.fuel_after, leg.implement
            cursor[i] += 1
            remaining -= 1
            if cursor[i] < len(routes[i]):
                legs[i] = self._leg(tractor_ids[i], node[i], free[i], fuel[i], mounted[i],
                                    routes[i][cursor[i]])
                if legs[i] is None:
                    return None
            else:
                legs[i] = None

        makespan = max(free) if count else now
        idle = sum(makespan - free_at for free_at in free)
        cost = (late_cost + W_DRIVE * (travel + service) + W_WAIT * (wait + idle) + W_EARLY * early)
        return Schedule(stops, cost, travel, service, wait, idle, late_jobs, makespan)

    def _leg(self, tractor_id: str, node: str, free_at: float, fuel: float, mounted: str,
             job_id: str) -> _Leg | None:
        """Route one tractor to one job. None if it cannot do it within the fuel rules."""
        option = self._option.get((tractor_id, job_id))
        if option is None:
            return None
        implement, work = option
        tractor = self.farm.tractors[tractor_id]
        field = self.farm.jobs[job_id].field
        depot, km = self.depot, self._km
        reserve = FUEL_RESERVE * tractor.fuel_capacity
        work_burn = work * tractor.burn_work
        # A tractor must end the job with enough fuel to drive back to the yard
        # and still be above the reserve, or it would be stranded in the field.
        home_burn = km[(field, depot)] * tractor.burn_km

        swap = implement != mounted
        via_yard = swap or node == depot or (node, field) in self._passes_yard
        must_refuel = False
        if not via_yard:
            direct_km = km[(node, field)]
            fuel_after = fuel - direct_km * tractor.burn_km - work_burn
            if fuel_after - home_burn >= reserve:
                minutes = direct_km / tractor.speed_kmh * 60.0
                return _Leg(implement, work, False, False, False, direct_km, minutes, 0.0,
                            free_at + minutes, fuel_after)
            via_yard = must_refuel = True

        km_in, km_out = km[(node, depot)], km[(depot, field)]
        if math.isinf(km_in + km_out):
            return None
        fuel_at_yard = fuel - km_in * tractor.burn_km
        refuel = must_refuel or fuel_at_yard < REFUEL_BELOW * tractor.fuel_capacity
        out_burn = km_out * tractor.burn_km + work_burn
        fuel_after = (tractor.fuel_capacity if refuel else fuel_at_yard) - out_burn
        if fuel_after - home_burn < reserve:
            if refuel:
                return None  # even a full tank is not enough
            refuel = True
            fuel_after = tractor.fuel_capacity - out_burn
            if fuel_after - home_burn < reserve:
                return None

        total_km = km_in + km_out
        minutes = total_km / tractor.speed_kmh * 60.0
        service = (SWAP_MIN if swap else 0.0) + (REFUEL_MIN if refuel else 0.0)
        return _Leg(implement, work, True, swap, refuel, total_km, minutes, service,
                    free_at + minutes + service, fuel_after)

    def _stop(self, tractor_id: str, job_id: str, field: str, node: str, depart: float,
              leg: _Leg, start: float, finish: float, late: float) -> Stop:
        if leg.via_yard:
            path = self.road_map.path(node, self.depot) + self.road_map.path(self.depot, field)[1:]
        else:
            path = self.road_map.path(node, field)
        return Stop(
            job=job_id, tractor=tractor_id, field=field, implement=leg.implement,
            via_yard=leg.via_yard, swap=leg.swap, refuel=leg.refuel, path=path,
            depart=depart, arrive=leg.arrive, start=start, finish=finish, late_min=late,
            km=leg.km, travel_min=leg.travel_min, service_min=leg.service_min,
            wait_min=start - leg.arrive, fuel_after=leg.fuel_after,
        )
