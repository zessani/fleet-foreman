import json

import pytest

from foreman import ai_recovery, ai_safety
from foreman.ai_base import ModelReply
from foreman.ai_recovery import RecoveryCase
from foreman.ai_safety import SafetyCase


class FakeClient:
    name = "fake"

    def __init__(self, reply=None, raises=False):
        self.reply = reply
        self.raises = raises
        self.seen = None

    def ask(self, system, prompt, tools, schema, timeout_s=10.0):
        self.seen = {"system": system, "prompt": prompt, "tools": tools, "schema": schema}
        if self.raises:
            raise RuntimeError("boom")
        return self.reply


def good(answer):
    return ModelReply(ok=True, answer=answer, tools=[{"name": "past_lessons", "args": {}}], ms=12.34)


def recovery_case():
    return RecoveryCase(
        tractor_id="t1", tractor_name="Rusty", report="hydraulic hose burst", clock="09:40",
        plan_no=3, now=100.0,
        tractor_status={"t1": {"name": "Rusty", "state": "broken", "job": "j1", "fuel_pct": 50, "mounted": "plow"}},
        jobs_at_risk=[{"job": "j1", "label": "Plow A", "priority": 1, "deadline": 300, "pct_done": 40, "slack_min": 60}],
        past_lessons=[{"tractor": "Rusty", "report": "hose leak", "downtime_min": 45, "lesson": "carry spare hoses"}],
    )


def safety_case(may_replan=True):
    return SafetyCase(
        plan_no=4, clock="10:00", now=120.0,
        plan_summary={
            "tractors": {
                "t1": {"name": "Rusty", "jobs": [
                    {"job": "j1", "label": "Plow", "start": 0, "finish": 30, "deadline": 200, "late_min": 0, "after": None},
                    {"job": "j2", "label": "Seed", "start": 30, "finish": 60, "deadline": 200, "late_min": 0, "after": "j1"}]},
                "t2": {"name": "Dusty", "jobs": []},
            },
            "held": {"j3": "no implement"},
            "out_of_service": [],
        },
        tractor_history={"t1": {"name": "Rusty", "jobs_done_today": 2, "breakdowns_today": 1, "minutes_worked": 90}},
        recent_log=["09:00 plan 3", "09:30 Rusty fault"],
        past_lessons=[], job_ids=["j1", "j2", "j3"], may_request_replan=may_replan,
    )


GOOD_RECOVERY = {"severity": "moderate", "downtime_min": 60, "reason": "hose swap", "lesson": "Keep spare hoses."}


def concern(action="warn", job=None, caution=None, text="check this"):
    return {"concern": text, "action": action, "job": job, "caution": caution}


# ---- recovery ----

def test_recovery_good_answer():
    call = ai_recovery.diagnose(FakeClient(good(GOOD_RECOVERY)), recovery_case())
    assert call.status == "done"
    assert call.answer == GOOD_RECOVERY
    assert call.agent == "AI Recovery agent"
    assert call.input == "Rusty: hydraulic hose burst"
    assert call.ms == 12.3


def test_recovery_whole_float_and_trim():
    answer = {**GOOD_RECOVERY, "downtime_min": 30.0, "reason": "  " + "x" * 300, "lesson": "y" * 300}
    call = ai_recovery.diagnose(FakeClient(good(answer)), recovery_case())
    assert call.answer["downtime_min"] == 30 and isinstance(call.answer["downtime_min"], int)
    assert len(call.answer["reason"]) == 200 and len(call.answer["lesson"]) == 200


def test_recovery_model_failure_keeps_error():
    call = ai_recovery.diagnose(FakeClient(ModelReply(ok=False, error="timed out")), recovery_case())
    assert call.status == "fallback" and call.error == "timed out" and call.answer is None


def test_recovery_client_raising_falls_back():
    call = ai_recovery.diagnose(FakeClient(raises=True), recovery_case())
    assert call.status == "fallback" and "boom" in call.error


