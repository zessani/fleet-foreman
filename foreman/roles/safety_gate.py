"""Safety gate: passes or rejects every plan. It stands in for human approval.

Tool: the rule verifier (foreman.safety.verify), which re-derives every rule
of CLAUDE.md section 12 from the plan and the world state.
"""

from __future__ import annotations

from foreman.roles.base import Cycle, Role
from foreman.safety import verify


class SafetyGate(Role):
    name = "Safety gate"

    def check(self, cycle: Cycle) -> None:
        cycle.safety = verify(self.farm, self.run.road_map, cycle.world, cycle.plan,
                              blocked=self.run.blocked, speed_factor=self.run.speed_factor)
        if cycle.safety.passed:
            warnings = [check for check in cycle.safety.checks if not check.passed]
            note = f" Warning: {warnings[0].detail}" if warnings else ""
            self.say("plan_passed", cycle.safety.summary() + note)
            return
        for check in cycle.safety.failures():
            self.say("plan_rejected", f"Rule {check.id} failed ({check.name}): {check.detail}",
                     rule=check.id)
