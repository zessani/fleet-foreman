import copy
import dataclasses
from pathlib import Path

import pytest

from foreman.model import load_farm
from foreman.nav.roadmap import RoadMap
from foreman.planner import plan as make_plan
from foreman.safety import verify
from foreman.state import Plan, WorldState

FARM_FILE = Path(__file__).resolve().parent.parent / "scenarios" / "example_farm.json"


@pytest.fixture
def farm():
    return load_farm(FARM_FILE)


@pytest.fixture
def road_map(farm):
    return RoadMap(farm.raw)


@pytest.fixture
def world(farm):
    return WorldState.start_of_day(farm)


@pytest.fixture
def plan(farm, road_map, world):
    return make_plan(farm, road_map, world)


def tamper(plan: Plan, job: str, **changes) -> Plan:
    """A copy of the plan with one stop changed."""
    routes = {
        t: [dataclasses.replace(s, **changes) if s.job == job else s for s in route]
        for t, route in plan.routes.items()
    }
    return Plan(plan.created_at, routes, dict(plan.held))


def run(farm, road_map, world, plan, **kwargs):
    return verify(farm, road_map, world, plan, **kwargs)


def failed_ids(report):
    return [c.id for c in report.checks if not c.passed]


def test_untouched_plan_passes_everything(farm, road_map, world, plan):
    report = run(farm, road_map, world, plan)
    assert [c.id for c in report.checks] == list(range(1, 10))
    assert all(c.passed for c in report.checks), [c.detail for c in report.checks if not c.passed]
    assert report.passed
    assert report.summary() == "Safety gate passed 9 of 9 checks."


def test_rule_1_job_on_two_tractors(farm, road_map, world, plan):
    stop = plan.stop_for("J1")  # on T2
    routes = {t: list(r) for t, r in plan.routes.items()}
    routes["T3"].append(dataclasses.replace(stop, tractor="T3"))
    report = run(farm, road_map, world, Plan(0.0, routes, {}))
    assert not report.checks[0].passed
    assert not report.passed
    # The copy also trips rule 4 (same field, same time) and rule 8 (planned twice).


def test_rule_1_finished_job_rescheduled(farm, road_map, world, plan):
    world.jobs["J1"].status = "done"
    report = run(farm, road_map, world, plan)
    assert not report.checks[0].passed
    assert not report.passed


def test_rule_2_wrong_implement_type(farm, road_map, world, plan):
    broken = tamper(plan, "J5", implement="mower_4")  # T2 owns it, but J5 is a spray job
    report = run(farm, road_map, world, broken)
    assert not report.checks[1].passed
    assert not report.passed
    # The mower is slower or faster than the sprayer, so rule 8 (time needed) may also trip.


def test_rule_3_low_starting_fuel(farm, road_map, world, plan):
    world.tractors["T2"].fuel = 30.0  # reserve is 22 of 220
    report = run(farm, road_map, world, plan)
    assert not report.checks[2].passed
    assert not report.passed
    assert failed_ids(report) == [3]


def test_rule_4_same_field_overlap(farm, road_map, world, plan):
    j2 = plan.stop_for("J2")
    broken = tamper(plan, "J2", start=j2.start + 50, finish=j2.finish + 50)
    report = run(farm, road_map, world, broken)
    assert not report.checks[3].passed
    assert not report.passed
    # Pushing J2 into J3 also breaks the J2-before-J3 order, so rule 5 fails too.


def test_rule_5_after_starts_before_predecessor(farm, road_map, world, plan):
    jobs = dict(farm.jobs)
    jobs["J4"] = dataclasses.replace(jobs["J4"], after="J1")  # J4 starts ~31, J1 ends ~61
    other = copy.copy(farm)
    other.jobs = jobs
    report = run(other, road_map, world, plan)
    assert not report.checks[4].passed
    assert not report.passed
    assert failed_ids(report) == [5]


def test_rule_5_before_release(farm, road_map, world, plan):
    broken = tamper(plan, "J5", start=10.0)  # J5 is released at 60
    report = run(farm, road_map, world, broken)
    assert not report.checks[4].passed
    assert not report.passed


def test_rule_6_closed_road(farm, road_map, world, plan):
    used = next(stop.path for stop in plan.stops() if len(stop.path) > 1)
    report = run(farm, road_map, world, plan, blocked={frozenset(used[:2])})
    assert not report.checks[5].passed
    assert not report.passed
    assert failed_ids(report) == [6]


def test_rule_7_unavailable_tractor(farm, road_map, world, plan):
    world.tractors["T2"].available = False
    report = run(farm, road_map, world, plan)
    assert not report.checks[6].passed
    assert not report.passed
    assert failed_ids(report) == [7]


def test_rule_8_stop_deleted_without_hold(farm, road_map, world, plan):
    routes = {t: [s for s in r if s.job != "J5"] for t, r in plan.routes.items()}
    report = run(farm, road_map, world, Plan(0.0, routes, {}))
    assert not report.checks[7].passed
    assert not report.passed
    assert failed_ids(report) == [8]


def test_rule_8_stop_too_short(farm, road_map, world, plan):
    j1 = plan.stop_for("J1")
    broken = tamper(plan, "J1", finish=j1.start + 5)
    report = run(farm, road_map, world, broken)
    assert not report.checks[7].passed
    assert not report.passed


def test_rule_9_deadline_is_only_a_warning(farm, road_map, world, plan):
    broken = tamper(plan, "J1", finish=300.0)  # deadline is 240
    report = run(farm, road_map, world, broken)
    assert not report.checks[8].passed
    assert report.checks[8].severity == "warning"
    assert "J1" in report.checks[8].detail
    assert report.passed
    assert failed_ids(report) == [9]
    assert report.failures() == []
    assert report.summary() == "Safety gate passed 8 of 9 checks."


def test_to_dict_shape(farm, road_map, world, plan):
    data = run(farm, road_map, world, plan).to_dict()
    assert data["passed"] is True
    assert len(data["checks"]) == 9
    for check in data["checks"]:
        assert set(check) == {"id", "name", "passed", "severity", "detail"}
