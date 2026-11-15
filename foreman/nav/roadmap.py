"""Shortest-path routing over the farm road graph.

Routes are precomputed because the planner asks for the same distances many
times while evaluating assignments. Blocking or reopening a road rebuilds the
lookup tables; normal ``distance`` and ``path`` calls do no graph search.
"""

import heapq
import math


class RoadMap:
    def __init__(self, farm: dict) -> None:
        """Build from the farm file as a dict (CLAUDE.md section 8a)."""
        self._xy: dict[str, tuple[float, float]] = {
            node["id"]: (node["x"], node["y"]) for node in farm["nodes"]
        }
        if len(self._xy) != len(farm["nodes"]):
            raise ValueError("farm node ids must be unique")

        road_pairs = farm.get("roads")
        if road_pairs is None:
            ids = list(self._xy)
            road_pairs = [
                [a, b]
                for index, a in enumerate(ids)
                for b in ids[index + 1 :]
            ]

        self._roads: set[frozenset[str]] = set()
        for road in road_pairs:
            if len(road) != 2:
                raise ValueError(f"road must contain two node ids: {road!r}")
            a, b = road
            if a not in self._xy or b not in self._xy:
                raise ValueError(f"road references unknown node: {a!r}, {b!r}")
            if a == b:
                raise ValueError(f"road cannot connect a node to itself: {a!r}")
            self._roads.add(frozenset((a, b)))

        self._blocked: set[frozenset[str]] = set()
        self._km: dict[tuple[str, str], float] = {}
        self._paths: dict[tuple[str, str], list[str]] = {}
        self._rebuild()

    def distance(self, a: str, b: str) -> float:
        """Distance from a to b in km. math.inf if there is no route."""
        self._require_nodes(a, b)
        return self._km[(a, b)]

    def path(self, a: str, b: str) -> list[str]:
        """Node ids from a to b, both included. Empty list if there is no route."""
        self._require_nodes(a, b)
        return list(self._paths[(a, b)])

    def block(self, a: str, b: str) -> None:
        """Close the road between two linked nodes."""
        road = self._require_road(a, b)
        self._blocked.add(road)
        self._rebuild()

    def open(self, a: str, b: str) -> None:
        """Reopen a closed road."""
        road = self._require_road(a, b)
        self._blocked.discard(road)
        self._rebuild()

    def _rebuild(self) -> None:
        adjacency: dict[str, list[tuple[str, float]]] = {
            node_id: [] for node_id in self._xy
        }
        for road in self._roads - self._blocked:
            a, b = tuple(road)
            distance = math.dist(self._xy[a], self._xy[b])
            adjacency[a].append((b, distance))
            adjacency[b].append((a, distance))
        for neighbors in adjacency.values():
            neighbors.sort()

        self._km = {}
        self._paths = {}
        for source in self._xy:
            distances, previous = self._shortest_paths(source, adjacency)
            for destination in self._xy:
                key = (source, destination)
                self._km[key] = distances[destination]
                self._paths[key] = self._build_path(
                    source, destination, distances, previous
                )

    def _shortest_paths(
        self,
        source: str,
        adjacency: dict[str, list[tuple[str, float]]],
    ) -> tuple[dict[str, float], dict[str, str]]:
        distances = {node_id: math.inf for node_id in self._xy}
        distances[source] = 0.0
        previous: dict[str, str] = {}
        queue: list[tuple[float, str]] = [(0.0, source)]

        while queue:
            distance, node_id = heapq.heappop(queue)
            if distance > distances[node_id]:
                continue
            for neighbor, road_km in adjacency[node_id]:
                candidate = distance + road_km
                if candidate < distances[neighbor]:
                    distances[neighbor] = candidate
                    previous[neighbor] = node_id
                    heapq.heappush(queue, (candidate, neighbor))

        return distances, previous

    @staticmethod
    def _build_path(
        source: str,
        destination: str,
        distances: dict[str, float],
        previous: dict[str, str],
    ) -> list[str]:
        if math.isinf(distances[destination]):
            return []
        path = [destination]
        while path[-1] != source:
            path.append(previous[path[-1]])
        path.reverse()
        return path

    def _require_nodes(self, a: str, b: str) -> None:
        for node_id in (a, b):
            if node_id not in self._xy:
                raise KeyError(f"unknown farm node: {node_id}")

    def _require_road(self, a: str, b: str) -> frozenset[str]:
        self._require_nodes(a, b)
        road = frozenset((a, b))
        if road not in self._roads:
            raise ValueError(f"nodes are not directly linked by a road: {a}, {b}")
        return road
