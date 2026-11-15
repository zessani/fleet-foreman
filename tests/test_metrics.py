"""The numbers of CLAUDE.md section 13 mean what the brief says they mean."""

import copy
from pathlib import Path

from foreman.engine import Engine
from foreman.model import Farm, farm_from_dict, load_farm
from foreman.nav import RoadMap
from foreman.sim import Simulation

EXAMPLE = Path(__file__).resolve().parent.parent / "scenarios" / "example_farm.json"


def farm_with_only(job_id: str, **changes) -> Farm:
    raw = copy.deepcopy(load_farm(EXAMPLE).raw)
    raw["jobs"] = [dict(job, **changes) for job in raw["jobs"] if job["id"] == job_id]
    return farm_from_dict(raw)


def test_idle_counts_only_tractors_that_could_be_doing_something() -> None:
    farm = farm_with_only("J2")  # one tilling job; Bolt (T2) owns no disc
    sim = Simulation(farm, RoadMap(farm.raw))
    sim.advance(10)
    assert sim.tractors["T1"].minutes["idle"] == 10  # Atlas could till and has no task
    assert sim.tractors["T2"].minutes["idle"] == 0   # Bolt cannot till: not idle
    assert sim.tractors["T2"].minutes["unassigned"] == 10  # but it is unassigned


def test_a_job_that_is_not_released_yet_makes_nobody_idle() -> None:
    farm = farm_with_only("J2", release=60)
    sim = Simulation(farm, RoadMap(farm.raw))
    sim.advance(30)
    assert sim.tractors["T1"].minutes["idle"] == 0
    sim.advance(40)
    assert sim.tractors["T1"].minutes["idle"] == 10  # idle from minute 60 to 70


def test_an_old_plan_number_is_ignored() -> None:
    farm = load_farm(EXAMPLE)
    sim = Simulation(farm, RoadMap(farm.raw))
    stop = {"job": "J2", "field": "F4", "implement": "disc_6", "via_yard": False,
            "swap": False, "refuel": False, "path": ["YARD", "F4"]}
    atlas = sim.tractors["T1"]
    atlas.dispatch([stop], plan_no=2)
    atlas.dispatch([], plan_no=1)  # a late command from the plan before
    assert atlas.plan_no == 2
    sim.advance(10)
    assert atlas.status()["state"] == "working"


def test_report_leads_with_hidden_factors_off() -> None:
    report = Engine(EXAMPLE).report()
    assert report["main"]["hidden_factors"] is False
    assert report["with_hidden_factors"]["hidden_factors"] is True
    for table in (report["main"], report["with_hidden_factors"], report["live"]):
        assert {"foreman", "baseline", "difference"} <= table.keys()
        assert "service_min" in table["foreman"]
