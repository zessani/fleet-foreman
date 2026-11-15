"""The tractor interface (CLAUDE.md section 8b).

Every tractor sits behind these three calls. The simulator implements it now.
An AgOpenGPS tractor or a real ATC tractor can implement it later.
"""

from typing import Protocol

# Values of the "state" key returned by TractorAdapter.status().
TRACTOR_STATES = ("idle", "traveling", "servicing", "working", "waiting", "broken")


class TractorAdapter(Protocol):
    def status(self) -> dict:
        """Return {id, x, y, node, heading, state, job, pct, fuel, mounted}."""
        ...

    def dispatch(self, stops: list[dict], plan_no: int) -> None:
        """Replace the tractor's to-do list. Work in progress is not interrupted.

        Each stop is {job, field, implement, via_yard, swap, refuel, path},
        where path is a list of node ids. plan_no counts up with every plan;
        a command with a number lower than one already accepted is ignored.
        """
        ...

    def stop(self) -> None:
        """Halt the tractor where it is."""
        ...
