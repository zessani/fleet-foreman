"""The planner: which tractor does which job, in what order.

Regret insertion builds the plan, then local search improves it. Every
candidate is scored by the exact evaluator, so the plan the search likes is
the plan that will actually run.

The input is the current world state, not "everyone at the yard at 6 AM". A
re-plan is the same call with a newer state (CLAUDE.md section 11).
"""

from __future__ import annotations

import math
import time

from foreman.evaluator import W_UNPLANNED, Evaluator, Order, SpeedFactor
from foreman.model import Farm
from foreman.nav import RoadMap
from foreman.state import Plan, WorldState


def plan(farm: Farm, road_map: RoadMap, world: WorldState, previous: Plan | None = None,
         speed_factor: SpeedFactor | None = None, local_search: bool = True) -> Plan:
    """Build a plan for every unfinished job from the current state.

    previous is the plan being replaced. It is accepted now so callers do not
    change later; Phase 2 uses it to keep re-plans close to the old plan.
    The result depends only on the inputs: same input, same plan.
    """
    started = time.perf_counter()
    evaluator = Evaluator(farm, road_map, world, speed_factor)

    order: Order = {tractor_id: [] for tractor_id in evaluator.tractor_ids}
    locked: dict[str, int] = {tractor_id: 0 for tractor_id in evaluator.tractor_ids}
    in_progress: set[str] = set()
    for tractor_id in evaluator.tractor_ids:
        # Work in progress stays with its tractor, first in its route.
        busy_job = world.tractors[tractor_id].busy_job
        if busy_job in evaluator.open_jobs:
            order[tractor_id].append(busy_job)
            locked[tractor_id] = 1
            in_progress.add(busy_job)

    waiting = sorted(evaluator.open_jobs - in_progress, key=lambda job_id: farm.jobs[job_id].order)
    held = _insert_all(farm, world, evaluator, order, locked, waiting)

    if local_search:
        _improve(evaluator, order, locked)

    schedule = evaluator.schedule(order)
    if schedule is None:
        raise RuntimeError("planner built routes the evaluator rejects")
    unplanned_cost = sum(W_UNPLANNED * farm.jobs[job_id].priority for job_id in held)
    return Plan(
        created_at=world.now,
        routes=schedule.routes,
        held=held,
        cost=schedule.cost + unplanned_cost,
        ms=(time.perf_counter() - started) * 1000.0,
    )


def _insert_all(farm: Farm, world: WorldState, evaluator: Evaluator, order: Order,
                locked: dict[str, int], waiting: list[str]) -> dict[str, str]:
    """Regret insertion. Returns the jobs that could not be placed, with the reason."""
    held: dict[str, str] = {}
    for job_id in list(waiting):
        if not evaluator.capable_tractors(job_id):
            held[job_id] = no_tractor_reason(farm, world, job_id)
            waiting.remove(job_id)

    while waiting:
        placed = {job_id for route in order.values() for job_id in route}
        choice: tuple[tuple[float, float, int], str, str, int] | None = None
        for job_id in waiting:
            job = farm.jobs[job_id]
            if job.after in evaluator.open_jobs and job.after not in placed:
                continue  # place its predecessor first
            options = _best_slot_per_tractor(evaluator, order, locked, job_id)
            if not options:
                continue
            # Regret: how much worse the second-best tractor is. Jobs with few
            # good homes are placed first, before those homes fill up.
            regret = options[1][0] - options[0][0] if len(options) > 1 else math.inf
            key = (-regret, options[0][0], job.order)
            if choice is None or key < choice[0]:
                choice = (key, job_id, options[0][1], options[0][2])
        if choice is None:
            break
        _, job_id, tractor_id, position = choice
        order[tractor_id].insert(position, job_id)
        waiting.remove(job_id)

    for job_id in waiting:
        after = farm.jobs[job_id].after
        if after in held or after in waiting:
            held[job_id] = f"must follow {after}, which is held"
        else:
            held[job_id] = "no tractor can fit it in within the fuel rules"
    return held


def _best_slot_per_tractor(evaluator: Evaluator, order: Order, locked: dict[str, int],
                           job_id: str) -> list[tuple[float, str, int]]:
    """For each capable tractor, its cheapest position for this job. Sorted, best first."""
    options: list[tuple[float, str, int]] = []
    for tractor_id in evaluator.capable_tractors(job_id):
        route = order[tractor_id]
        best: tuple[float, str, int] | None = None
        for position in range(locked[tractor_id], len(route) + 1):
            route.insert(position, job_id)
            cost = evaluator.cost(order)
            del route[position]
            if cost is not None and (best is None or cost < best[0]):
                best = (cost, tractor_id, position)
        if best is not None:
            options.append(best)
    options.sort()
    return options


def _improve(evaluator: Evaluator, order: Order, locked: dict[str, int]) -> None:
    """Local search. Added after the first full day runs end to end."""


def no_tractor_reason(farm: Farm, world: WorldState, job_id: str) -> str:
    """Why no working tractor can take this job, in plain words."""
    job = farm.jobs[job_id]
    owners = [
        tractor for tractor in farm.tractors.values()
        if any(farm.implements[i].type == job.type for i in tractor.implements)
    ]
    if not owners:
        return f"no tractor has a {job.type} implement"
    strong_enough = [
        tractor for tractor in owners
        if any(farm.implements[i].type == job.type and farm.implements[i].min_hp <= tractor.hp
               for i in tractor.implements)
    ]
    if not strong_enough:
        return f"no tractor with a {job.type} implement has enough power to pull it"
    names = ", ".join(sorted(tractor.name for tractor in strong_enough))
    return f"the only tractors that can {job.type} are out of service ({names})"
