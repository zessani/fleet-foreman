"""Phase 0 checks: the shared formats in CLAUDE.md section 8 are all present."""

import math

from foreman.fake_state import fake_state, load_farm
from foreman.nav import RoadMap


def test_fake_state_has_every_part_of_section_8c() -> None:
    state = fake_state()
    assert {"now", "clock", "running", "speed", "farm", "foreman", "baseline"} <= state.keys()
    assert state["clock"] == "09:35"
    assert {"name", "nodes", "roads", "blocked"} <= state["farm"].keys()

    run_keys = {"tractors", "jobs", "plan", "totals"}
    totals_keys = {"jobs_done", "on_time", "travel_min", "idle_min",
                   "working_share", "km", "violations"}
    for run in (state["foreman"], state["baseline"]):
        assert run_keys <= run.keys()
        assert all(run[key] for key in run_keys), "every block must have data"
        assert totals_keys <= run["totals"].keys()

    foreman = state["foreman"]
    assert {"headline", "summary", "changes", "ms"} <= foreman["explanation"].keys()
    assert len(foreman["safety"]["checks"]) == 9
    assert foreman["log"]


def test_example_farm_uses_every_field_of_section_8a() -> None:
    farm = load_farm()
    assert {"name", "day_start_min", "horizon_min", "nodes", "roads", "implements",
            "tractors", "jobs", "truth", "events"} <= farm.keys()

    kinds = [node["kind"] for node in farm["nodes"]]
    assert kinds.count("depot") == 1
    assert "junction" in kinds and "field" in kinds
    assert any(job["after"] for job in farm["jobs"])

    node_ids = {node["id"] for node in farm["nodes"]}
    implement_ids = {implement["id"] for implement in farm["implements"]}
    assert all(a in node_ids and b in node_ids for a, b in farm["roads"])
    assert all(job["field"] in node_ids for job in farm["jobs"])
    for tractor in farm["tractors"]:
        assert set(tractor["implements"]) <= implement_ids
        assert tractor["mounted"] in tractor["implements"]


def test_road_map_interface_of_section_8d() -> None:
    road_map = RoadMap(load_farm())
    assert road_map.distance("YARD", "YARD") == 0.0
    assert road_map.path("YARD", "YARD") == ["YARD"]
    assert math.isclose(road_map.distance("YARD", "F4"), road_map.distance("F4", "YARD"))
    path = road_map.path("YARD", "F1")
    assert path[0] == "YARD" and path[-1] == "F1"
    road_map.block("YARD", "X1")
    road_map.open("YARD", "X1")
    assert road_map.distance("YARD", "F1") > 0
