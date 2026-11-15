"""Planner checks: complete, valid, deterministic, fast, and true to the simulator."""

import copy
from pathlib import Path

import pytest

from foreman.model import Farm, farm_from_dict, load_farm
from foreman.nav import RoadMap
from foreman.planner import plan
from foreman.sim import Simulation
from foreman.state import Plan, WorldState

EXAMPLE = Path(__file__).resolve().parent.parent / "scenarios" / "example_farm.json"


@pytest.fixture
def farm() -> Farm:
    return load_farm(EXAMPLE)


def make_plan(farm: Farm) -> Plan:
    return plan(farm, RoadMap(farm.raw), WorldState.start_of_day(farm))


def test_every_job_is_planned_exactly_once(farm: Farm) -> None:
    result = make_plan(farm)
    planned = [stop.job for stop in result.stops()]
    assert sorted(planned) == sorted(farm.jobs)
    assert result.held == {}


def test_plan_respects_order_and_one_machine_per_field(farm: Farm) -> None:
    stops = make_plan(farm).stops()
    finish = {stop.job: stop.finish for stop in stops}
    for stop in stops:
        job = farm.jobs[stop.job]
        assert stop.start >= job.release
        if job.after:
            assert stop.start >= finish[job.after]
    for a in stops:
        for b in stops:
            if a.job < b.job and a.field == b.field:
                assert a.finish <= b.start or b.finish <= a.start


def test_plan_is_deterministic(farm: Farm) -> None:
    assert make_plan(farm).job_order() == make_plan(farm).job_order()


def test_plan_is_fast(farm: Farm) -> None:
    assert make_plan(farm).ms < 1000


def test_job_no_tractor_can_do_is_held_with_a_reason(farm: Farm) -> None:
    raw = copy.deepcopy(farm.raw)
    raw["jobs"].append({"id": "J9", "type": "harvest", "field": "F1", "priority": 3,
                        "deadline": 600, "release": 0, "after": None})
    result = make_plan(farm_from_dict(raw))
    assert "harvest" in result.held["J9"]
    assert len(result.stops()) == len(farm.jobs)


def test_broken_tractor_gets_no_work(farm: Farm) -> None:
    world = WorldState.start_of_day(farm)
    world.tractors["T2"].available = False
    result = plan(farm, RoadMap(farm.raw), world)
    assert "T2" not in result.routes
    assert all(stop.tractor != "T2" for stop in result.stops())


def test_previous_plan_is_accepted(farm: Farm) -> None:
    first = make_plan(farm)
    again = plan(farm, RoadMap(farm.raw), WorldState.start_of_day(farm), previous=first)
    assert again.job_order() == first.job_order()


def test_simulated_finish_times_match_the_plan_within_one_minute(farm: Farm) -> None:
    """With no hidden speed factors, the day must run the way the planner predicted."""
    road_map = RoadMap(farm.raw)
    sim = Simulation(farm, road_map, use_truth=False)
    result = plan(farm, road_map, sim.world_state())
    for tractor_id, stops in result.routes.items():
        sim.tractors[tractor_id].dispatch([stop.to_dispatch() for stop in stops])
    sim.advance(farm.horizon_min)

    assert sim.all_done()
    assert sim.violations == []
    for stop in result.stops():
        assert sim.jobs[stop.job].done_at == pytest.approx(stop.finish, abs=1.0)
        assert sim.jobs[stop.job].started_at == pytest.approx(stop.start, abs=1.0)
