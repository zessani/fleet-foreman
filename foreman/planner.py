"""The planner: which tractor does which job, in what order.

Regret insertion builds the plan, then local search improves it. Every
candidate is scored by the exact evaluator, so the plan the search likes is
the plan that will actually run.

The input is the current world state, not "everyone at the yard at 6 AM". A
re-plan is the same call with a newer state and the plan it replaces
(CLAUDE.md section 11).
"""

from __future__ import annotations

import math
import time
from typing import Callable

from foreman.evaluator import W_UNPLANNED, Evaluator, Order, SpeedFactor
from foreman.model import Farm
from foreman.nav import RoadMap
from foreman.state import Plan, WorldState

MAX_SEARCH_PASSES = 50  # a stop so a re-plan always returns quickly
MIN_GAIN = 1e-6         # ignore "improvements" that are only rounding noise

# Cost of moving a job off a tractor that is still able to do it.
# Strict sits above any travel or idle saving and below the cost of a late
# job, so a job only leaves a healthy tractor when that saves a deadline.
CHANGE_STRICT = 500.0
# Light lets the plan rebalance when capacity comes back or new work arrives.
CHANGE_LIGHT = 15.0

Score = Callable[[Order], "float | None"]


def plan(farm: Farm, road_map: RoadMap, world: WorldState, previous: Plan | None = None,
         speed_factor: SpeedFactor | None = None, local_search: bool = True,
         change_cost: float = CHANGE_STRICT) -> Plan:
    """Build a plan for every unfinished job from the current state.

    previous is the plan being replaced. The new plan starts from it and pays
    change_cost for each job it moves to a different tractor, so a re-plan
    changes as little as possible. The result depends only on the inputs:
    same input, same plan.
    """
    started = time.perf_counter()
    evaluator = Evaluator(farm, road_map, world, speed_factor)

    order: Order = {tractor_id: [] for tractor_id in evaluator.tractor_ids}
    locked: dict[str, int] = {tractor_id: 0 for tractor_id in evaluator.tractor_ids}
    for tractor_id in evaluator.tractor_ids:
        # Work in progress stays with its tractor, first in its route.
        busy_job = world.tractors[tractor_id].busy_job
        if busy_job in evaluator.open_jobs:
            order[tractor_id].append(busy_job)
            locked[tractor_id] = 1

    home = _previous_tractors(previous, evaluator)
    score = _scorer(evaluator, home, change_cost)
    _seed_from_previous(previous, evaluator, order, locked)

    placed = {job_id for route in order.values() for job_id in route}
    waiting = sorted(evaluator.open_jobs - placed, key=lambda job_id: farm.jobs[job_id].order)
    held = _insert_all(farm, world, evaluator, score, order, locked, waiting)

    if local_search:
        _improve(evaluator, score, order, locked)

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


def _previous_tractors(previous: Plan | None, evaluator: Evaluator) -> dict[str, str]:
    """Job -> the tractor that had it, for tractors that are still in service."""
    if previous is None:
        return {}
    return {
        stop.job: tractor_id
        for tractor_id, route in previous.routes.items() if tractor_id in evaluator.tractor_ids
        for stop in route
    }


def _scorer(evaluator: Evaluator, home: dict[str, str], change_cost: float) -> Score:
    """The evaluator's cost, plus a charge for each job moved off its previous tractor."""
    if not home or change_cost <= 0:
        return evaluator.cost

    def score(order: Order) -> float | None:
        cost = evaluator.cost(order)
        if cost is None:
            return None
        moved = 0
        for tractor_id, route in order.items():
            for job_id in route:
                was_on = home.get(job_id)
                if was_on is not None and was_on != tractor_id:
                    moved += 1
        return cost + change_cost * moved

    return score


def _seed_from_previous(previous: Plan | None, evaluator: Evaluator, order: Order,
                        locked: dict[str, int]) -> None:
    """Start each tractor with the unfinished jobs it already had, in the same order."""
    if previous is None:
        return
    in_progress = {job_id for route in order.values() for job_id in route}
    seeded: Order = {tractor_id: list(route) for tractor_id, route in order.items()}
    for tractor_id in evaluator.tractor_ids:
        for stop in previous.routes.get(tractor_id, []):
            if (stop.job in evaluator.open_jobs and stop.job not in in_progress
                    and evaluator.can_do(tractor_id, stop.job)):
                seeded[tractor_id].append(stop.job)
    # The old routes may no longer be possible (a predecessor lost its tractor,
    # say). Then start from the locked work alone and insert everything fresh.
    if evaluator.cost(seeded) is not None:
        for tractor_id in order:
            order[tractor_id] = seeded[tractor_id]


