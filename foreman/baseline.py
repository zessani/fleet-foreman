"""First come, first served dispatcher: the baseline the planner is compared against.

It obeys every hard rule (capability, fuel, job order within a field) but does
no optimisation: jobs are taken in submitted order and each is appended to the
end of the route of the capable tractor that frees up first.
"""

from __future__ import annotations

import time

from foreman.evaluator import W_UNPLANNED, Evaluator, Order, SpeedFactor
from foreman.model import Farm
from foreman.nav import RoadMap
from foreman.planner import no_tractor_reason
from foreman.state import Plan, WorldState

NO_FIT_REASON = "no tractor can fit it in within the fuel rules"


def plan_baseline(farm: Farm, road_map: RoadMap, world: WorldState,
                  previous: Plan | None = None,
                  speed_factor: SpeedFactor | None = None) -> Plan:
    """Dispatch every unfinished job first come, first served. previous is ignored."""
    started = time.perf_counter()
    evaluator = Evaluator(farm, road_map, world, speed_factor)
    order: Order = {tractor_id: [] for tractor_id in evaluator.tractor_ids}
    placed = _lock_in_progress(world, evaluator, order)
    held: dict[str, str] = {}

    waiting = sorted(evaluator.open_jobs - placed, key=lambda job_id: farm.jobs[job_id].order)
    deferred: dict[str, list[str]] = {}  # predecessor id -> jobs waiting for it
    for job_id in waiting:
        after = farm.jobs[job_id].after
        if after in evaluator.open_jobs and after not in placed and after not in held:
            deferred.setdefault(after, []).append(job_id)
        else:
            _dispatch(farm, world, evaluator, order, placed, held, deferred, job_id)

    for followers in deferred.values():  # only reachable through a dependency cycle
        for job_id in followers:
            held[job_id] = f"must follow {farm.jobs[job_id].after}, which is held"

    schedule = evaluator.schedule(order)
    if schedule is None:
        raise RuntimeError("baseline built routes the evaluator rejects")
    unplanned_cost = sum(W_UNPLANNED * farm.jobs[job_id].priority for job_id in held)
    return Plan(
        created_at=world.now,
        routes=schedule.routes,
        held=held,
        cost=schedule.cost + unplanned_cost,
        ms=(time.perf_counter() - started) * 1000.0,
    )


def _lock_in_progress(world: WorldState, evaluator: Evaluator, order: Order) -> set[str]:
    """Work in progress stays with its tractor, first in its route."""
    placed: set[str] = set()
    for tractor_id in evaluator.tractor_ids:
        busy_job = world.tractors[tractor_id].busy_job
        if busy_job in evaluator.open_jobs:
            order[tractor_id].append(busy_job)
            placed.add(busy_job)
    return placed


def _dispatch(farm: Farm, world: WorldState, evaluator: Evaluator, order: Order,
              placed: set[str], held: dict[str, str], deferred: dict[str, list[str]],
              job_id: str) -> None:
    """Place or hold one job, then handle the jobs that were waiting for it."""
    after = farm.jobs[job_id].after
    if after in held:
        held[job_id] = f"must follow {after}, which is held"
    elif not evaluator.capable_tractors(job_id):
        held[job_id] = no_tractor_reason(farm, world, job_id)
    elif _append_to_first_fit(world, evaluator, order, job_id):
        placed.add(job_id)
    else:
        held[job_id] = NO_FIT_REASON
    for follower in deferred.pop(job_id, []):
        _dispatch(farm, world, evaluator, order, placed, held, deferred, follower)


def _append_to_first_fit(world: WorldState, evaluator: Evaluator, order: Order,
                         job_id: str) -> bool:
    """Append to the route of the earliest-free capable tractor that stays feasible."""
    free_times = _free_times(world, evaluator, order)
    ranked = sorted(evaluator.capable_tractors(job_id), key=lambda t: (free_times[t], t))
    for tractor_id in ranked:
        order[tractor_id].append(job_id)
        if evaluator.cost(order) is not None:
            return True
        order[tractor_id].pop()  # not feasible (fuel, say): try the next tractor
    return False


def _free_times(world: WorldState, evaluator: Evaluator, order: Order) -> dict[str, float]:
    """When each tractor finishes its current route."""
    schedule = evaluator.schedule(order)
    free: dict[str, float] = {}
    for tractor_id in evaluator.tractor_ids:
        route = schedule.routes[tractor_id] if schedule else []
        free[tractor_id] = (route[-1].finish if route
                            else max(world.now, world.tractors[tractor_id].free_at))
    return free
