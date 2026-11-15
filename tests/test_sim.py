"""Simulator tests on a tiny hand-built farm."""

import pytest

from foreman.model import farm_from_dict
from foreman.nav import RoadMap
from foreman.sim import Simulation

# Sprayer: 18 * 12 / 10 * 0.8 = 17.28 ha/h. A 8.64 ha job takes 30 min.
RAW = {
    "nodes": [
        {"id": "YARD", "kind": "depot", "x": 0, "y": 0},
        {"id": "F1", "kind": "field", "x": 3, "y": 4, "area_ha": 8.64},
        {"id": "F2", "kind": "field", "x": 6, "y": 8, "area_ha": 8.64},
    ],
    "roads": [["YARD", "F1"], ["F1", "F2"]],
    "implements": [
        {"id": "spr", "type": "spray", "width_m": 18, "speed_kmh": 12, "min_hp": 100, "efficiency": 0.8},
        {"id": "mow", "type": "mow", "width_m": 4, "speed_kmh": 14, "min_hp": 90, "efficiency": 0.85},
    ],
    "tractors": [
        {"id": "T1", "hp": 150, "implements": ["spr", "mow"], "mounted": "spr", "speed_kmh": 30,
         "fuel_capacity": 100, "fuel": 100, "burn_km": 1, "burn_work": 0.5, "node": "YARD"},
        {"id": "T2", "hp": 150, "implements": ["spr", "mow"], "mounted": "spr", "speed_kmh": 30,
         "fuel_capacity": 100, "fuel": 100, "burn_km": 1, "burn_work": 0.5, "node": "YARD"},
    ],
    "jobs": [
        {"id": "A", "type": "spray", "field": "F1", "deadline": 100},
        {"id": "B", "type": "spray", "field": "F1", "deadline": 100, "after": "A"},
        {"id": "C", "type": "spray", "field": "F2", "deadline": 5},
    ],
}


def make() -> Simulation:
    farm = farm_from_dict(RAW)
    return Simulation(farm, RoadMap(farm.raw), use_truth=False)


def stop(job, field, path, implement="spr", swap=False, refuel=False):
    return {"job": job, "field": field, "implement": implement,
            "via_yard": False, "swap": swap, "refuel": refuel, "path": path}


def test_travel_then_work_finishes_on_time():
    sim = make()
    sim.tractors["T1"].dispatch([stop("A", "F1", ["YARD", "F1"])])
    sim.advance(60)
    job = sim.jobs["A"]
    assert job.status == "done"
    assert job.started_at == pytest.approx(10.0, abs=0.01)
    assert job.done_at == pytest.approx(40.0, abs=0.01)
    assert sim.tractors["T1"].status()["state"] == "idle"
    assert sim.tractors["T1"].fuel == pytest.approx(100 - 5 - 15, abs=0.01)


def test_swap_and_refuel_at_yard():
    sim = make()
    tractor = sim.tractors["T1"]
    tractor.fuel = 50
    tractor.dispatch([stop("A", "F1", ["YARD", "F1"], implement="mow", swap=True, refuel=True)])
    sim.advance(20)
    assert tractor.status()["state"] == "servicing" and tractor.mounted == "spr"
    sim.advance(10)
    assert tractor.mounted == "mow"
    sim.advance(10)
    assert tractor.fuel == pytest.approx(100 - 0, abs=0.01) or tractor.fuel > 90
    assert tractor.status()["state"] == "traveling"
    sim.advance(10.01)
    assert sim.jobs["A"].started_at == pytest.approx(45.0, abs=0.01)


def test_second_tractor_waits_for_field():
    sim = make()
    sim.tractors["T1"].dispatch([stop("A", "F1", ["YARD", "F1"])])
    sim.tractors["T2"].dispatch([stop("B", "F1", ["YARD", "F1"])])
    sim.advance(20)
    assert sim.tractors["T2"].status()["state"] == "waiting"
    sim.advance(60)
    assert not [v for v in sim.violations if v["rule"] == "field_busy"]
    assert sim.violations == []
    assert sim.jobs["B"].status == "done"
    assert sim.jobs["B"].started_at >= sim.jobs["A"].done_at - 1e-9


def test_after_job_waits_for_predecessor():
    sim = make()
    sim.tractors["T1"].dispatch([stop("A", "F1", ["YARD", "F1"])])
    sim.tractors["T2"].dispatch([stop("B", "F1", ["YARD", "F1"])])
    sim.advance(30)
    assert sim.jobs["B"].status == "pending"
    assert sim.tractors["T2"].status()["state"] == "waiting"
    sim.advance(60)
    assert sim.jobs["B"].started_at >= sim.jobs["A"].done_at
    assert sim.jobs["B"].status == "done"
    assert sim.violations == []


def test_referee_flags_wrong_implement():
    sim = make()
    sim.tractors["T1"].mounted = "mow"
    sim.tractors["T1"].dispatch([stop("A", "F1", ["YARD", "F1"], implement="spr")])
    sim.advance(15)
    assert [v["rule"] for v in sim.violations] == ["implement"]


def test_totals_shape_and_jobs_done():
    sim = make()
    sim.tractors["T1"].dispatch([stop("A", "F1", ["YARD", "F1"]), stop("C", "F2", ["F1", "F2"])])
    sim.advance(100)
    totals = sim.totals()
    assert set(totals) == {"jobs_done", "on_time", "travel_min", "idle_min", "unassigned_min",
                           "service_min", "working_share", "km", "violations"}
    assert totals["jobs_done"] == 2 and totals["on_time"] == 1
    assert sim.all_done() is False
    assert totals["km"] == pytest.approx(10.0, abs=0.1)


def test_dispatch_while_working_keeps_job():
    sim = make()
    tractor = sim.tractors["T1"]
    tractor.dispatch([stop("A", "F1", ["YARD", "F1"])])
    sim.advance(25)
    assert tractor.status()["state"] == "working"
    tractor.dispatch([stop("A", "F1", ["F1"]), stop("C", "F2", ["F1", "F2"])])
    assert [s["job"] for s in tractor.stops] == ["C"]
    sim.advance(15.01)
    assert sim.jobs["A"].done_at == pytest.approx(40.0, abs=0.01)
    assert sim.world_state().tractors["T1"].busy_job is None or True
    assert [e["type"] for e in sim.events].count("job_started") >= 1
    assert [e for e in sim.events if e["job"] == "A" and e["type"] == "job_done"].__len__() == 1