def _insert_all(farm: Farm, world: WorldState, evaluator: Evaluator, score: Score, order: Order,
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
            options = _best_slot_per_tractor(evaluator, score, order, locked, job_id)
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


def _best_slot_per_tractor(evaluator: Evaluator, score: Score, order: Order,
                           locked: dict[str, int], job_id: str) -> list[tuple[float, str, int]]:
    """For each capable tractor, its cheapest position for this job. Sorted, best first."""
    options: list[tuple[float, str, int]] = []
    for tractor_id in evaluator.capable_tractors(job_id):
        route = order[tractor_id]
        best: tuple[float, str, int] | None = None
        for position in range(locked[tractor_id], len(route) + 1):
            route.insert(position, job_id)
            cost = score(order)
            del route[position]
            if cost is not None and (best is None or cost < best[0]):
                best = (cost, tractor_id, position)
        if best is not None:
            options.append(best)
    options.sort()
    return options


def _improve(evaluator: Evaluator, score: Score, order: Order, locked: dict[str, int]) -> None:
    """Local search: keep applying the best single move until none helps.

    Two moves are tried: move one job to any other position on any capable
    tractor, and swap two jobs. Locked work in progress is never touched.
    """
    cost = score(order)
    if cost is None:
        return
    for _ in range(MAX_SEARCH_PASSES):
        relocate = _best_relocate(evaluator, score, order, locked, cost)
        swap = _best_swap(evaluator, score, order, locked, cost)
        if relocate is None and swap is None:
            return
        if swap is None or (relocate is not None and relocate[0] <= swap[0]):
            cost, source, i, target, j = relocate
            order[target].insert(j, order[source].pop(i))
        else:
            cost, first, i, second, j = swap
            order[first][i], order[second][j] = order[second][j], order[first][i]


def _best_relocate(evaluator: Evaluator, score: Score, order: Order, locked: dict[str, int],
                   cost_now: float) -> tuple[float, str, int, str, int] | None:
    """The cheapest "move one job elsewhere", if it beats the current cost."""
    best: tuple[float, str, int, str, int] | None = None
    threshold = cost_now - MIN_GAIN
    for source in evaluator.tractor_ids:
        for i in range(locked[source], len(order[source])):
            job_id = order[source].pop(i)
            for target in evaluator.capable_tractors(job_id):
                for j in range(locked[target], len(order[target]) + 1):
                    if target == source and j == i:
                        continue  # that is where it already was
                    order[target].insert(j, job_id)
                    cost = score(order)
                    del order[target][j]
                    if cost is not None and cost < threshold:
                        best, threshold = (cost, source, i, target, j), cost
            order[source].insert(i, job_id)
    return best


def _best_swap(evaluator: Evaluator, score: Score, order: Order, locked: dict[str, int],
               cost_now: float) -> tuple[float, str, int, str, int] | None:
    """The cheapest "exchange two jobs", if it beats the current cost."""
    best: tuple[float, str, int, str, int] | None = None
    threshold = cost_now - MIN_GAIN
    slots = [(tractor_id, i) for tractor_id in evaluator.tractor_ids
             for i in range(locked[tractor_id], len(order[tractor_id]))]
    for a, (first, i) in enumerate(slots):
        for second, j in slots[a + 1:]:
            job_a, job_b = order[first][i], order[second][j]
            if first != second and not (evaluator.can_do(first, job_b)
                                        and evaluator.can_do(second, job_a)):
                continue
            order[first][i], order[second][j] = job_b, job_a
            cost = score(order)
            order[first][i], order[second][j] = job_a, job_b
            if cost is not None and cost < threshold:
                best, threshold = (cost, first, i, second, j), cost
    return best


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
    in_service = [t for t in strong_enough if world.tractors[t.id].available]
    if in_service:
        return "no tractor can finish it on one tank and still get back to the yard"
    names = ", ".join(sorted(tractor.name for tractor in strong_enough))
    return f"the only tractors that can {job.type} are out of service ({names})"
