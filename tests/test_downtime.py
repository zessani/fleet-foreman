"""Expected downtime: "out for the day", the hold rule, and the lesson saved at the end of the day."""

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from foreman import ai_recovery
from foreman.ai_crew import AiCrew
from foreman.day import Day
from foreman.faults import FAULTS, OUT_ALL_DAY_REPORT, out_all_day_report
from foreman.model import farm_from_dict, load_farm
from foreman.nav import RoadMap
from foreman.planner import HOLD_DEADLINE_MARGIN_MIN, plan_with_return
from foreman.state import WorldState
from tests.test_ai_agents import FakeClient, good, recovery_case
from tests.test_ai_wiring import (EXAMPLE, ScriptedModel, breakdown, check_clean, day_with,
                                  example_raw, waiting)

SAMPLE = Path(__file__).resolve().parent.parent / "scenarios" / "farms" / "sample_farm.json"
OUT_MESSAGE = "Atlas is out for the day. Its work stays with the other tractors."


def answer(minutes: int, out_for_day: bool | None = None, severity: str = "severe") -> dict:
    reply = {"severity": severity, "downtime_min": minutes,
             "reason": "Gearbox has to go to the workshop.", "lesson": "Gearbox faults end the day."}
    if out_for_day is not None:
        reply["out_for_day"] = out_for_day
    return reply


def recovery_call(day: Day):
    return next(call for call in day.desk.crew.calls if call.agent == "AI Recovery agent")


def outcome_lessons(day: Day) -> list[dict]:
    """Lessons that carry what really happened (not the AI Recovery agent's own guess)."""
    return [lesson for lesson in day.memory.lessons
            if "actual_downtime_min" in lesson or "still_down_after_min" in lesson]


# ---- the fault report ----

def test_the_scripted_breakdown_report_says_the_tractor_is_out_all_day() -> None:
    assert out_all_day_report() == OUT_ALL_DAY_REPORT
    assert "workshop" in OUT_ALL_DAY_REPORT and "today" in OUT_ALL_DAY_REPORT
    assert len(FAULTS) == 8  # the samples stay as they are: their texts match saved lessons
    assert OUT_ALL_DAY_REPORT not in {fault["report"] for fault in FAULTS}


def test_the_demo_day_breakdown_gets_that_report_and_no_repair_time() -> None:
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    model = ScriptedModel(recovery=[answer(450, True)])
    day = Day(raw, ai=AiCrew(model, sync=True))
    day.advance(212)
    fault = day.faults["T3"]
    assert fault["report"] == OUT_ALL_DAY_REPORT and fault["true_downtime"] is None
    assert fault["out_for_day"] is True


# ---- the answer checker ----

@pytest.mark.parametrize("given, expected", [(True, True), (False, False), (None, False)])
def test_out_for_day_is_accepted_and_defaults_to_false(given, expected) -> None:
    checked, error = ai_recovery.check_answer(answer(400, given))
    assert error is None and checked["out_for_day"] is expected


@pytest.mark.parametrize("bad", [
    answer(400, "yes"),                          # not a true or false
    {**answer(400), "out_for_day": 1},           # a number is not a true or false
    answer(400, True, severity="minor"),         # a minor fault cannot end the day
])
def test_bad_out_for_day_answers_fall_back(bad) -> None:
    call = ai_recovery.diagnose(FakeClient(good(bad)), recovery_case())
    assert call.status == "fallback" and call.answer is None
    assert "out_for_day" in call.error or "minor" in call.error


def test_schema_and_prompt_offer_out_for_day() -> None:
    assert "out_for_day" in ai_recovery.SCHEMA["required"]
    assert ai_recovery.SCHEMA["properties"]["out_for_day"] == {"type": "boolean"}
    client = FakeClient(good(answer(400, True)))
    case = replace(recovery_case(), day_ends_in_min=420.0)
    ai_recovery.diagnose(client, case)
    prompt = client.seen["prompt"]
    assert "out_for_day" in prompt and "past the end of the working day" in prompt
    assert "The working day ends 420 min from now." in prompt


# ---- what Foreman does with "out for the day" ----

def test_out_for_day_holds_nothing_and_sets_no_timer(tmp_path) -> None:
    events = [breakdown(100, "T1", None)]
    plain = Day(example_raw(events))  # no AI agents
    plain.advance(102)
    day = day_with(ScriptedModel(recovery=[answer(560, True)]), events, tmp_path)
    day.advance(102)
    assert waiting(day) == []
    assert "T1" not in day.foreman.returning
    assert not [event for event in day._pending if event["type"] == "_downtime_expired"]
    assert "downtime_estimate" not in [e for r in day.foreman.replans for e in r["events"]]
    assert day.foreman.plan.job_order() == plain.foreman.plan.job_order()  # the rule-based plan stands
    call = recovery_call(day)
    assert call.applied == OUT_MESSAGE
    assert [line["message"] for line in day.memory.log.of("ai_applied")
            if line["agent"] == "AI Recovery agent"] == [OUT_MESSAGE]
    assert "out for the day" in day.memory.log.of("ai_diagnosis")[0]["message"]
    assert day.memory.lessons[0]["out_for_day"] is True
    day.advance(day.farm.horizon_min)
    check_clean(day)


def test_an_estimate_past_the_end_of_the_day_counts_as_out_for_the_day() -> None:
    day = day_with(ScriptedModel(recovery=[answer(600, False)]), [breakdown(100, "T1", None)])
    assert 100 + 600 >= day.farm.horizon_min
    day.advance(102)
    assert waiting(day) == [] and "T1" not in day.foreman.returning
    assert not [event for event in day._pending if event["type"] == "_downtime_expired"]
    assert recovery_call(day).applied == OUT_MESSAGE
    assert day.faults["T1"]["out_for_day"] is True


