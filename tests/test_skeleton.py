"""The shared formats of CLAUDE.md section 8 are all present."""

import json
import math
from pathlib import Path

from foreman.engine import Engine
from foreman.nav import RoadMap

EXAMPLE = Path(__file__).resolve().parent.parent / "scenarios" / "example_farm.json"


def load_example() -> dict:
    with open(EXAMPLE, encoding="utf-8") as farm_file:
        return json.load(farm_file)


def test_state_has_every_part_of_section_8c() -> None:
    state = Engine(EXAMPLE).state()
    assert {"now", "clock", "running", "speed", "farm", "foreman", "baseline"} <= state.keys()
    assert state["clock"] == "06:00"
    assert {"name", "nodes", "roads", "blocked"} <= state["farm"].keys()

    run_keys = {"tractors", "jobs", "plan", "totals"}
    totals_keys = {"jobs_done", "on_time", "travel_min", "idle_min", "unassigned_min",
                   "service_min", "wasted_min", "finish_min", "working_share", "km", "violations"}
    tractor_keys = {"id", "name", "x", "y", "status", "job", "fuel_pct", "mounted", "color"}
    job_keys = {"id", "type", "field", "priority", "deadline", "pct", "status", "tractor"}
    plan_keys = {"job", "tractor", "implement", "start", "finish", "late_min", "path"}
    for run in (state["foreman"], state["baseline"]):
        assert run_keys <= run.keys()
        assert totals_keys <= run["totals"].keys()
        assert all(tractor_keys <= row.keys() for row in run["tractors"])
        assert all(job_keys <= row.keys() for row in run["jobs"])
        assert run["plan"] and all(plan_keys <= row.keys() for row in run["plan"])

    foreman = state["foreman"]
    assert {"headline", "summary", "changes", "ms"} <= foreman["explanation"].keys()
    assert {"passed", "checks"} <= foreman["safety"].keys()
    assert all({"t", "clock", "agent", "event", "message"} <= line.keys() for line in foreman["log"])


def test_example_farm_uses_every_field_of_section_8a() -> None:
    farm = load_example()
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
    road_map = RoadMap(load_example())
    assert road_map.distance("YARD", "YARD") == 0.0
    assert road_map.path("YARD", "YARD") == ["YARD"]
    assert math.isclose(road_map.distance("YARD", "F4"), road_map.distance("F4", "YARD"))
    path = road_map.path("YARD", "F1")
    assert path[0] == "YARD" and path[-1] == "F1"
    road_map.block("YARD", "X1")
    road_map.open("YARD", "X1")
    assert road_map.distance("YARD", "F1") > 0
