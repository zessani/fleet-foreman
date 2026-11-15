""""Why?" for a job (CLAUDE.md section 15).

Shows the tractor the plan chose, then every other tractor with the real
reason it was not chosen. Each reason is worked out from the plan and the
world state with the same evaluator the planner uses, so nothing is invented.
"""

from __future__ import annotations

from foreman.evaluator import Evaluator, Order, Schedule, SpeedFactor
from foreman.model import Farm, clock, pick_implement, why_not
from foreman.nav import RoadMap
from foreman.state import Plan, Stop, WorldState


def why(farm: Farm, road_map: RoadMap, world: WorldState, plan: Plan, job_id: str,
        speed_factor: SpeedFactor | None = None) -> dict:
    """Explain who does this job and why the other tractors do not.

    Raises KeyError for a job id the farm does not have.
    """
    job = farm.jobs[job_id]
    job_state = world.jobs[job_id]
    label = _label(farm, job_id)
    result = {"job": job_id, "label": label, "status": job_state.status, "chosen": None,
              "held_reason": None, "others": [], "text": []}

    if job_state.status == "done":
        result["chosen"] = _done_choice(farm, world, job_id)
        result["text"] = [_done_sentence(farm, label, result["chosen"])]
        return result

    stop = plan.stop_for(job_id)
    if stop is not None:
        result["chosen"] = _choice(farm, stop)
        result["text"].append(_chosen_sentence(farm, label, stop, job_state.status == "active"))
    elif job_id in plan.held:
        result["status"] = "held"
        result["held_reason"] = plan.held[job_id]
        result["text"].append(f"{label} is held: {plan.held[job_id].rstrip('.')}.")
    else:
        result["text"].append(f"{label} is not in the current plan.")

    chosen_id = stop.tractor if stop else None
    comparison = _Comparison(farm, road_map, world, plan, job_id, stop, speed_factor)
    for tractor_id in sorted(farm.tractors):
        if tractor_id == chosen_id:
            continue
        name = farm.tractors[tractor_id].name
        reason = comparison.reason(tractor_id)
        result["others"].append({"tractor": tractor_id, "name": name, "reason": reason})
        result["text"].append(f"{name} {reason}.")
    return result


class _Comparison:
    """Tries the job on other tractors, against the plan as it stands."""

    def __init__(self, farm: Farm, road_map: RoadMap, world: WorldState, plan: Plan, job_id: str,
                 stop: Stop | None, speed_factor: SpeedFactor | None) -> None:
        self.farm, self.road_map, self.world = farm, road_map, world
        self.plan, self.job_id, self.stop = plan, job_id, stop
        self.speed_factor = speed_factor
        self._ready = False

    def reason(self, tractor_id: str) -> str:
        farm, world = self.farm, self.world
        tractor = farm.tractors[tractor_id]
        job = farm.jobs[self.job_id]
        if not world.tractors[tractor_id].available:
            return "is out of service"
        cannot = why_not(farm, tractor, job.type)
        if cannot:
            return cannot
        if self.stop is not None and world.tractors[self.stop.tractor].busy_job == self.job_id:
            chosen_name = farm.tractors[self.stop.tractor].name
            return f"is not used because {chosen_name} has already started it"

        self._prepare()
        best = self._best_on(tractor_id)
        if best is None:
            return "cannot fit it in within the fuel rules"
        return self._compare(best)

    def _prepare(self) -> None:
        """Build the evaluator and the plan's own schedule once, only if needed."""
        if self._ready:
            return
        self._ready = True
        self.evaluator = Evaluator(self.farm, self.road_map, self.world, self.speed_factor)
        usable = set(self.evaluator.tractor_ids)
        open_jobs = self.evaluator.open_jobs
        self.order: Order = {
            tractor_id: [j for j in route if j in open_jobs]
            for tractor_id, route in self.plan.job_order().items() if tractor_id in usable
        }
        for tractor_id in usable:
            self.order.setdefault(tractor_id, [])
        self.base: Schedule | None = self.evaluator.schedule(self.order)

    def _best_on(self, tractor_id: str) -> Schedule | None:
        """The cheapest way to give the job to this tractor, or None if there is none."""
        if not self.evaluator.can_do(tractor_id, self.job_id):
            return None
        without = {t: [j for j in route if j != self.job_id] for t, route in self.order.items()}
        route = without[tractor_id]
        # Work in progress stays first in its tractor's route.
        first = 1 if route and route[0] == self.world.tractors[tractor_id].busy_job else 0
        best_cost, best_order = None, None
        for position in range(first, len(route) + 1):
            trial = dict(without)
            trial[tractor_id] = route[:position] + [self.job_id] + route[position:]
            cost = self.evaluator.cost(trial)
            if cost is not None and (best_cost is None or cost < best_cost):
                best_cost, best_order = cost, trial
        return None if best_order is None else self.evaluator.schedule(best_order)

    def _compare(self, option: Schedule) -> str:
        farm = self.farm
        job = farm.jobs[self.job_id]
        other = next(stop for route in option.routes.values() for stop in route
                     if stop.job == self.job_id)
        finish = clock(farm.day_start_min, other.finish)
        late = other.finish - job.deadline
        if late > 1e-6:
            return f"would finish {finish}, {max(1, _minutes(late))} min past the deadline"

        if self.stop is None:
            # The job is held, yet this tractor has room for it.
            return f"could fit it in, finishing {finish}"
        later = _minutes(other.finish - self.stop.finish)
        if later >= 1:
            chosen_name = farm.tractors[self.stop.tractor].name
            return f"would finish {finish}, {later} min later than {chosen_name}"

        if self.base is not None:
            pushed_late = self._newly_late(option)
            if pushed_late:
                return f"would make {_join(pushed_late)} late"
            extra = _minutes((option.travel_min + option.service_min)
                             - (self.base.travel_min + self.base.service_min))
            if extra >= 1:
                return f"would add {extra} min of driving and yard time"
            delayed = self._most_delayed(option)
            if delayed:
                return f"would delay {delayed[0]} by {delayed[1]} min"
            waiting = _minutes((option.wait_min + option.idle_min)
                               - (self.base.wait_min + self.base.idle_min))
            if waiting >= 1:
                return f"would leave tractors waiting {waiting} min longer"
            if option.cost < self.base.cost - 1e-6:
                return "would do about as well, and the plan keeps work where it is unless a move clearly pays"
        return "would cost more overall"

    def _newly_late(self, option: Schedule) -> list[str]:
        """Other jobs that are on time in the plan but late if this tractor takes the job."""
        on_time = {stop.job for route in self.base.routes.values() for stop in route
                   if stop.late_min <= 1e-6}
        return [_label(self.farm, stop.job)
                for route in option.routes.values() for stop in route
                if stop.late_min > 1e-6 and stop.job in on_time and stop.job != self.job_id]


    def _most_delayed(self, option: Schedule) -> tuple[str, int] | None:
        """The other job pushed back the most if this tractor takes the job."""
        planned = {stop.job: stop.finish for route in self.base.routes.values() for stop in route}
        worst_job, worst = None, 0
        for route in option.routes.values():
            for stop in route:
                if stop.job == self.job_id or stop.job not in planned:
                    continue
                delay = _minutes(stop.finish - planned[stop.job])
                if delay > worst:
                    worst_job, worst = stop.job, delay
        return None if worst_job is None else (_label(self.farm, worst_job), worst)


