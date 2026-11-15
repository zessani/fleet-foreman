"""Navigation agent: routes between fields and keeps tractors out of each other's fields.

Tools: the route finder (foreman.nav.RoadMap) and the conflict checker below.
"""

from __future__ import annotations

import math

from foreman.roles.base import Cycle, Role
from foreman.state import Plan, Stop


class RouteFinder(Role):
    name = "Route finder"

    def on_event(self, cycle: Cycle, event: dict) -> None:
        """A road closed or reopened: update the road map before anyone plans."""
        a, b = event["a"], event["b"]
        road = frozenset((a, b))
        if event["type"] == "road_blocked":
            self.run.road_map.block(a, b)
            self.run.blocked.add(road)
            using = [stop.job for stop in _stops(cycle.previous) if road in _legs(stop.path)
                     and self.run.sim.jobs[stop.job].status != "done"]
            self.say("road_closed", f"Road {a} to {b} is closed. {len(using)} planned route(s) "
                                    "used it and will be re-routed.", road=[a, b], jobs=using)
        else:
            self.run.road_map.open(a, b)
            self.run.blocked.discard(road)
            self.say("road_open", f"Road {a} to {b} is open again.", road=[a, b])

    def route(self, cycle: Cycle) -> None:
        """Check every route in the new plan and look for tractors sharing a field."""
        stops = _stops(cycle.plan)
        bad_roads = [stop.job for stop in stops if not self._drivable(stop)]
        conflicts = field_conflicts(stops)
        km = sum(stop.km for stop in stops)
        if bad_roads or conflicts:
            self.say("route_problem", f"Problem found: {len(bad_roads)} route(s) are not drivable "
                                      f"and {len(conflicts)} field(s) are double-booked.",
                     jobs=bad_roads, conflicts=conflicts)
            return
        self.say("routes", f"{len(stops)} route(s) set, {km:.1f} km of road travel. "
                           "No two tractors are in the same field at once.", km=round(km, 2))

    def _drivable(self, stop: Stop) -> bool:
        legs = _legs(stop.path)
        return (not any(leg in self.run.blocked for leg in legs)
                and all(math.isfinite(self.run.road_map.distance(*sorted(leg))) for leg in legs))


def field_conflicts(stops: list[Stop]) -> list[tuple[str, str]]:
    """Conflict checker: pairs of jobs that would have two tractors in one field at once."""
    conflicts = []
    by_field: dict[str, list[Stop]] = {}
    for stop in stops:
        by_field.setdefault(stop.field, []).append(stop)
    for visits in by_field.values():
        visits.sort(key=lambda stop: stop.start)
        for earlier, later in zip(visits, visits[1:]):
            if later.start < earlier.finish - 1e-6:
                conflicts.append((earlier.job, later.job))
    return conflicts


def _stops(plan: Plan | None) -> list[Stop]:
    return plan.stops() if plan else []


def _legs(path: list[str]) -> list[frozenset[str]]:
    return [frozenset(pair) for pair in zip(path, path[1:]) if pair[0] != pair[1]]
