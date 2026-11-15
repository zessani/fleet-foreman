"""Stand-in road map: straight-line distance, roads ignored.

Ahmed replaces the inside of this class with real shortest-path routing over
farm["roads"] and keeps the interface (CLAUDE.md section 8d). The planner only
ever calls the five methods below.
"""

import math


class RoadMap:
    def __init__(self, farm: dict) -> None:
        """Build from the farm file as a dict (CLAUDE.md section 8a)."""
        self._xy: dict[str, tuple[float, float]] = {
            node["id"]: (node["x"], node["y"]) for node in farm["nodes"]
        }
        self._blocked: set[frozenset[str]] = set()
        self._km: dict[tuple[str, str], float] = {}
        self._rebuild()

    def distance(self, a: str, b: str) -> float:
        """Distance from a to b in km. math.inf if there is no route."""
        return self._km[(a, b)]

    def path(self, a: str, b: str) -> list[str]:
        """Node ids from a to b, both included. Empty list if there is no route."""
        if math.isinf(self._km[(a, b)]):
            return []
        return [a] if a == b else [a, b]

    def block(self, a: str, b: str) -> None:
        """Close the road between two linked nodes."""
        self._blocked.add(frozenset((a, b)))
        self._rebuild()

    def open(self, a: str, b: str) -> None:
        """Reopen a closed road."""
        self._blocked.discard(frozenset((a, b)))
        self._rebuild()

    def _rebuild(self) -> None:
        # All pairs are worked out here, once, so distance() and path() stay
        # plain lookups. The stand-in ignores roads, so a blocked road is
        # recorded but changes nothing yet.
        self._km = {
            (a, b): math.dist(self._xy[a], self._xy[b])
            for a in self._xy
            for b in self._xy
        }
