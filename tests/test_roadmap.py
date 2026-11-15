"""Road routing behavior required by CLAUDE.md section 8d."""

import math

import pytest

from foreman.nav import RoadMap


def farm(*, roads: list[list[str]] | None = None) -> dict:
    data = {
        "nodes": [
            {"id": "A", "x": 0.0, "y": 0.0},
            {"id": "B", "x": 1.0, "y": 0.0},
            {"id": "C", "x": 2.0, "y": 0.0},
            {"id": "D", "x": 1.0, "y": 2.0},
        ]
    }
    if roads is not None:
        data["roads"] = roads
    return data


def test_routes_follow_roads_and_choose_the_shortest_path() -> None:
    road_map = RoadMap(
        farm(roads=[["A", "B"], ["B", "C"], ["A", "D"], ["D", "C"]])
    )

    assert road_map.path("A", "C") == ["A", "B", "C"]
    assert math.isclose(road_map.distance("A", "C"), 2.0)
    assert road_map.path("C", "A") == ["C", "B", "A"]
    assert road_map.distance("A", "A") == 0.0
    assert road_map.path("A", "A") == ["A"]


def test_block_rebuilds_routes_and_open_restores_them() -> None:
    road_map = RoadMap(
        farm(roads=[["A", "B"], ["B", "C"], ["A", "D"], ["D", "C"]])
    )

    road_map.block("B", "C")
    assert road_map.path("A", "C") == ["A", "D", "C"]

    road_map.open("B", "C")
    assert road_map.path("A", "C") == ["A", "B", "C"]


def test_disconnected_destination_has_no_path() -> None:
    road_map = RoadMap(farm(roads=[["A", "B"]]))

    assert math.isinf(road_map.distance("A", "C"))
    assert road_map.path("A", "C") == []


def test_missing_roads_creates_a_fully_connected_farm() -> None:
    road_map = RoadMap(farm())

    assert road_map.path("A", "C") == ["A", "C"]
    assert math.isclose(road_map.distance("A", "C"), 2.0)


def test_invalid_nodes_and_nonexistent_roads_fail_clearly() -> None:
    road_map = RoadMap(farm(roads=[["A", "B"]]))

    with pytest.raises(KeyError, match="unknown farm node"):
        road_map.distance("A", "MISSING")
    with pytest.raises(ValueError, match="not directly linked"):
        road_map.block("A", "C")


def test_invalid_farm_graph_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown node"):
        RoadMap(farm(roads=[["A", "MISSING"]]))