# ---- the hold rule ----

def _plan_while_atlas_is_down(raw: dict, back_at: float):
    farm = farm_from_dict(raw)
    world = WorldState.start_of_day(farm)
    world.tractors["T1"] = replace(world.tractors["T1"], available=False)
    result = plan_with_return(farm, RoadMap(farm.raw), world, None, {"T1": back_at},
                              lambda tractor_id: f"waiting for {tractor_id}", {})
    return farm, result


def _with_deadlines(**deadlines: float) -> dict:
    raw = copy.deepcopy(load_farm(EXAMPLE).raw)
    for job in raw["jobs"]:
        if job["id"] in deadlines:
            job["deadline"] = deadlines.pop(job["id"])
    for job_id, deadline in deadlines.items():  # new mowing jobs on North 40
        raw["jobs"].append({"id": job_id, "type": "mow", "field": "F1", "priority": 2,
                            "deadline": deadline, "release": 0, "after": None})
    return raw


def test_a_job_with_room_before_its_deadline_is_held() -> None:
    # Atlas back at minute 20: in the waiting schedule River Flat finishes near 305, deadline 480.
    _, result = _plan_while_atlas_is_down(_with_deadlines(), 20)
    assert result.held == {"J4": "waiting for T1"}


def test_a_job_that_would_finish_inside_the_margin_is_moved_now() -> None:
    # A 330 deadline: waiting for Atlas would finish River Flat 25 min before it, under 30.
    # The rule moves it now even though, on this busy day, the move makes it late.
    _, result = _plan_while_atlas_is_down(_with_deadlines(J4=330, J6=300), 20)
    assert HOLD_DEADLINE_MARGIN_MIN == 30.0
    assert result.held == {}
    assert result.stop_for("J4").tractor != "T1"


def test_one_job_is_held_and_the_tight_one_is_moved() -> None:
    # Waiting gives Atlas the tilling (lots of room) and a second mowing job that would
    # finish 8 min before its deadline. The tilling waits for Atlas; the mowing moves now.
    farm, result = _plan_while_atlas_is_down(_with_deadlines(J4=330, J6=400), 20)
    assert result.held["J2"] == "waiting for T1"
    assert result.held["J3"].startswith("must follow J2")  # depends on held work, so it waits too
    stop = result.stop_for("J6")
    assert stop is not None and stop.tractor != "T1"
    assert all(stop.tractor != "T1" for stop in result.stops())


def test_a_job_is_moved_when_a_job_that_depends_on_it_would_lose_its_margin() -> None:
    # The tilling has room, but the planting that must follow it would finish too close.
    _, held_plan = _plan_while_atlas_is_down(_with_deadlines(J4=330), 20)
    assert held_plan.held.get("J2") == "waiting for T1"
    _, result = _plan_while_atlas_is_down(_with_deadlines(J4=330, J3=360), 20)
    assert "J2" not in result.held and result.stop_for("J2").tractor != "T1"


# ---- the lesson saved at the end of the day ----

def test_repaired_saves_the_real_minutes(tmp_path) -> None:
    day = day_with(ScriptedModel(recovery=[answer(20, False, "minor")]), [breakdown(100, "T1", 20)], tmp_path)
    day.advance(day.farm.horizon_min)
    assert [(l["estimated_min"], l["actual_downtime_min"]) for l in outcome_lessons(day)] == [(20, 20)]


def test_a_missed_estimate_is_saved_once_at_the_end_of_the_day(tmp_path) -> None:
    day = day_with(ScriptedModel(recovery=[answer(20, False, "minor")]), [breakdown(100, "T1", None)], tmp_path)
    day.advance(day.farm.horizon_min)
    day_end = [l for l in outcome_lessons(day) if l.get("not_repaired_that_day")]
    assert len(day_end) == 1 and day_end[0]["estimated_min"] == 20


def test_out_for_day_that_proved_right_is_saved(tmp_path) -> None:
    day = day_with(ScriptedModel(recovery=[answer(560, True)]), [breakdown(100, "T1", None)], tmp_path)
    day.advance(day.farm.horizon_min)
    assert day.finished
    [lesson] = outcome_lessons(day)
    assert lesson["estimated_out_for_day"] is True and lesson["not_repaired_that_day"] is True
    assert lesson["lesson"].startswith(
        "The AI Recovery agent said out for the day and Atlas was not repaired that day")
    assert day.memory.log.of("estimate_confirmed")
    saved = [json.loads(line) for line in (tmp_path / "lessons.jsonl").read_text().splitlines()]
    assert saved[-1] == lesson
    # The measured outcome comes first for the same kind of fault next time.
    report = "Hydraulic hose burst on the implement lift."
    assert day.memory.relevant_lessons(report)[0] == lesson


def test_out_for_day_that_proved_wrong_saves_the_real_minutes(tmp_path) -> None:
    # The model says out for the day; the tractor is really back after 120 minutes.
    day = day_with(ScriptedModel(recovery=[answer(560, True)]), [breakdown(100, "T1", 120)], tmp_path)
    day.advance(102)
    assert recovery_call(day).applied == OUT_MESSAGE
    day.advance(day.farm.horizon_min)
    [lesson] = outcome_lessons(day)
    assert lesson["estimated_out_for_day"] is True and lesson["actual_downtime_min"] == 120
    assert "said out for the day, but this fault took 120 min" in lesson["lesson"]
    assert "repaired" in [e for r in day.foreman.replans for e in r["events"]]  # the repair path still works
    assert "T1" not in day.foreman.watch.down
    check_clean(day)