@pytest.mark.parametrize("bad", [
    {**GOOD_RECOVERY, "severity": "catastrophic"},
    {**GOOD_RECOVERY, "downtime_min": -5},
    {**GOOD_RECOVERY, "downtime_min": 721},
    {**GOOD_RECOVERY, "downtime_min": 10.5},
    {**GOOD_RECOVERY, "downtime_min": "60"},
    {**GOOD_RECOVERY, "downtime_min": True},
    {**GOOD_RECOVERY, "reason": "  "},
    {k: v for k, v in GOOD_RECOVERY.items() if k != "lesson"},
    None,
])
def test_recovery_bad_shapes_fall_back(bad):
    call = ai_recovery.diagnose(FakeClient(good(bad)), recovery_case())
    assert call.status == "fallback" and call.answer is None and call.error


def test_recovery_tools_and_prompt():
    client = FakeClient(good(GOOD_RECOVERY))
    case = recovery_case()
    call = ai_recovery.diagnose(client, case)
    tools = {t.name: t for t in client.seen["tools"]}
    assert set(tools) == {"tractor_status", "jobs_at_risk", "past_lessons"}
    assert tools["tractor_status"].select_by == "tractor"
    assert tools["tractor_status"].data == case.tractor_status
    assert tools["jobs_at_risk"].data == case.jobs_at_risk
    assert tools["past_lessons"].data == case.past_lessons
    case.jobs_at_risk[0]["priority"] = 99  # the tool data is a snapshot
    assert tools["jobs_at_risk"].data[0]["priority"] == 1
    assert "never assigns jobs" in client.seen["system"]
    assert "cannot approve" in client.seen["system"]
    assert "Rusty" in client.seen["prompt"] and "09:40" in client.seen["prompt"]
    assert "hydraulic hose burst" in client.seen["prompt"]
    json.dumps(call.to_dict())


# ---- safety ----

def test_safety_good_answer():
    answer = {"concerns": [concern("hold_job", job="j3"),
                           concern("request_replan", caution="spread_dependent_jobs")]}
    call = ai_safety.review(FakeClient(good(answer)), safety_case())
    assert call.status == "done"
    assert call.agent == "AI Safety agent"
    assert call.input == "2 jobs on 2 tractors, 1 held"
    assert call.answer["concerns"][0]["action"] == "hold_job"
    assert call.answer["concerns"][0]["job"] == "j3"
    assert call.answer["concerns"][1]["caution"] == "spread_dependent_jobs"


def test_safety_empty_concerns_is_done():
    call = ai_safety.review(FakeClient(good({"concerns": []})), safety_case())
    assert call.status == "done" and call.answer == {"concerns": []}


def test_safety_model_failure_keeps_error():
    call = ai_safety.review(FakeClient(ModelReply(ok=False, error="no key")), safety_case())
    assert call.status == "fallback" and call.error == "no key" and call.answer is None


def test_safety_client_raising_falls_back():
    call = ai_safety.review(FakeClient(raises=True), safety_case())
    assert call.status == "fallback" and "boom" in call.error


@pytest.mark.parametrize("bad", [{"concerns": "none"}, {"concerns": None}, {}, None, ["x"]])
def test_safety_bad_shapes_fall_back(bad):
    call = ai_safety.review(FakeClient(good(bad)), safety_case())
    assert call.status == "fallback" and call.answer is None and call.error


def test_safety_unknown_job_downgrades_to_warn():
    checked, err = ai_safety.check_answer({"concerns": [concern("hold_job", job="nope")]}, safety_case())
    assert err is None
    assert checked["concerns"][0]["action"] == "warn" and checked["concerns"][0]["job"] is None


def test_safety_unknown_caution_downgrades_to_warn():
    checked, _ = ai_safety.check_answer({"concerns": [concern("request_replan", caution="go_faster")]}, safety_case())
    assert checked["concerns"][0]["action"] == "warn"


