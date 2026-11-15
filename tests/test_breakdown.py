"""Breakdown recovery: no human, no lost work, no repeated work, no rule broken."""

import copy
import json
from pathlib import Path

import pytest

from foreman.day import Day
from foreman.watch import STALL_MIN

EXAMPLE = Path(__file__).resolve().parent.parent / "scenarios" / "example_farm.json"
TRACTORS = ("T1", "T2", "T3")
BREAK_TIMES = (3, 20, 45, 70, 100, 140, 180, 230, 300)


def example_raw(events: list[dict] | None = None, **changes) -> dict:
    """The example farm with its scripted events replaced by the ones given."""
    with open(EXAMPLE, encoding="utf-8") as farm_file:
        raw = json.load(farm_file)
    raw["events"] = events or []
    raw.update(changes)
    return raw


def ten_job_raw(events: list[dict] | None = None) -> dict:
    """The example farm grown to 8 fields and 10 jobs."""
    raw = example_raw(events)
    raw["nodes"] += [
        {"id": "F5", "kind": "field", "name": "South Pivot", "x": 0.5, "y": -2.2, "area_ha": 14},
        {"id": "F6", "kind": "field", "name": "Mesa Top", "x": -2.4, "y": -1.0, "area_ha": 9},
        {"id": "F7", "kind": "field", "name": "Canal Strip", "x": 4.2, "y": 0.4, "area_ha": 11},
        {"id": "F8", "kind": "field", "name": "Home Quarter", "x": -0.8, "y": 2.9, "area_ha": 8},
    ]
    raw["roads"] += [["YARD", "F5"], ["YARD", "F6"], ["X2", "F7"], ["X1", "F8"]]
    raw["jobs"] += [
        {"id": "J6", "type": "spray", "field": "F5", "priority": 3, "deadline": 300},
        {"id": "J7", "type": "mow", "field": "F6", "priority": 2, "deadline": 540},
        {"id": "J8", "type": "till", "field": "F7", "priority": 3, "deadline": 420},
        {"id": "J9", "type": "plant", "field": "F7", "priority": 4, "deadline": 640, "after": "J8"},
        {"id": "J10", "type": "spray", "field": "F8", "priority": 5, "deadline": 200, "release": 30},
    ]
    return raw


def run_day(raw: dict) -> Day:
    day = Day(raw)
    day.advance(day.farm.horizon_min)
    return day


def check_day_is_clean(day: Day) -> None:
    """The promises that must hold whatever broke and whenever."""
    for run in (day.foreman, day.baseline):
        assert run.sim.violations == []
        for job_id, state in run.sim.jobs.items():
            area = day.farm.jobs[job_id].area_ha
            worked = run.sim.worked_ha.get(job_id, 0.0)
            # Never more than the field: nothing is covered twice.
            assert worked <= area + 1e-6
            if state.status == "done":
                # And all of it: nothing is skipped.
                assert worked == pytest.approx(area, abs=1e-6)
    # Every unfinished job is either still planned or reported as held.
    foreman = day.foreman
    planned = {stop.job for stop in foreman.plan.stops()}
    for job_id, state in foreman.sim.jobs.items():
        if state.status != "done":
            assert job_id in planned or job_id in foreman.plan.held
    # Nothing was dispatched without passing Safety, and every re-plan was fast and explained.
    assert not day.memory.log.of("plan_rejected")
    assert not day.memory.log.of("stand_down")
    assert all(replan["ms"] < 1000 for replan in foreman.replans)
    assert foreman.explanation["headline"]


@pytest.mark.parametrize("tractor", TRACTORS)
@pytest.mark.parametrize("minute", BREAK_TIMES)
@pytest.mark.parametrize("farm", ["example", "ten jobs"])
def test_any_tractor_can_break_at_any_time(farm: str, tractor: str, minute: int) -> None:
    build = example_raw if farm == "example" else ten_job_raw
    day = run_day(build([{"at": minute, "type": "breakdown", "tractor": tractor}]))
    check_day_is_clean(day)
    # The failure was noticed from the status feed, within one reading.
    detected = day.memory.log.of("failure_detected")
    assert len(detected) == 1
    assert detected[0]["data"]["detection_min"] <= 1.0
    assert day.foreman.sim.tractors[tractor].status()["state"] == "broken"


