"""Independent plan verifier (CLAUDE.md section 12).

Every rule is re-derived here from the farm, the road map, the world state and
the plan. Nothing is imported from the planner, evaluator or baseline, so a bug
there cannot hide a violation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from foreman.model import FUEL_RESERVE, Farm
from foreman.nav.roadmap import RoadMap
from foreman.state import Plan, Stop, WorldState

TOL = 1e-6
WORK_TOL_MIN = 1.0  # a stop may be one minute shorter than the exact work time

SpeedFactor = Callable[[str, str, str], float]


@dataclass
class Check:
    id: int
    name: str
    passed: bool
    severity: str  # "hard" for rules 1-8, "warning" for rule 9
    detail: str


@dataclass
class SafetyReport:
    checks: list[Check]

    @property
    def passed(self) -> bool:
        # Warnings (deadlines) never block dispatch.
        return all(c.passed for c in self.checks if c.severity == "hard")

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checks": [
                {"id": c.id, "name": c.name, "passed": c.passed,
                 "severity": c.severity, "detail": c.detail}
                for c in self.checks
            ],
        }

    def summary(self) -> str:
        ok = sum(1 for c in self.checks if c.passed)
        return f"Safety Agent passed {ok} of {len(self.checks)} checks."

    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.severity == "hard" and not c.passed]


def _check(rule: int, name: str, problems: list[str], ok_detail: str, severity: str = "hard") -> Check:
    if problems:
        return Check(rule, name, False, severity, "; ".join(problems))
    return Check(rule, name, True, severity, ok_detail)


def _all_stops(plan: Plan) -> list[Stop]:
    return [stop for route in plan.routes.values() for stop in route]


def _legs(path: list[str]) -> list[tuple[str, str]]:
    return list(zip(path, path[1:]))


# 1
def check_one_tractor_per_job(farm: Farm, world: WorldState, plan: Plan) -> Check:
    problems: list[str] = []
    seen: dict[str, str] = {}
    for stop in _all_stops(plan):
        if stop.job in seen:
            problems.append(f"job {stop.job} is on both {seen[stop.job]} and {stop.tractor}")
        seen[stop.job] = stop.tractor
        if stop.job not in farm.jobs:
            problems.append(f"job {stop.job} on {stop.tractor} does not exist")
        state = world.jobs.get(stop.job)
        if state is not None and state.status == "done":
            problems.append(f"job {stop.job} on {stop.tractor} is already done")
    return _check(1, "Each job has one tractor, none rescheduled", problems,
                  f"{len(seen)} jobs planned, each once, none already done")


# 2
def check_right_implement(farm: Farm, plan: Plan) -> Check:
    problems: list[str] = []
    count = 0
    for key, route in plan.routes.items():
        for stop in route:
            count += 1
            tag = f"{stop.job} on {key}"
            if stop.tractor != key:
                problems.append(f"{tag}: stop says tractor {stop.tractor}")
            implement = farm.implements.get(stop.implement)
            tractor = farm.tractors.get(key)
            job = farm.jobs.get(stop.job)
            if implement is None:
                problems.append(f"{tag}: implement {stop.implement} does not exist")
                continue
            if job is not None and implement.type != job.type:
                problems.append(f"{tag}: {implement.id} is {implement.type}, job needs {job.type}")
            if tractor is None:
                continue
            if stop.implement not in tractor.implements:
                problems.append(f"{tag}: {key} does not own {implement.id}")
            if implement.min_hp > tractor.hp + TOL:
                problems.append(f"{tag}: {implement.id} needs {implement.min_hp:g} hp, {key} has {tractor.hp:g}")
    return _check(2, "Right implement, owned, enough power", problems,
                  f"{count} stops use the right implement type, owned, with enough power")


# 3
def _stop_fuel_problems(farm: Farm, road_map: RoadMap, stop: Stop, fuel: float,
                        tractor_id: str) -> tuple[float, list[str]]:
    """Walk one stop and return (fuel at the end of the work, problems)."""
    tractor = farm.tractors[tractor_id]
    reserve = FUEL_RESERVE * tractor.fuel_capacity
    tag = f"{stop.job} on {tractor_id}"
    problems: list[str] = []
    refueled = False

    def at_node(node: str, fuel_now: float) -> float:
        nonlocal refueled
        # Refuelling only happens the first time the tractor is at the yard.
        if stop.refuel and not refueled and node == farm.depot:
            refueled = True
            return tractor.fuel_capacity
        return fuel_now

    if stop.path:
        fuel = at_node(stop.path[0], fuel)
        for a, b in _legs(stop.path):
            km = road_map.distance(a, b)
            if math.isinf(km):
                break  # rule 6 reports the missing road
            fuel -= km * tractor.burn_km
            if fuel < reserve - TOL:
                problems.append(f"{tag}: fuel {fuel:.1f} below reserve {reserve:.1f} driving {a} to {b}")
            fuel = at_node(b, fuel)
    if (stop.refuel or stop.swap) and farm.depot not in stop.path:
        problems.append(f"{tag}: refuel or swap set but the yard is not on the path")
    fuel -= (stop.finish - stop.start) * tractor.burn_work
    if fuel < reserve - TOL:
        problems.append(f"{tag}: fuel {fuel:.1f} below reserve {reserve:.1f} after work")
    home = road_map.distance(stop.field, farm.depot) * tractor.burn_km
    if not math.isinf(home) and fuel - home < reserve - TOL:
        problems.append(f"{tag}: only {fuel - home:.1f} fuel left at the yard, reserve is {reserve:.1f}")
    return fuel, problems


def check_fuel(farm: Farm, road_map: RoadMap, world: WorldState, plan: Plan) -> Check:
    problems: list[str] = []
    count = 0
    for tractor_id, route in plan.routes.items():
        if tractor_id not in farm.tractors or tractor_id not in world.tractors:
            continue  # rule 7 reports unknown tractors
        fuel = world.tractors[tractor_id].fuel
        for stop in route:
            count += 1
            fuel, found = _stop_fuel_problems(farm, road_map, stop, fuel, tractor_id)
            problems.extend(found)
    return _check(3, "Fuel above reserve, enough to get home", problems,
                  f"{count} stops keep fuel above the reserve with enough to drive home")


# 4
def check_one_machine_per_field(plan: Plan) -> Check:
    problems: list[str] = []
    by_field: dict[str, list[Stop]] = {}
    for stop in _all_stops(plan):
        by_field.setdefault(stop.field, []).append(stop)
    for field_id, stops in sorted(by_field.items()):
        stops = sorted(stops, key=lambda s: (s.start, s.finish))
        for first, second in zip(stops, stops[1:]):
            if second.start < first.finish - TOL:
                problems.append(f"field {field_id}: {first.job} ({first.tractor}) until {first.finish:g} "
                                f"overlaps {second.job} ({second.tractor}) from {second.start:g}")
    n = sum(len(v) for v in by_field.values())
    return _check(4, "One machine per field at a time", problems,
                  f"{n} stops, {len(by_field)} fields, no overlap")


# 5
def check_order_and_release(farm: Farm, world: WorldState, plan: Plan) -> Check:
    problems: list[str] = []
    stops = _all_stops(plan)
    by_job = {s.job: s for s in stops}
    for stop in stops:
        job = farm.jobs.get(stop.job)
        if job is None:
            continue
        if stop.start < job.release - TOL:
            problems.append(f"{stop.job} starts at {stop.start:g}, before release {job.release:g}")
        if not job.after:
            continue
        pred_state = world.jobs.get(job.after)
        if pred_state is not None and pred_state.status == "done":
            continue
        pred = by_job.get(job.after)
        if pred is None:
            problems.append(f"{stop.job} needs {job.after} first, which is neither done nor planned")
        elif pred.finish > stop.start + TOL:
            problems.append(f"{stop.job} starts at {stop.start:g} before {job.after} finishes at {pred.finish:g}")
    return _check(5, "Order and release times respected", problems,
                  f"{len(stops)} stops start after release and after their predecessors")


# 6
def check_roads(farm: Farm, road_map: RoadMap, world: WorldState, plan: Plan,
                blocked: set[frozenset[str]]) -> Check:
    problems: list[str] = []
    legs = 0
    for tractor_id, route in plan.routes.items():
        state = world.tractors.get(tractor_id)
        here = state.node if state else None
        for stop in route:
            tag = f"{stop.job} on {tractor_id}"
            job = farm.jobs.get(stop.job)
            if not stop.path:
                problems.append(f"{tag}: empty path")
            else:
                if here is not None and stop.path[0] != here:
                    problems.append(f"{tag}: path starts at {stop.path[0]}, tractor is at {here}")
                if stop.path[-1] != stop.field:
                    problems.append(f"{tag}: path ends at {stop.path[-1]}, field is {stop.field}")
            if job is not None and stop.field != job.field:
                problems.append(f"{tag}: stop field {stop.field}, job field {job.field}")
            for a, b in _legs(stop.path):
                legs += 1
                try:
                    km = road_map.distance(a, b)
                except KeyError:
                    km = math.inf
                if math.isinf(km):
                    problems.append(f"{tag}: no road from {a} to {b}")
                if frozenset((a, b)) in blocked:
                    problems.append(f"{tag}: road {a}-{b} is closed")
            here = stop.field
    return _check(6, "Routes use open roads", problems,
                  f"{legs} road legs, all connected and open")


# 7
def check_working_tractors(farm: Farm, world: WorldState, plan: Plan) -> Check:
    problems: list[str] = []
    used = 0
    for tractor_id, route in plan.routes.items():
        if not route:
            continue
        used += 1
        state = world.tractors.get(tractor_id)
        if state is None or tractor_id not in farm.tractors:
            problems.append(f"{tractor_id} is not a known tractor but has {len(route)} stops")
        elif not state.available:
            problems.append(f"{tractor_id} is down but has {len(route)} stops")
    return _check(7, "Only working tractors get jobs", problems,
                  f"{used} tractors with work, all available")


# 8
def check_no_work_lost(farm: Farm, world: WorldState, plan: Plan,
                       speed_factor: SpeedFactor | None) -> Check:
    problems: list[str] = []
    stops = _all_stops(plan)
    counts: dict[str, int] = {}
    for stop in stops:
        counts[stop.job] = counts.get(stop.job, 0) + 1
    open_jobs = [j for j, s in world.jobs.items() if s.status != "done"]
    for job_id in open_jobs:
        n = counts.get(job_id, 0)
        held = job_id in plan.held
        if n == 0 and not held:
            problems.append(f"{job_id} is neither planned nor held")
        elif n > 0 and held:
            problems.append(f"{job_id} is both planned and held")
        elif n > 1:
            problems.append(f"{job_id} is planned {n} times")
    for stop in stops:
        implement = farm.implements.get(stop.implement)
        state = world.jobs.get(stop.job)
        if implement is None or state is None:
            continue
        factor = speed_factor(stop.tractor, stop.implement, stop.field) if speed_factor else 1.0
        need = state.remaining_ha / (implement.ha_per_hour * factor) * 60.0
        have = stop.finish - stop.start
        if have < need - WORK_TOL_MIN:
            problems.append(f"{stop.job} on {stop.tractor} gets {have:.1f} min but needs {need:.1f}")
    return _check(8, "No work lost or repeated", problems,
                  f"{len(open_jobs)} unfinished jobs: {len(counts)} planned, {len(plan.held)} held, "
                  f"each stop long enough")


# 9
def check_deadlines(farm: Farm, plan: Plan) -> Check:
    late: list[str] = []
    for stop in _all_stops(plan):
        job = farm.jobs.get(stop.job)
        if job is not None and stop.finish > job.deadline + TOL:
            late.append(f"{stop.job} on {stop.tractor} is {stop.finish - job.deadline:.0f} min late")
    return _check(9, "Deadlines met", late, "every stop finishes by its deadline", severity="warning")


def verify(farm: Farm, road_map: RoadMap, world: WorldState, plan: Plan,
           blocked: set[frozenset[str]] | None = None,
           speed_factor: SpeedFactor | None = None) -> SafetyReport:
    closed = blocked or set()
    return SafetyReport([
        check_one_tractor_per_job(farm, world, plan),
        check_right_implement(farm, plan),
        check_fuel(farm, road_map, world, plan),
        check_one_machine_per_field(plan),
        check_order_and_release(farm, world, plan),
        check_roads(farm, road_map, world, plan, closed),
        check_working_tractors(farm, world, plan),
        check_no_work_lost(farm, world, plan, speed_factor),
        check_deadlines(farm, plan),
    ])
