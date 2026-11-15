"""Bounded resilience tie-breaker for otherwise efficient schedules.

Normal operating cost always creates the shortlist.  This module only moves
jobs when the resulting planner cost remains within five percent of the best
normal plan, then prefers the schedule with less expected damage if a tractor
fails.  It is deliberately analytical and fast enough to use inside planning;
the deterministic simulator remains the final proof tool.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from foreman.evaluator import W_LATE_JOB, W_LATE_MIN, W_UNPLANNED, W_WAIT, Evaluator, Order
from foreman.model import SWAP_MIN, Farm, pick_implement, travel_minutes, work_minutes
from foreman.nav import RoadMap
from foreman.state import WorldState

OPERATING_TOLERANCE = 0.05
RESILIENCE_WEIGHT = 0.10
MAX_RESILIENCE_PASSES = 8
MIN_RESILIENCE_GAIN = 1e-6
MIN_RELATIVE_RESILIENCE_GAIN = 0.05

OperatingScore = Callable[[Order], float | None]


@dataclass(frozen=True)
class ResilienceChoice:
    best_operating_cost: float
    selected_operating_cost: float
    before: float
    after: float
    alternatives_evaluated: int
    changed: bool

    def to_dict(self) -> dict:
        increase = 0.0
        if self.best_operating_cost > 0:
            increase = 100.0 * (self.selected_operating_cost - self.best_operating_cost) / self.best_operating_cost
        return {
            "enabled": True,
            "operating_tolerance_pct": round(100 * OPERATING_TOLERANCE, 1),
            "resilience_weight_pct": round(100 * RESILIENCE_WEIGHT, 1),
            "minimum_resilience_gain_pct": round(100 * MIN_RELATIVE_RESILIENCE_GAIN, 1),
            "best_operating_cost": round(self.best_operating_cost, 2),
            "selected_operating_cost": round(self.selected_operating_cost, 2),
            "operating_increase_pct": round(max(0.0, increase), 2),
            "resilience_before": round(self.before, 2),
            "resilience_after": round(self.after, 2),
            "resilience_improvement_pct": round(
                100.0 * (self.before - self.after) / self.before
                if self.before > 0 else 0.0, 1),
            "alternatives_evaluated": self.alternatives_evaluated,
            "changed": self.changed,
        }


def operating_limit(best_cost: float, tolerance: float = OPERATING_TOLERANCE) -> float:
    """Largest normal planner cost eligible for the resilience tie-breaker."""
    return best_cost + max(1.0, abs(best_cost) * tolerance)


def improve_within_tolerance(farm: Farm, road_map: RoadMap, world: WorldState,
                             evaluator: Evaluator, operating_score: OperatingScore,
                             order: Order, locked: dict[str, int]) -> ResilienceChoice:
    """Improve expected failure damage without leaving the operating shortlist.

    Relocations and swaps use the same neighborhood as the normal local search.
    Work in progress is locked.  The supplied order is mutated only when a
    lower-risk eligible neighbor is found.
    """
    best_operating = operating_score(order)
    if best_operating is None:
        return ResilienceChoice(0.0, 0.0, 0.0, 0.0, 0, False)
    ceiling = operating_limit(best_operating)
    current_operating = best_operating
    current_resilience = resilience_score(farm, road_map, world, evaluator, order)
    before = current_resilience
    evaluated = 0
    changed = False

    for _ in range(MAX_RESILIENCE_PASSES):
        best_move = None
        required_gain = max(MIN_RESILIENCE_GAIN,
                            current_resilience * MIN_RELATIVE_RESILIENCE_GAIN)
        required_risk = current_resilience - required_gain
        current_combined = current_operating + RESILIENCE_WEIGHT * current_resilience
        best_key = (current_combined - MIN_RESILIENCE_GAIN,
                    current_resilience, current_operating)

        for source in evaluator.tractor_ids:
            for i in range(locked[source], len(order[source])):
                job_id = order[source].pop(i)
                for target in evaluator.capable_tractors(job_id):
                    for j in range(locked[target], len(order[target]) + 1):
                        if target == source and j == i:
                            continue
                        order[target].insert(j, job_id)
                        operating = operating_score(order)
                        if operating is not None and operating <= ceiling + 1e-9:
                            risk = resilience_score(farm, road_map, world, evaluator, order)
                            evaluated += 1
                            combined = operating + RESILIENCE_WEIGHT * risk
                            if risk <= required_risk and (combined, risk, operating) < best_key:
                                best_key = (combined, risk, operating)
                                best_move = ("relocate", source, i, target, j)
                        del order[target][j]
                order[source].insert(i, job_id)

        slots = [(tractor_id, i) for tractor_id in evaluator.tractor_ids
                 for i in range(locked[tractor_id], len(order[tractor_id]))]
        for a, (first, i) in enumerate(slots):
            for second, j in slots[a + 1:]:
                job_a, job_b = order[first][i], order[second][j]
                if first != second and not (evaluator.can_do(first, job_b)
                                            and evaluator.can_do(second, job_a)):
                    continue
                order[first][i], order[second][j] = job_b, job_a
                operating = operating_score(order)
                if operating is not None and operating <= ceiling + 1e-9:
                    risk = resilience_score(farm, road_map, world, evaluator, order)
                    evaluated += 1
                    combined = operating + RESILIENCE_WEIGHT * risk
                    if risk <= required_risk and (combined, risk, operating) < best_key:
                        best_key = (combined, risk, operating)
                        best_move = ("swap", first, i, second, j)
                order[first][i], order[second][j] = job_a, job_b

        if best_move is None:
            break
        kind, first, i, second, j = best_move
        if kind == "relocate":
            order[second].insert(j, order[first].pop(i))
        else:
            order[first][i], order[second][j] = order[second][j], order[first][i]
        _, current_resilience, current_operating = best_key
        changed = True

    return ResilienceChoice(best_operating, current_operating, before, current_resilience,
                            evaluated, changed)


def resilience_score(farm: Farm, road_map: RoadMap, world: WorldState,
                     evaluator: Evaluator, order: Order) -> float:
    """Expected recovery damage from a failure before each assigned job.

    Failure probability grows with the assigned tractor's accumulated operating
    hours.  Damage uses the planner's existing deadline and unfinished-work
    weights.  A compatible backup is estimated from its location and next-free
    time in this schedule; no backup receives the normal unplanned-job penalty.
    """
    schedule = evaluator.schedule(order)
    if schedule is None:
        return math.inf
    damage = 0.0
    for tractor_id, route in schedule.routes.items():
        tractor = farm.tractors[tractor_id]
        exposure_min = 0.0
        for stop in route:
            work_min = stop.finish - stop.start
            exposure_min += stop.travel_min + stop.service_min + stop.wait_min + work_min / 2.0
            probability = 1.0 - math.exp(-tractor.breakdown_risk_per_hour * exposure_min / 60.0)
            impact = _recovery_impact(farm, road_map, world, schedule.routes,
                                      tractor_id, stop.job, stop.start)
            damage += probability * impact
            exposure_min += work_min / 2.0
    return damage


def _recovery_impact(farm: Farm, road_map: RoadMap, world: WorldState,
                     routes: dict, failed_tractor: str, job_id: str,
                     failure_at: float) -> float:
    job = farm.jobs[job_id]
    failed = farm.tractors[failed_tractor]
    failed_implement = pick_implement(farm, failed, job.type)
    repair_work = (work_minutes(failed_implement, world.jobs[job_id].remaining_ha)
                   if failed_implement else math.inf)
    # Waiting for the failed tractor is a real recovery option too.  A backup
    # only counts when it can beat the configured typical repair.
    best_finish = failure_at + failed.typical_repair_min + repair_work
    for backup_id, state in world.tractors.items():
        if backup_id == failed_tractor or not state.available:
            continue
        backup = farm.tractors[backup_id]
        implement = pick_implement(farm, backup, job.type)
        if implement is None:
            continue
        node, mounted, free_at = _backup_state_at(farm, world, routes.get(backup_id, []),
                                                   backup_id, failure_at)
        if implement.id == mounted:
            km = road_map.distance(node, job.field)
            service = 0.0
        else:
            km = road_map.distance(node, farm.depot) + road_map.distance(farm.depot, job.field)
            service = SWAP_MIN
        if math.isinf(km):
            continue
        travel = travel_minutes(km, backup.speed_kmh)
        work = work_minutes(implement, world.jobs[job_id].remaining_ha)
        finish = max(failure_at, free_at) + travel + service + work
        best_finish = min(best_finish, finish)

    if math.isinf(best_finish) or best_finish > farm.horizon_min:
        return W_UNPLANNED * job.priority
    lateness = max(0.0, best_finish - job.deadline)
    if lateness > 0:
        return job.priority * (W_LATE_JOB + W_LATE_MIN * lateness)
    original = next(stop for route in routes.values() for stop in route if stop.job == job_id)
    return W_WAIT * max(0.0, best_finish - original.finish)


def _backup_state_at(farm: Farm, world: WorldState, route: list, tractor_id: str,
                     failure_at: float) -> tuple[str, str, float]:
    state = world.tractors[tractor_id]
    node, mounted, free_at = state.node, state.mounted, max(world.now, state.free_at)
    for stop in route:
        if stop.finish <= failure_at:
            node, mounted, free_at = stop.field, stop.implement, stop.finish
        elif stop.depart <= failure_at:
            # The backup is already traveling, servicing, waiting, or working
            # this stop.  Treat it as committed until that stop finishes.
            return stop.field, stop.implement, stop.finish
        else:
            break
    return node, mounted, free_at