def test_finished_work_is_kept_after_a_breakdown() -> None:
    quiet = run_day(example_raw())
    broken = run_day(example_raw([{"at": 150, "type": "breakdown", "tractor": "T1"}]))
    for job_id, state in quiet.foreman.sim.jobs.items():
        if state.done_at is not None and state.done_at < 150:
            after = broken.foreman.sim.jobs[job_id]
            assert after.status == "done"
            assert after.done_at == state.done_at
            assert after.tractor == state.tractor


def test_a_half_done_field_is_finished_once_by_another_tractor() -> None:
    # Atlas (T1) is mowing River Flat (J4) at minute 150 in the day plan.
    day = Day(example_raw([{"at": 150, "type": "breakdown", "tractor": "T1"}]))
    day.advance(149)
    assert day.foreman.sim.jobs["J4"].tractor == "T1"
    day.advance(3)
    done_before = day.foreman.sim.worked_ha["J4"]
    assert 0 < done_before < day.farm.jobs["J4"].area_ha
    day.advance(day.farm.horizon_min)
    job = day.foreman.sim.jobs["J4"]
    assert job.status == "done" and job.tractor != "T1"
    assert day.foreman.sim.worked_ha["J4"] == pytest.approx(day.farm.jobs["J4"].area_ha, abs=1e-6)
    check_day_is_clean(day)


def test_breakdown_leaves_unaffected_tractors_alone_when_no_deadline_needs_it() -> None:
    # Deadlines are loose here, so nothing justifies touching the healthy tractors.
    raw = ten_job_raw([{"at": 60, "type": "breakdown", "tractor": "T1"}])
    for job in raw["jobs"]:
        job["deadline"] = 660
    day = Day(raw)
    day.advance(59)
    before = day.foreman.plan.job_order()
    day.advance(5)
    after = day.foreman.plan
    for tractor_id in ("T2", "T3"):
        for job_id in before[tractor_id]:
            if day.foreman.sim.jobs[job_id].status != "done":
                assert after.stop_for(job_id).tractor == tractor_id
    story = day.foreman.explanation
    assert story["moved"] == len([j for j in before["T1"]
                                  if day.foreman.sim.jobs[j].status != "done"
                                  and j not in after.held])
    assert story["unchanged"] + story["retimed"] > 0
    assert f"{story['moved']} job" in story["summary"]


def test_breakdown_while_traveling() -> None:
    day = Day(example_raw([{"at": 2, "type": "breakdown", "tractor": "T2"}]))
    day.advance(1.9)
    assert day.foreman.sim.tractors["T2"].status()["state"] == "traveling"
    day.advance(day.farm.horizon_min)
    assert day.memory.incidents[0]["state"] == "traveling"
    assert day.foreman.replans[1]["events"] == ["breakdown"]
    assert day.foreman.sim.all_done()
    check_day_is_clean(day)


def test_breakdown_at_the_yard_during_a_swap() -> None:
    # Cedar (T3) starts the day swapping to the disc at the yard.
    day = Day(example_raw([{"at": 10, "type": "breakdown", "tractor": "T3"}]))
    day.advance(9)
    assert day.foreman.sim.tractors["T3"].status()["state"] == "servicing"
    day.advance(day.farm.horizon_min)
    assert day.foreman.sim.all_done()
    assert day.foreman.sim.tractors["T3"].minutes["work"] == 0
    check_day_is_clean(day)


def test_only_tractor_for_a_job_type_breaks_and_the_job_is_held_with_a_reason() -> None:
    # Make Atlas the only tractor with a planter, then break it before planting.
    raw = example_raw([{"at": 20, "type": "breakdown", "tractor": "T1"}])
    for tractor in raw["tractors"]:
        if tractor["id"] == "T3":
            tractor["implements"] = ["sprayer_18", "disc_6"]
            tractor["mounted"] = "disc_6"
    day = run_day(raw)
    assert "out of service" in day.foreman.plan.held["J3"]
    assert day.foreman.sim.jobs["J3"].status != "done"
    assert day.foreman.block()["jobs"][2]["status"] == "held"
    # Everything that can still be done is done.
    assert {j for j, s in day.foreman.sim.jobs.items() if s.status == "done"} == {"J1", "J2", "J4", "J5"}
    check_day_is_clean(day)


