"""'Why?' checks: the chosen tractor matches the plan and every reason is real."""

import copy
import re
from pathlib import Path

import pytest

from foreman.model import Farm, farm_from_dict, load_farm
from foreman.nav import RoadMap
from foreman.planner import plan
from foreman.state import Plan, WorldState
from foreman.why import why

EXAMPLE = Path(__file__).resolve().parent.parent / "scenarios" / "example_farm.json"
KEYS = {"job", "label", "status", "chosen", "held_reason", "others", "text"}
CHOSEN_KEYS = {"tractor", "name", "implement", "implement_name", "start", "finish",
               "deadline", "margin_min"}


@pytest.fixture
def farm() -> Farm:
    return load_farm(EXAMPLE)


@pytest.fixture
def world(farm: Farm) -> WorldState:
    return WorldState.start_of_day(farm)


@pytest.fixture
def first(farm: Farm, world: WorldState) -> Plan:
    return plan(farm, RoadMap(farm.raw), world)


def reason_for(result: dict, tractor_id: str) -> str:
    return next(other["reason"] for other in result["others"] if other["tractor"] == tractor_id)


def check_shape(farm: Farm, result: dict) -> None:
    assert set(result) == KEYS
    assert len(result["text"]) == 1 + len(result["others"])
    for other in result["others"]:
        assert set(other) == {"tractor", "name", "reason"}
        assert isinstance(other["reason"], str) and other["reason"].strip()
        assert other["name"] == farm.tractors[other["tractor"]].name
    for sentence in result["text"]:
        assert sentence.strip().endswith(".")


def test_chosen_matches_the_plan(farm: Farm, world: WorldState, first: Plan) -> None:
    road_map = RoadMap(farm.raw)
    for job_id in farm.jobs:
        result = why(farm, road_map, world, first, job_id)
        check_shape(farm, result)
        stop = first.stop_for(job_id)
        chosen = result["chosen"]
        assert set(chosen) == CHOSEN_KEYS
        assert chosen["tractor"] == stop.tractor
        assert chosen["implement"] == stop.implement
        assert chosen["finish"] == pytest.approx(stop.finish, abs=0.05)
        assert chosen["margin_min"] == round(farm.jobs[job_id].deadline - stop.finish)
        assert result["held_reason"] is None
        # Every tractor but the chosen one is explained, once.
        assert sorted(o["tractor"] for o in result["others"]) == sorted(
            t for t in farm.tractors if t != stop.tractor)
        assert chosen["name"] in result["text"][0]


def test_label_and_clock_times(farm: Farm, world: WorldState, first: Plan) -> None:
    result = why(farm, RoadMap(farm.raw), world, first, "J5")
    assert result["label"] == "Spray East Orchard"
    assert result["text"][0].startswith("Spray East Orchard")
    assert re.search(r"\d\d:\d\d to \d\d:\d\d", result["text"][0])


def test_tractor_without_the_implement(farm: Farm, world: WorldState, first: Plan) -> None:
    result = why(farm, RoadMap(farm.raw), world, first, "J2")   # till; Bolt has no disc
    assert reason_for(result, "T2") == "has no till implement"
    assert "Bolt has no till implement." in result["text"]


def test_tractor_without_the_power(farm: Farm) -> None:
    raw = copy.deepcopy(farm.raw)
    next(t for t in raw["tractors"] if t["id"] == "T2")["implements"].append("disc_6")
    weak = farm_from_dict(raw)   # Bolt has 120 hp, the disc needs 160
    world = WorldState.start_of_day(weak)
    road_map = RoadMap(weak.raw)
    result = why(weak, road_map, world, plan(weak, road_map, world), "J2")
    assert reason_for(result, "T2") == "has 120 hp and its till implement needs 160"


def test_out_of_service_tractor(farm: Farm, world: WorldState) -> None:
    world.tractors["T1"].available = False
    road_map = RoadMap(farm.raw)
    result = why(farm, road_map, world, plan(farm, road_map, world), "J2")
    assert result["chosen"]["tractor"] == "T3"
    assert reason_for(result, "T1") == "is out of service"
    check_shape(farm, result)


def test_capable_but_worse_tractor_gets_real_numbers(farm: Farm, world: WorldState,
                                                     first: Plan) -> None:
    road_map = RoadMap(farm.raw)
    result = why(farm, road_map, world, first, "J1")   # spray; Bolt chosen, Cedar could
    assert result["chosen"]["tractor"] == "T2"
    reason = reason_for(result, "T3")
    match = re.fullmatch(r"would finish (\d\d):(\d\d), (\d+) min later than Bolt", reason)
    assert match, reason

    # The numbers are the real ones: put the job on Cedar and time it.
    from foreman.evaluator import Evaluator
    evaluator = Evaluator(farm, road_map, world)
    order = first.job_order()
    order["T2"].remove("J1")
    finishes = []
    for position in range(len(order["T3"]) + 1):
        trial = dict(order)
        trial["T3"] = order["T3"][:position] + ["J1"] + order["T3"][position:]
        schedule = evaluator.schedule(trial)
        if schedule:
            finishes.append((schedule.cost, next(s.finish for s in schedule.routes["T3"]
                                                 if s.job == "J1")))
    cedar_finish = min(finishes)[1]
    assert int(match.group(3)) == round(cedar_finish - first.stop_for("J1").finish)
    shown = int(match.group(1)) * 60 + int(match.group(2))
    assert shown == int(farm.day_start_min + cedar_finish)


