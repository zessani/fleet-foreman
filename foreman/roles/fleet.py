"""planner: which tractor does which job, in what order, with which implement.

Tools: the implement picker (foreman.model.pick_implement) and the planner
(foreman.planner.plan).
"""

from __future__ import annotations

from foreman.roles.base import Cycle, Role
from foreman.model import pick_implement, why_not
from foreman.model import clock
from foreman.planner import CHANGE_LIGHT, CHANGE_STRICT, plan_with_return

# After these events there is new capacity or new work, so the plan may rebalance.
REBALANCE_EVENTS = {"jobs_submitted", "repaired", "job_added", "downtime_estimate",
                    "downtime_expired", "ai_caution", "hold_released"}
# Resilience is preventive.  During a real incident, the recovery contract
# (preserve active work, wait for a short repair, minimize churn) takes priority.
RECOVERY_EVENTS = {"breakdown", "repaired", "drift", "downtime_estimate",
                   "downtime_expired", "hold_released"}


class FleetPlanner(Role):
    name = "Planner"

    def plan(self, cycle: Cycle) -> None:
        cycle.world = self.run.world()
        rebalance = any(event["type"] in REBALANCE_EVENTS for event in cycle.events)
        cycle.change_cost = CHANGE_LIGHT if rebalance else CHANGE_STRICT
        previous = None if cycle.from_scratch else cycle.previous
        self._pick_implements(cycle)
        cycle.plan = plan_with_return(
            self.farm, self.run.road_map, cycle.world, previous,
            returning=self.run.returning, waiting_reason=self._waiting_reason, had=self.run.had,
            speed_factor=self.run.speed_factor, change_cost=cycle.change_cost,
            hold=self.run.ai_holds, spread_chain=self.run.spread_chain,
            resilience=not any(event["type"] in RECOVERY_EVENTS for event in cycle.events))
        planned = sum(len(route) for route in cycle.plan.routes.values())
        message = (f"Planned {planned} job(s) across {len(cycle.plan.routes)} tractor(s) in "
                   f"{cycle.plan.ms:.1f} ms.")
        resilience = cycle.plan.resilience
        if resilience:
            if resilience["changed"]:
                message += (f" Resilience selected a schedule {resilience['operating_increase_pct']:.1f}% "
                            f"above the cheapest operating plan and reduced estimated disruption "
                            f"damage by {resilience['resilience_improvement_pct']:.1f}%.")
            else:
                message += " The cheapest operating plan was also the best resilient alternative."
        if previous is not None:
            message += (" Kept the other tractors' work in place." if not rebalance
                        else " Rebalanced across the fleet.")
        self.say("plan", message, planner_ms=round(cycle.plan.ms, 2), planned=planned,
                 resilience=resilience)
        for job_id, reason in cycle.plan.held.items():
            self.say("job_held", f"{self.job_label(job_id)} is held: {reason}.", job=job_id)

    def _waiting_reason(self, tractor_id: str) -> str:
        back = clock(self.farm.day_start_min, self.run.returning[tractor_id])
        return (f"waiting for {self.tractor_name(tractor_id)}, expected back {back} "
                "(AI Recovery agent estimate)")

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
