import copy
from pathlib import Path

from foreman.baseline import plan_baseline
from foreman.model import farm_from_dict, load_farm
from foreman.nav import RoadMap
from foreman.state import WorldState

FARM_PATH = Path(__file__).resolve().parent.parent / "scenarios" / "example_farm.json"


def _run(farm, world=None):
    world = world or WorldState.start_of_day(farm)
    return plan_baseline(farm, RoadMap(farm.raw), world)


def test_every_job_planned_once_nothing_held():
    farm = load_farm(FARM_PATH)
    plan = _run(farm)
    planned = [job for jobs in plan.job_order().values() for job in jobs]
    assert sorted(planned) == sorted(farm.jobs)
    assert plan.held == {}


def test_after_job_starts_when_predecessor_finishes():
    plan = _run(load_farm(FARM_PATH))
    assert plan.stop_for("J3").start >= plan.stop_for("J2").finish


def test_no_overlap_within_a_field():
    plan = _run(load_farm(FARM_PATH))
    by_field = {}
    for stop in plan.stops():
        by_field.setdefault(stop.field, []).append(stop)
    for stops in by_field.values():
        stops.sort(key=lambda s: s.start)
        for earlier, later in zip(stops, stops[1:]):
            assert later.start >= earlier.finish - 1e-9


def test_deterministic():
    farm = load_farm(FARM_PATH)
    assert _run(farm).job_order() == _run(farm).job_order()


def test_job_without_implement_is_held():
    farm = load_farm(FARM_PATH)
    raw = copy.deepcopy(farm.raw)
    raw["jobs"].append({"id": "J9", "type": "harvest", "field": "F1", "priority": 3,
                        "deadline": 600, "release": 0, "after": None})
    farm = farm_from_dict(raw)
    plan = _run(farm)
    assert "J9" in plan.held
    planned = {job for jobs in plan.job_order().values() for job in jobs}
    assert planned == set(farm.jobs) - {"J9"}


def test_unavailable_tractor_gets_no_stops():
    farm = load_farm(FARM_PATH)
    world = WorldState.start_of_day(farm)
    world.tractors["T2"].available = False
    plan = _run(farm, world)
    assert not plan.routes.get("T2")
