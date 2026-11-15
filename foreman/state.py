"""World state and plan types shared by the planner, baseline, and simulator."""

from __future__ import annotations

from dataclasses import dataclass, field

from foreman.model import Farm

JOB_STATUSES = ("pending", "active", "done", "held")


@dataclass
class TractorState:
    """A tractor as the planner sees it: where and when it is next free."""

    id: str
    node: str            # the node it is at, or will be at when free
    free_at: float       # minute it can take new work
    fuel: float          # fuel at that moment
    mounted: str         # implement hitched at that moment
    available: bool = True         # False while broken down
    busy_job: str | None = None    # work in progress; stays with this tractor


@dataclass
class JobState:
    id: str
    remaining_ha: float
    status: str = "pending"
    done_at: float | None = None
    tractor: str | None = None     # the tractor working it, or that finished it
    started_at: float | None = None


@dataclass
class WorldState:
    now: float
    tractors: dict[str, TractorState]
    jobs: dict[str, JobState]

    @classmethod
    def start_of_day(cls, farm: Farm) -> "WorldState":
        return cls(
            now=0.0,
            tractors={
                t.id: TractorState(t.id, t.node, 0.0, t.fuel, t.mounted)
                for t in farm.tractors.values()
            },
            jobs={j.id: JobState(j.id, j.area_ha) for j in farm.jobs.values()},
        )


@dataclass
class Stop:
    """One planned job visit, with the timing the evaluator worked out."""

    job: str
    tractor: str
    field: str
    implement: str
    via_yard: bool
    swap: bool
    refuel: bool
    path: list[str]
    depart: float       # leaves its previous position
    arrive: float       # reaches the field
    start: float        # work begins (after any wait)
    finish: float
    late_min: float
    km: float
    travel_min: float
    service_min: float  # swap and refuel time at the yard
    wait_min: float     # waiting at the field
    fuel_after: float

    def to_dispatch(self) -> dict:
        """The stop format of TractorAdapter.dispatch (CLAUDE.md section 8b)."""
        return {
            "job": self.job, "field": self.field, "implement": self.implement,
            "via_yard": self.via_yard, "swap": self.swap, "refuel": self.refuel,
            "path": list(self.path),
        }


@dataclass
class Plan:
    created_at: float
    routes: dict[str, list[Stop]]                        # tractor id -> stops in order
    held: dict[str, str] = field(default_factory=dict)   # job id -> why it is not planned
    cost: float = 0.0
    ms: float = 0.0                                      # time taken to build the plan

    def stops(self) -> list[Stop]:
        every = [stop for route in self.routes.values() for stop in route]
        return sorted(every, key=lambda stop: (stop.start, stop.tractor))

    def stop_for(self, job_id: str) -> Stop | None:
        for route in self.routes.values():
            for stop in route:
                if stop.job == job_id:
                    return stop
        return None

    def job_order(self) -> dict[str, list[str]]:
        return {tractor: [stop.job for stop in route] for tractor, route in self.routes.items()}
