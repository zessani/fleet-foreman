"""Orchestrator: runs the loop and routes each event to the right roles.

No human is in this loop. An event comes in, the roles named in ROUTES act
in order, the Safety gate approves or rejects, and an approved plan is
dispatched to the tractors.
"""

from __future__ import annotations

import time

from foreman.roles.base import Cycle, Role
from foreman.roles.fleet import FleetPlanner
from foreman.roles.navigation import RouteFinder
from foreman.roles.recovery import RecoveryMonitor
from foreman.roles.safety_gate import SafetyGate
from foreman.explain import explain
from foreman.memory import Memory
from foreman.run import Run

# Event -> the roles that handle it, in order (CLAUDE.md section 6).
# A route that includes Fleet ends in a new plan and, if Safety passes it, a dispatch.
ROUTES: dict[str, tuple[str, ...]] = {
    "jobs_submitted":   ("Planner", "Route finder", "Safety gate"),
    "breakdown":        ("Recovery monitor", "Planner", "Route finder", "Safety gate"),
    "repaired":         ("Recovery monitor", "Planner", "Route finder", "Safety gate"),
    "road_blocked":     ("Route finder", "Planner", "Route finder", "Safety gate"),
    "road_opened":      ("Route finder", "Planner", "Route finder", "Safety gate"),
    "priority_changed": ("Planner", "Route finder", "Safety gate"),
    "job_added":        ("Planner", "Route finder", "Safety gate"),
    "drift":            ("Recovery monitor", "Planner", "Route finder", "Safety gate"),
    # Follow-ups to an AI agent's answer. The planner and the safety gate still decide.
    "downtime_estimate": ("Planner", "Route finder", "Safety gate"),
    "downtime_expired":  ("Planner", "Route finder", "Safety gate"),
    "ai_caution":        ("Planner", "Route finder", "Safety gate"),
    "hold_released":     ("Planner", "Route finder", "Safety gate"),
    "job_done":         ("Recovery monitor",),
    "work_sample":      ("Recovery monitor",),
}


class Orchestrator(Role):
    name = "Orchestrator"

    def __init__(self, run: Run, memory: Memory) -> None:
        super().__init__(run, memory)
        self.recovery = RecoveryMonitor(run, memory)
        self.fleet = FleetPlanner(run, memory)
        self.navigation = RouteFinder(run, memory)
        self.safety = SafetyGate(run, memory)
        self._first_responder = {"Recovery monitor": self.recovery, "Route finder": self.navigation}

    def handle(self, events: list[dict]) -> Cycle:
        """Handle events that arrived together. They share one re-plan."""
        cycle = Cycle(events=events, started=time.perf_counter(), previous=self.run.plan)
        needs_plan = False
        for event in events:
            route = ROUTES[event["type"]]
            replans = "Planner" in route
            self.say("route", f"{event['type']}: {', '.join(route)}"
                              f"{', dispatch' if replans else ''}.", route=list(route))
            # Roles listed before the planner act on the event itself.
            for name in route[: route.index("Planner")] if replans else route:
                self._first_responder[name].on_event(cycle, event)
            needs_plan = needs_plan or replans
        if needs_plan:
            self._plan_check_dispatch(cycle)
        return cycle

    def _plan_check_dispatch(self, cycle: Cycle) -> None:
        self._build(cycle)
        if not cycle.safety.passed:
            # One more try, ignoring the old plan, before giving up.
            self.say("retry", "Safety rejected the plan. Planning again from scratch.")
            cycle.from_scratch = True
            self._build(cycle)
        if cycle.safety.passed:
            self._dispatch(cycle)
        else:
            self._stand_down(cycle)

    def _build(self, cycle: Cycle) -> None:
        self.fleet.plan(cycle)
        self.navigation.route(cycle)
        self.safety.check(cycle)

    def _dispatch(self, cycle: Cycle) -> None:
        run = self.run
        run.send(cycle.plan)
        ms = (time.perf_counter() - cycle.started) * 1000.0
        old = None if cycle.from_scratch and cycle.previous is None else cycle.previous
        story = explain(self.farm, cycle.world, old, cycle.plan, cycle.events[0],
                        cycle.safety.summary(), ms)
        for extra in cycle.events[1:]:
            story["headline"] += " " + explain(self.farm, cycle.world, old, cycle.plan, extra)["headline"]
        if cycle.events[0].get("headline"):
            story["headline"] = cycle.events[0]["headline"]
        run.explanation = story
        run.explanations.append({"t": round(run.now, 2), "plan_no": run.plan_no, **story})
        run.safety = cycle.safety.to_dict()
        run.replans.append({
            "t": round(run.now, 2), "plan_no": run.plan_no, "ms": round(ms, 2),
            "events": [event["type"] for event in cycle.events],
            "moved": story["moved"], "retimed": story["retimed"],
            "unchanged": story["unchanged"], "held": story["held"],
            "detection_min": max((event.get("detection_min", 0.0) for event in cycle.events),
                                 default=0.0),
        })
        names = ", ".join(self.tractor_name(t) for t in sorted(cycle.plan.routes)) or "no tractors"
        stayed = story["unchanged"] + story["retimed"]
        self.say("dispatch", f"Plan {run.plan_no} sent to {names}. {story['moved']} moved, "
                             f"{stayed} stayed with their tractor. Event to dispatch: {ms:.1f} ms.",
                 plan_no=run.plan_no, ms=round(ms, 2))

    def _stand_down(self, cycle: Cycle) -> None:
        """No plan passed Safety. Nothing unverified is sent; idle the tractors that can stop."""
        run = self.run
        stopped = []
        for tractor_id, tractor in sorted(run.tractors.items()):
            if tractor.status()["state"] not in ("working", "broken"):
                tractor.stop()
                stopped.append(self.tractor_name(tractor_id))
        run.safety = cycle.safety.to_dict()
        self.say("stand_down", "No plan passed the Safety gate, so none was sent. Stopped: "
                               f"{', '.join(stopped) or 'nobody'}. Tractors mid-job finish that job.")
