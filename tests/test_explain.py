"""Explanation checks: every sentence comes from plan data."""

import copy
import dataclasses
from pathlib import Path

import pytest

from foreman.explain import explain
from foreman.model import Farm, load_farm
from foreman.nav import RoadMap
from foreman.planner import plan
from foreman.state import Plan, WorldState

EXAMPLE = Path(__file__).resolve().parent.parent / "scenarios" / "example_farm.json"
KEYS = {"headline", "summary", "changes", "ms", "safety", "moved", "retimed", "unchanged", "held"}


@pytest.fixture
def farm() -> Farm:
    return load_farm(EXAMPLE)


@pytest.fixture
def first(farm: Farm) -> Plan:
    return plan(farm, RoadMap(farm.raw), WorldState.start_of_day(farm))


def broken_world(farm: Farm, first: Plan) -> tuple[WorldState, Plan, str]:
    world = WorldState.start_of_day(farm)
    world.now = 100
    victim = next(tractor for tractor, route in first.routes.items() if route)
    world.tractors[victim].available = False
    second = plan(farm, RoadMap(farm.raw), world, previous=first)
    return world, second, victim


def test_first_plan(farm: Farm, first: Plan) -> None:
    world = WorldState.start_of_day(farm)
    result = explain(farm, world, None, first, {"type": "jobs_submitted"})
    assert result["headline"].startswith("Day plan ready at 06:00")
    assert len(result["changes"]) == len(first.stops())
    assert "5 jobs planned across 3 tractors." in result["summary"]
    assert result["unchanged"] == 0 and result["moved"] == 0 and result["retimed"] == 0
    assert "18 m boom sprayer" in " ".join(result["changes"])


def test_breakdown(farm: Farm, first: Plan) -> None:
    world, second, victim = broken_world(farm, first)
    trigger = {"type": "breakdown", "tractor": victim, "job": None, "state": "traveling"}
    result = explain(farm, world, first, second, trigger)
    name = farm.tractors[victim].name
    assert result["headline"] == f"{name} went down at 07:40 while traveling."
    changed = [j for j in farm.jobs
               if first.stop_for(j) and second.stop_for(j)
               and first.stop_for(j).tractor != second.stop_for(j).tractor]
    for job_id in changed:
        label = f"{farm.jobs[job_id].type.capitalize()} {farm.nodes[farm.jobs[job_id].field].name}:"
        lines = [c for c in result["changes"] if c.startswith(label)]
        assert len(lines) == 1 and "moves from" in lines[0]
    total = result["moved"] + result["retimed"] + result["unchanged"] + result["held"]
    assert total == len(farm.jobs)


def test_breakdown_while_working_half_done(farm: Farm, first: Plan) -> None:
    world, second, victim = broken_world(farm, first)
    job_id = next(s.job for s in first.routes[victim])
    world.jobs[job_id].remaining_ha = farm.jobs[job_id].area_ha / 2
    trigger = {"type": "breakdown", "tractor": victim, "job": job_id, "state": "working"}
    new = copy.deepcopy(second)
    moved_to = next(t for t in farm.tractors if t != victim)
    stop = dataclasses.replace(new.stop_for(job_id), tractor=moved_to)
    for route in new.routes.values():
        route[:] = [s for s in route if s.job != job_id]
    new.routes[moved_to].append(stop)
    old = copy.deepcopy(first)
    old_stop = dataclasses.replace(old.stop_for(job_id), tractor=victim)
    for route in old.routes.values():
        route[:] = [s for s in route if s.job != job_id]
    old.routes[victim].append(old_stop)
    result = explain(farm, world, old, new, trigger)
    assert "(50% done)" in result["headline"]
    assert any("the remaining 50%" in c for c in result["changes"])


def test_held_job(farm: Farm, first: Plan) -> None:
    world = WorldState.start_of_day(farm)
    new = copy.deepcopy(first)
    job_id = next(iter(farm.jobs))
    for route in new.routes.values():
        route[:] = [s for s in route if s.job != job_id]
    new.held[job_id] = "no tractor can pull the right implement"
    result = explain(farm, world, first, new, {"type": "job_added", "job": job_id})
    assert result["held"] == 1
    assert any("is held: no tractor can pull the right implement." in c for c in result["changes"])
    assert "1 job is held." in result["summary"]


def test_late_job(farm: Farm, first: Plan) -> None:
    world = WorldState.start_of_day(farm)
    new = copy.deepcopy(first)
    route = next(r for r in new.routes.values() if r)
    deadline = farm.jobs[route[0].job].deadline
    route[0] = dataclasses.replace(route[0], finish=deadline + 25, late_min=25)
    result = explain(farm, world, first, new, {"type": "drift", "job": route[0].job, "behind_min": 25})
    assert any("25 min past its" in c for c in result["changes"])
    assert "1 job will finish late." in result["summary"]
    assert "behind plan" in result["headline"]


def test_singular_and_plural(farm: Farm, first: Plan) -> None:
    world = WorldState.start_of_day(farm)
    one = copy.deepcopy(first)
    one.routes = {t: r for t, r in one.routes.items() if r}
    keep = next(iter(one.routes))
    dropped = [s.job for t, r in one.routes.items() if t != keep for s in r]
    one.routes = {keep: one.routes[keep][:1]}
    result = explain(farm, world, None, one, {"type": "jobs_submitted"})
    assert "1 job planned across 1 tractor." in result["summary"]
    both = copy.deepcopy(first)
    for index, route in enumerate(r for r in both.routes.values() if r):
        if index < 2:
            route[0] = dataclasses.replace(route[0], late_min=5)
    assert "2 jobs will finish late." in explain(farm, world, first, both, {"type": "jobs_submitted"})["summary"]
    assert dropped is not None


def test_keys_and_rounding(farm: Farm, first: Plan) -> None:
    world = WorldState.start_of_day(farm)
    result = explain(farm, world, first, first, {"type": "repaired", "tractor": "T2"},
                     safety_line="Safety Agent passed 9 of 9 checks.", ms=12.3456)
    assert set(result) == KEYS
    assert result["ms"] == 12.3
    assert result["safety"] == "Safety Agent passed 9 of 9 checks."
    assert result["headline"] == "Bolt is back in service at 06:00."
    assert result["unchanged"] == len(farm.jobs)
    assert result["summary"].startswith("0 jobs moved, 0 retimed, 5 unchanged.")