def test_two_tractors_down_at_once_share_one_replan() -> None:
    day = Day(ten_job_raw([{"at": 50, "type": "breakdown", "tractor": "T1"},
                           {"at": 50, "type": "breakdown", "tractor": "T2"}]))
    day.advance(49)
    plans_before = day.foreman.plan_no
    day.advance(3)
    assert day.foreman.plan_no == plans_before + 1  # two failures, one re-plan
    assert day.foreman.replans[-1]["events"] == ["breakdown", "breakdown"]
    day.advance(day.farm.horizon_min)
    assert all(stop.tractor == "T3" for stop in day.foreman.plan.stops())
    check_day_is_clean(day)


def test_a_repair_later_in_the_day_brings_held_work_back() -> None:
    raw = example_raw([{"at": 20, "type": "breakdown", "tractor": "T1"},
                       {"at": 200, "type": "repair", "tractor": "T1"}])
    for tractor in raw["tractors"]:
        if tractor["id"] == "T3":
            tractor["implements"] = ["sprayer_18", "disc_6"]
            tractor["mounted"] = "disc_6"
    day = Day(raw)
    day.advance(150)
    assert "J3" in day.foreman.plan.held
    day.advance(day.farm.horizon_min)
    assert day.memory.log.of("back_in_service")
    assert day.foreman.sim.jobs["J3"].status == "done"
    assert day.foreman.sim.jobs["J3"].tractor == "T1"
    assert day.foreman.plan.held == {}
    check_day_is_clean(day)


def test_a_silent_failure_is_caught_by_lack_of_progress() -> None:
    # Atlas keeps reporting "working" but stops covering ground at minute 100.
    day = run_day(example_raw([{"at": 100, "type": "breakdown", "tractor": "T1", "silent": True}]))
    detected = day.memory.log.of("failure_detected")
    assert len(detected) == 1
    assert detected[0]["data"]["cause"] == "stall"
    assert STALL_MIN <= detected[0]["data"]["detection_min"] <= STALL_MIN + 2.0
    assert day.foreman.sim.all_done()
    check_day_is_clean(day)


def test_events_from_the_operator_are_applied_to_both_worlds() -> None:
    day = Day(example_raw())
    day.advance(30)
    assert day.apply({"type": "breakdown", "tractor": "T2"}) == {"ok": True, "at": 30.0}
    assert day.apply({"type": "breakdown", "tractor": "T9"})["ok"] is False
    assert day.apply({"type": "nonsense"})["ok"] is False
    day.advance(2)
    for run in (day.foreman, day.baseline):
        assert run.sim.tractors["T2"].status()["state"] == "broken"
        assert all(stop.tractor != "T2" for stop in run.plan.stops())
    assert day.manual == [{"type": "breakdown", "tractor": "T2", "at": 30.0}]


def test_priority_change_and_new_job_trigger_a_replan() -> None:
    day = Day(example_raw())
    day.advance(40)
    plans = day.foreman.plan_no
    assert day.apply({"type": "set_priority", "job": "J4", "priority": 5})["ok"]
    assert day.farm.jobs["J4"].priority == 5
    assert day.apply({"type": "add_job", "job": {"id": "J6", "type": "spray", "field": "F3",
                                                 "priority": 4, "deadline": 600}})["ok"]
    assert day.foreman.plan_no == plans + 2
    day.advance(day.farm.horizon_min)
    assert day.foreman.sim.jobs["J6"].status == "done"
    check_day_is_clean(day)


def test_the_farm_given_to_a_day_is_not_changed_by_its_events() -> None:
    raw = example_raw()
    before = copy.deepcopy(raw)
    day = Day(raw)
    day.apply({"type": "add_job", "job": {"id": "J6", "type": "spray", "field": "F3"}})
    assert raw == before


def test_a_job_that_runs_well_behind_plan_triggers_a_replan() -> None:
    # West Bench tills at 60% of the catalog rate, which the planner does not know.
    # Planting there has to wait for it, so the rest of the day needs new times.
    day = run_day(example_raw(truth={"*|till|F4": 0.6}))
    drift = day.memory.log.of("drift")
    assert len(drift) == 1 and drift[0]["data"]["job"] == "J2"
    assert drift[0]["data"]["behind_min"] > 15
    assert ["drift"] in [replan["events"] for replan in day.foreman.replans]
    assert day.foreman.sim.all_done()
    check_day_is_clean(day)


def test_a_day_that_runs_to_plan_is_never_replanned() -> None:
    day = run_day(example_raw(truth={}))
    assert [replan["events"] for replan in day.foreman.replans] == [["jobs_submitted"]]