def test_reason_is_never_vague_on_the_example_farm(farm: Farm, world: WorldState,
                                                   first: Plan) -> None:
    road_map = RoadMap(farm.raw)
    for job_id in farm.jobs:
        for other in why(farm, road_map, world, first, job_id)["others"]:
            assert other["reason"].startswith(("has ", "would finish", "would add",
                                               "would delay", "would make")), other


def test_past_the_deadline_reason(farm: Farm) -> None:
    raw = copy.deepcopy(farm.raw)
    next(j for j in raw["jobs"] if j["id"] == "J1")["deadline"] = 70   # Bolt finishes at 61
    tight = farm_from_dict(raw)
    world = WorldState.start_of_day(tight)
    road_map = RoadMap(tight.raw)
    result = why(tight, road_map, world, plan(tight, road_map, world), "J1")
    assert result["chosen"]["tractor"] == "T2"
    assert re.fullmatch(r"would finish \d\d:\d\d, \d+ min past the deadline",
                        reason_for(result, "T3"))


def test_held_job_out_of_service(farm: Farm, world: WorldState) -> None:
    world.tractors["T1"].available = False
    world.tractors["T3"].available = False   # nobody left who can till
    road_map = RoadMap(farm.raw)
    replan = plan(farm, road_map, world)
    assert "J2" in replan.held
    result = why(farm, road_map, world, replan, "J2")
    check_shape(farm, result)
    assert result["status"] == "held"
    assert result["chosen"] is None
    assert result["held_reason"] == replan.held["J2"]
    assert result["text"][0].startswith("Till West Bench is held: ")
    assert len(result["others"]) == len(farm.tractors)
    assert reason_for(result, "T1") == "is out of service"
    assert reason_for(result, "T2") == "has no till implement"
    assert reason_for(result, "T3") == "is out of service"


def test_held_job_too_big_for_one_tank(farm: Farm) -> None:
    raw = copy.deepcopy(farm.raw)
    next(n for n in raw["nodes"] if n["id"] == "F1")["area_ha"] = 5000
    huge = farm_from_dict(raw)
    world = WorldState.start_of_day(huge)
    road_map = RoadMap(huge.raw)
    day = plan(huge, road_map, world)
    assert "J1" in day.held
    result = why(huge, road_map, world, day, "J1")
    check_shape(huge, result)
    assert result["chosen"] is None and result["held_reason"]
    assert reason_for(result, "T1") == "has no spray implement"
    assert reason_for(result, "T2") == "cannot fit it in within the fuel rules"
    assert reason_for(result, "T3") == "cannot fit it in within the fuel rules"


def test_done_job(farm: Farm, world: WorldState) -> None:
    state = world.jobs["J1"]
    state.status, state.remaining_ha, state.tractor = "done", 0.0, "T2"
    state.started_at, state.done_at = 5.0, 61.0
    world.now = 61.0
    road_map = RoadMap(farm.raw)
    result = why(farm, road_map, world, plan(farm, road_map, world), "J1")
    check_shape(farm, result)
    assert result["status"] == "done"
    assert result["chosen"]["tractor"] == "T2"
    assert result["chosen"]["implement"] == "sprayer_18"
    assert result["chosen"]["finish"] == 61.0
    assert result["chosen"]["margin_min"] == 179
    assert result["others"] == []
    assert result["text"] == ["Spray North 40 is finished. Bolt did it with the 18 m boom sprayer, "
                              "done at 07:01, 179 min before its 10:00 deadline."]


def test_work_in_progress_stays_with_its_tractor(farm: Farm, world: WorldState) -> None:
    world.now = 20.0
    world.tractors["T2"].busy_job = "J1"
    world.tractors["T2"].node, world.tractors["T2"].free_at = "F1", 61.0
    world.jobs["J1"].status, world.jobs["J1"].tractor = "active", "T2"
    world.jobs["J1"].started_at = 5.0
    road_map = RoadMap(farm.raw)
    result = why(farm, road_map, world, plan(farm, road_map, world), "J1")
    check_shape(farm, result)
    assert result["status"] == "active"
    assert result["chosen"]["tractor"] == "T2"
    assert "working it now" in result["text"][0]
    assert reason_for(result, "T1") == "has no spray implement"
    assert reason_for(result, "T3") == "is not used because Bolt has already started it"


def test_unknown_job_raises(farm: Farm, world: WorldState, first: Plan) -> None:
    with pytest.raises(KeyError):
        why(farm, RoadMap(farm.raw), world, first, "J99")


def test_does_not_change_the_plan_or_the_world(farm: Farm, world: WorldState,
                                               first: Plan) -> None:
    order_before = copy.deepcopy(first.job_order())
    world_before = copy.deepcopy(world)
    road_map = RoadMap(farm.raw)
    for job_id in farm.jobs:
        why(farm, road_map, world, first, job_id)
    assert first.job_order() == order_before
    assert world == world_before


def test_learned_speed_factor_is_used(farm: Farm, world: WorldState, first: Plan) -> None:
    road_map = RoadMap(farm.raw)
    normal = reason_for(why(farm, road_map, world, first, "J1"), "T3")
    slow = reason_for(why(farm, road_map, world, first, "J1",
                          speed_factor=lambda tractor, implement, field:
                          0.5 if tractor == "T3" else 1.0), "T3")
    assert normal != slow