def _choice(farm: Farm, stop: Stop) -> dict:
    deadline = farm.jobs[stop.job].deadline
    return {
        "tractor": stop.tractor, "name": farm.tractors[stop.tractor].name,
        "implement": stop.implement, "implement_name": farm.implements[stop.implement].name,
        "start": round(stop.start, 1), "finish": round(stop.finish, 1),
        "deadline": deadline, "margin_min": _minutes(deadline - stop.finish),
    }


def _done_choice(farm: Farm, world: WorldState, job_id: str) -> dict | None:
    """The tractor that finished the job, from the world state."""
    job, state = farm.jobs[job_id], world.jobs[job_id]
    tractor = farm.tractors.get(state.tractor) if state.tractor else None
    if tractor is None:
        return None
    implement = pick_implement(farm, tractor, job.type)
    finish = state.done_at
    return {
        "tractor": tractor.id, "name": tractor.name,
        "implement": implement.id if implement else None,
        "implement_name": implement.name if implement else None,
        "start": None if state.started_at is None else round(state.started_at, 1),
        "finish": None if finish is None else round(finish, 1),
        "deadline": job.deadline,
        "margin_min": None if finish is None else _minutes(job.deadline - finish),
    }


def _chosen_sentence(farm: Farm, label: str, stop: Stop, active: bool) -> str:
    name = farm.tractors[stop.tractor].name
    implement = farm.implements[stop.implement].name
    finish = clock(farm.day_start_min, stop.finish)
    tail = _deadline_tail(farm, farm.jobs[stop.job].deadline, stop.finish)
    if active:
        return f"{label}: {name} is working it now with the {implement}, finishing {finish}, {tail}."
    start = clock(farm.day_start_min, stop.start)
    return f"{label} goes to {name} with the {implement}, {start} to {finish}, {tail}."


def _done_sentence(farm: Farm, label: str, chosen: dict | None) -> str:
    if chosen is None:
        return f"{label} is finished."
    sentence = f"{label} is finished. {chosen['name']} did it"
    if chosen["implement_name"]:
        sentence += f" with the {chosen['implement_name']}"
    if chosen["finish"] is not None:
        sentence += (f", done at {clock(farm.day_start_min, chosen['finish'])}, "
                     f"{_deadline_tail(farm, chosen['deadline'], chosen['finish'])}")
    return sentence + "."


def _deadline_tail(farm: Farm, deadline: float, finish: float) -> str:
    when = clock(farm.day_start_min, deadline)
    if finish - deadline > 1e-6:
        return f"{max(1, _minutes(finish - deadline))} min past its {when} deadline"
    return f"{_minutes(deadline - finish)} min before its {when} deadline"


def _label(farm: Farm, job_id: str) -> str:
    job = farm.jobs[job_id]
    node = farm.nodes.get(job.field)
    return f"{job.type.capitalize()} {node.name if node else job.field}"


def _minutes(value: float) -> int:
    return int(round(value))


def _join(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]
