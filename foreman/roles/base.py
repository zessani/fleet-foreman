"""What every agent shares: the run it works on, the memory, and one cycle of work."""

from __future__ import annotations

from dataclasses import dataclass

from foreman.memory import Memory
from foreman.planner import CHANGE_STRICT
from foreman.run import Run
from foreman.safety import SafetyReport
from foreman.state import Plan, WorldState


@dataclass
class Cycle:
    """One pass through the routing table: the events, and what each agent added."""

    events: list[dict]              # handled together; each has a "type"
    started: float                  # time.perf_counter() when the events reached the Orchestrator
    previous: Plan | None = None    # the plan being replaced
    world: WorldState | None = None
    plan: Plan | None = None
    safety: SafetyReport | None = None
    change_cost: float = CHANGE_STRICT
    from_scratch: bool = False      # second attempt after Safety rejected the first


class Role:
    name = "Role"

    def __init__(self, run: Run, memory: Memory) -> None:
        self.run = run
        self.farm = run.farm
        self.memory = memory

    def say(self, event: str, message: str, **data) -> None:
        """Write one line to the event log under this agent's name."""
        self.memory.log.append(self.run.now, self.name, event, message, **data)

    def job_label(self, job_id: str) -> str:
        job = self.farm.jobs[job_id]
        return f"{job.type.capitalize()} {self.farm.nodes[job.field].name}"

    def tractor_name(self, tractor_id: str) -> str:
        return self.farm.tractors[tractor_id].name