def test_safety_replan_not_allowed_downgrades_to_warn():
    answer = {"concerns": [concern("request_replan", caution="spread_dependent_jobs")]}
    checked, _ = ai_safety.check_answer(answer, safety_case(may_replan=False))
    assert checked["concerns"][0]["action"] == "warn"


def test_safety_second_replan_downgrades_and_cap_at_five():
    replan = concern("request_replan", caution="spread_dependent_jobs")
    answer = {"concerns": [replan, replan] + [concern()] * 6}
    checked, _ = ai_safety.check_answer(answer, safety_case())
    actions = [c["action"] for c in checked["concerns"]]
    assert actions[:2] == ["request_replan", "warn"]
    assert len(actions) == 5


def test_safety_long_text_trimmed():
    checked, _ = ai_safety.check_answer({"concerns": [concern(text="z" * 500)]}, safety_case())
    assert len(checked["concerns"][0]["concern"]) == 200


def test_safety_tools_and_prompt():
    client = FakeClient(good({"concerns": []}))
    case = safety_case()
    call = ai_safety.review(client, case)
    tools = {t.name: t for t in client.seen["tools"]}
    assert set(tools) == {"plan_summary", "tractor_history", "recent_log", "past_lessons"}
    assert tools["tractor_history"].select_by == "tractor"
    assert tools["plan_summary"].data == case.plan_summary
    assert tools["tractor_history"].data == case.tractor_history
    assert tools["recent_log"].data == case.recent_log
    case.plan_summary["held"]["j9"] = "later change"  # the tool data is a snapshot
    assert "j9" not in tools["plan_summary"].data["held"]
    system, prompt = client.seen["system"], client.seen["prompt"]
    assert "never assigns jobs" in system and "cannot approve" in system
    assert "spread_dependent_jobs" in prompt and "empty list" in prompt
    assert "You may use request_replan" in prompt
    json.dumps(call.to_dict())


def test_safety_prompt_forbids_replan_when_not_allowed():
    client = FakeClient(good({"concerns": []}))
    ai_safety.review(client, safety_case(may_replan=False))
    assert "may NOT use request_replan" in client.seen["prompt"]


GPS = "GPS receiver lost its RTK fix and will not reacquire. Auto-steer has disengaged."


def _memory(*lessons):
    from foreman.memory import Memory
    memory = Memory(360.0)
    memory.lessons.extend(lessons)
    return memory


def test_key_words_keep_short_capitals():
    from foreman.memory import _key_words
    assert {"gps", "rtk", "pto", "def"} <= _key_words("GPS RTK PTO DEF fix and the")
    assert "fix" not in _key_words("fix the and") and "a1" in _key_words("part A1")


def test_relevant_lessons_measured_before_guessed_and_superseded_guess_dropped():
    guess = {"report": GPS, "severity": "moderate", "downtime_min": 60, "lesson": "x"}
    other_guess = {"report": "GPS antenna cable cut", "downtime_min": 90, "lesson": "y"}
    measured = {"report": GPS, "estimated_min": 60, "actual_downtime_min": 15, "lesson": "z"}
    got = _memory(guess, other_guess, measured).relevant_lessons(GPS)
    assert got == [measured, other_guess]


def test_relevant_lessons_gps_rtk_only_match_and_unrelated_excluded():
    rtk = {"report": "RTK base station offline", "actual_downtime_min": 20, "lesson": "a"}
    unrelated = {"report": "Alternator belt snapped, battery light is on.", "actual_downtime_min": 45, "lesson": "b"}
    assert _memory(unrelated, rtk).relevant_lessons("Rover RTK dropped") == [rtk]
    assert _memory(unrelated).relevant_lessons(GPS) == []


def test_recovery_prompt_and_tool_say_measured_time_outweighs_estimates():
    client = FakeClient(good(GOOD_RECOVERY))
    ai_recovery.diagnose(client, recovery_case())
    tool = {t.name: t for t in client.seen["tools"]}["past_lessons"]
    for text in (tool.description, client.seen["prompt"]):
        assert "actual_downtime_min" in text and "outweighs" in text
