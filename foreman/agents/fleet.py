"""Fleet agent: which tractor does which job, in what order, with which implement.

Tools: the implement picker (foreman.model.pick_implement) and the planner
(foreman.planner.plan).
"""

from __future__ import annotations

from foreman.agents.base import Agent, Cycle
from foreman.model import pick_implement, why_not
from foreman.planner import CHANGE_LIGHT, CHANGE_STRICT, plan

# After these events there is new capacity or new work, so the plan may rebalance.
REBALANCE_EVENTS = {"jobs_submitted", "repaired", "job_added"}


class FleetAgent(Agent):
    name = "Fleet"

    def plan(self, cycle: Cycle) -> None:
        cycle.world = self.run.world()
        rebalance = any(event["type"] in REBALANCE_EVENTS for event in cycle.events)
        cycle.change_cost = CHANGE_LIGHT if rebalance else CHANGE_STRICT
        previous = None if cycle.from_scratch else cycle.previous
        self._pick_implements(cycle)
        cycle.plan = plan(self.farm, self.run.road_map, cycle.world, previous,
                          change_cost=cycle.change_cost)
        planned = sum(len(route) for route in cycle.plan.routes.values())
        message = (f"Planned {planned} job(s) across {len(cycle.plan.routes)} tractor(s) in "
                   f"{cycle.plan.ms:.1f} ms.")
        if previous is not None:
            message += (" Kept the other tractors' work in place." if not rebalance
                        else " Rebalanced across the fleet.")
        self.say("plan", message, planner_ms=round(cycle.plan.ms, 2), planned=planned)
        for job_id, reason in cycle.plan.held.items():
            self.say("job_held", f"{self.job_label(job_id)} is held: {reason}.", job=job_id)

    def _pick_implements(self, cycle: Cycle) -> None:
        """Call the implement picker for every job that needs a tractor chosen."""
        world = cycle.world
        homeless = self._jobs_needing_a_tractor(cycle)
        if cycle.previous is None or cycle.from_scratch:
            self.say("implements", f"Implement picker checked {len(homeless)} job(s) against "
                                   f"{len(world.tractors)} tractor(s).")
            return
        for job_id in homeless:
            job = self.farm.jobs[job_id]
            verdicts = []
            for tractor_id in sorted(self.farm.tractors):
                tractor = self.farm.tractors[tractor_id]
                if not world.tractors[tractor_id].available:
                    verdicts.append(f"{tractor.name} is out of service")
                    continue
                implement = pick_implement(self.farm, tractor, job.type)
                verdicts.append(f"{tractor.name} can pull the {implement.name}" if implement
                                else f"{tractor.name} {why_not(self.farm, tractor, job.type)}")
            self.say("implements", f"Implement picker, {self.job_label(job_id)}: "
                                   f"{'; '.join(verdicts)}.", job=job_id)

    def _jobs_needing_a_tractor(self, cycle: Cycle) -> list[str]:
        """Open jobs with no working tractor: new ones, and ones a failed tractor had."""
        world = cycle.world
        kept: set[str] = set()
        if cycle.previous is not None and not cycle.from_scratch:
            kept = {stop.job for tractor_id, route in cycle.previous.routes.items()
                    if world.tractors[tractor_id].available for stop in route}
        return [job_id for job_id, state in world.jobs.items()
                if state.status != "done" and job_id not in kept]
