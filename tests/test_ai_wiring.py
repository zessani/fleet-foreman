"""The AI agents add caution and nothing else: rules first, answers applied when they arrive."""

import json
import time
from pathlib import Path

from foreman.ai_base import AI_TIMEOUT_S, ModelReply
from foreman.ai_crew import AiCrew
from foreman.day import Day
from foreman.engine import Engine

EXAMPLE = Path(__file__).resolve().parent.parent / "scenarios" / "example_farm.json"
NO_CONCERNS = {"concerns": []}
CHAIN = {"concerns": [{"concern": "Cedar holds the tilling and the planting that depends on it.",
                       "action": "request_replan", "job": None,
                       "caution": "spread_dependent_jobs"}]}


def example_raw(events: list[dict] | None = None) -> dict:
    with open(EXAMPLE, encoding="utf-8") as farm_file:
        raw = json.load(farm_file)
    raw["events"] = events or []
    return raw


class ScriptedModel:
    """A fake model that answers each AI agent from its own list. No network."""

    name = "scripted fake"

    def __init__(self, recovery=(), safety=(), delay_s: float = 0.0) -> None:
        self.answers = {"recovery": list(recovery), "safety": list(safety)}
        self.delay_s = delay_s
        self.asked: list[str] = []

    def ask(self, system, prompt, tools, schema, timeout_s=AI_TIMEOUT_S) -> ModelReply:
        kind = "recovery" if "AI Recovery agent" in system else "safety"
        self.asked.append(kind)
        if self.delay_s:
            time.sleep(self.delay_s)
        queue = self.answers[kind]
        answer = queue.pop(0) if queue else (NO_CONCERNS if kind == "safety" else None)
        tool_calls = [{"name": tool.name, "args": {}} for tool in tools]
        if isinstance(answer, dict):
            return ModelReply(ok=True, answer=answer, tools=tool_calls, ms=5.0)
        return ModelReply(ok=False, error=str(answer or "no structured answer"), ms=5.0)


def diagnosis(minutes: int, severity: str = "moderate") -> dict:
    return {"severity": severity, "downtime_min": minutes,
            "reason": "Hose replacement on site.", "lesson": "Carry a spare lift hose."}


def breakdown(at: float, tractor: str, repair_after: float | None) -> dict:
    return {"at": at, "type": "breakdown", "tractor": tractor,
            "report": "Hydraulic hose burst on the implement lift.",
            "repair_after_min": repair_after}


def day_with(model, events, tmp_path=None) -> Day:
    lessons = tmp_path / "lessons.jsonl" if tmp_path else None
    return Day(example_raw(events), ai=AiCrew(model, sync=True), lessons_path=lessons)


def waiting(day: Day) -> list[str]:
    return [job for job, reason in day.foreman.plan.held.items() if reason.startswith("waiting for")]


def check_clean(day: Day) -> None:
    for run in (day.foreman, day.baseline):
        assert run.sim.violations == []
    assert not day.memory.log.of("plan_rejected") and not day.memory.log.of("stand_down")


def test_with_no_model_nothing_about_the_plan_changes() -> None:
    events = [breakdown(100, "T1", 35)]
    plain = Day(example_raw(events))
    off = Day(example_raw(events), ai=AiCrew(None))
    for day in (plain, off):
        day.advance(day.farm.horizon_min)
    assert off.desk is None
    assert off.foreman.sim.totals() == plain.foreman.sim.totals()
    assert [r["events"] for r in off.foreman.replans] == [r["events"] for r in plain.foreman.replans]


def test_a_short_downtime_estimate_holds_the_work_for_the_tractor(tmp_path) -> None:
    # Atlas is mowing River Flat at minute 100 and will really be back after 20 minutes.
    model = ScriptedModel(recovery=[diagnosis(20, "minor")])
    day = day_with(model, [breakdown(100, "T1", 20)], tmp_path)
    day.advance(102)
    assert waiting(day) == ["J4"]
    assert "expected back" in day.foreman.plan.held["J4"]
    assert all(stop.tractor != "T1" for stop in day.foreman.plan.stops())  # no job goes to a broken tractor
    call = day.desk.crew.calls[-2] if day.desk.crew.calls[-1].agent != "AI Recovery agent" else day.desk.crew.calls[-1]
    recovery_calls = [c for c in day.desk.crew.calls if c.agent == "AI Recovery agent"]
    assert recovery_calls[0].status == "done" and "held 1 job" in recovery_calls[0].applied
    assert [tool["name"] for tool in recovery_calls[0].tools] == ["tractor_status", "jobs_at_risk", "past_lessons"]
    assert call is not None

    day.advance(day.farm.horizon_min)
    job = day.foreman.sim.jobs["J4"]
    assert job.status == "done" and job.tractor == "T1"  # Atlas came back and finished its own field
    assert day.memory.estimates == [{"tractor": "Atlas", "estimate_min": 20, "actual_min": 20, "gap_min": 0}]
    assert day.memory.lessons[0]["lesson"] == "Carry a spare lift hose."
    assert "Carry a spare lift hose." in (tmp_path / "lessons.jsonl").read_text()
    check_clean(day)


def test_a_long_downtime_estimate_moves_the_work() -> None:
    model = ScriptedModel(recovery=[diagnosis(300, "severe")])
    day = day_with(model, [breakdown(100, "T1", None)])
    day.advance(102)
    assert waiting(day) == []
    assert day.foreman.plan.stop_for("J4").tractor == "T2"  # Bolt takes the rest of the mowing
    recovery = [c for c in day.desk.crew.calls if c.agent == "AI Recovery agent"][0]
    assert "kept the work moved" in recovery.applied
    assert day.memory.estimates == []  # no real repair time to compare with
    day.advance(day.farm.horizon_min)
    check_clean(day)


def test_a_wrong_estimate_stops_the_waiting_ten_minutes_after_it_passes() -> None:
    # The model says 20 minutes; the tractor is really out for the rest of the day.
    model = ScriptedModel(recovery=[diagnosis(20, "minor")])
    day = day_with(model, [breakdown(100, "T1", None)])
    day.advance(125)
    assert waiting(day) == ["J4"]
    day.advance(8)  # minute 133: past 100 + 20 + 10
    assert waiting(day) == []
    assert day.foreman.plan.stop_for("J4").tractor == "T2"
    assert "downtime_expired" in [e for r in day.foreman.replans for e in r["events"]]
    day.advance(day.farm.horizon_min)
    assert day.foreman.sim.jobs["J4"].status == "done"
    check_clean(day)


def test_no_answer_bad_answer_or_error_leaves_the_rule_based_plan() -> None:
    events = [breakdown(100, "T1", 35)]
    plain = Day(example_raw(events))
    plain.advance(102)
    bad_answers = [None, RuntimeError("timed out after 10 s"),
                   {"severity": "catastrophic", "downtime_min": -5, "reason": "", "lesson": ""}]
    for bad in bad_answers:
        day = day_with(ScriptedModel(recovery=[bad]), events)
        day.advance(102)
        assert day.foreman.plan.job_order() == plain.foreman.plan.job_order()
        recovery = [c for c in day.desk.crew.calls if c.agent == "AI Recovery agent"][0]
        assert recovery.status == "fallback" and recovery.applied.startswith("Nothing")
        assert day.memory.log.of("ai_fallback")
        assert day.memory.lessons == []


def test_the_chain_concern_causes_exactly_one_replan_that_passes_the_gate() -> None:
    # The model keeps asking for a re-plan; only the first request may cause one.
    model = ScriptedModel(safety=[CHAIN, CHAIN, CHAIN])
    day = day_with(model, [])
    run = day.foreman
    assert run.plan_no == 2 and run.spread_chain is True
    assert [r["events"] for r in run.replans] == [["jobs_submitted"], ["ai_caution"]]
    assert run.safety["passed"] is True
    # The day plan no longer keeps the tilling and the planting on one tractor.
    assert run.plan.stop_for("J2").tractor != run.plan.stop_for("J3").tractor
    assert model.asked == ["safety", "safety"]  # one review per plan, no third plan
    day.advance(day.farm.horizon_min)
    assert run.sim.all_done()
    check_clean(day)


def test_the_ai_safety_agent_can_hold_a_job_for_thirty_minutes() -> None:
    hold = {"concerns": [{"concern": "Spraying next to the yard looks unsafe right now.",
                          "action": "hold_job", "job": "J5", "caution": None}]}
    day = day_with(ScriptedModel(safety=[hold]), [])
    assert "AI Safety agent" in day.foreman.plan.held["J5"]
    day.advance(31)
    assert "J5" not in day.foreman.plan.held  # the hold ran out and the planner took it back
    day.advance(day.farm.horizon_min)
    assert day.foreman.sim.jobs["J5"].status == "done"
    check_clean(day)


def test_an_answer_for_a_plan_that_was_replaced_is_discarded() -> None:
    import threading

    gate = threading.Event()

    class SlowFirstReview(ScriptedModel):
        """The first review does not answer until the test lets it."""

        def ask(self, system, prompt, tools, schema, timeout_s=AI_TIMEOUT_S) -> ModelReply:
            first = not self.asked
            reply = super().ask(system, prompt, tools, schema, timeout_s)
            if first:
                gate.wait(5)
            return reply

    model = SlowFirstReview(safety=[CHAIN])
    day = Day(example_raw(), ai=AiCrew(model))  # background calls, as on the live server
    assert day.ai_thinking() and day.foreman.plan_no == 1
    day.apply({"type": "set_priority", "job": "J4", "priority": 5})  # plan 2 replaces plan 1
    gate.set()
    for _ in range(200):
        if not day.ai_thinking():
            break
        time.sleep(0.01)
    day.drain_ai()
    first = day.desk.crew.calls[0]
    assert first.plan_no == 1 and "already been replaced" in first.applied
    assert day.foreman.spread_chain is False
    assert day.memory.log.of("ai_stale")


def test_the_clock_runs_at_one_while_an_ai_agent_is_thinking() -> None:
    engine = Engine(EXAMPLE, client=ScriptedModel(delay_s=0.3))
    assert engine.speed == 16 and engine.clock_speed == 1
    state = engine.state()
    assert state["foreman"]["ai"] == {"enabled": True, "thinking": True, "model": "scripted fake",
                                      "estimates": 0, "downtime_gap_min": None}
    assert state["foreman"]["reasoning"][0]["status"] == "thinking"
    time.sleep(0.6)
    engine.tick(0.0)
    assert engine.clock_speed == 16
    done = engine.state()["foreman"]["reasoning"][0]
    assert done["status"] == "done" and done["applied"] == "No concerns. Nothing changed."


def test_the_key_never_reaches_the_log_the_state_or_the_lessons(tmp_path, monkeypatch) -> None:
    secret = "sk-ant-oat01-THIS-IS-A-TEST-SECRET"
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", secret)
    model = ScriptedModel(recovery=[diagnosis(20)], safety=[CHAIN])
    engine = Engine(EXAMPLE, client=model, lessons_path=tmp_path / "lessons.jsonl",
                    log_path=tmp_path / "log.jsonl")
    engine.day.desk.crew.sync = True
    engine.event({"type": "breakdown", "tractor": "T1"})
    engine.advance(200)
    engine.tick(0.0)
    everything = (json.dumps(engine.state()) + json.dumps(engine.learning_report())
                  + json.dumps(engine.report()) + (tmp_path / "log.jsonl").read_text()
                  + ((tmp_path / "lessons.jsonl").read_text() if (tmp_path / "lessons.jsonl").exists() else ""))
    assert secret not in everything and "sk-ant" not in everything


def test_why_and_learning_are_served_by_the_engine() -> None:
    engine = Engine(EXAMPLE)
    answer = engine.why("J2")
    assert answer["chosen"]["tractor"] and len(answer["text"]) == 1 + len(answer["others"])
    report = engine.learning_report()
    assert {"days", "factors", "lessons", "downtime_estimates"} <= report.keys()
    days = engine.run_learning_days(3)
    assert len(days) == 3 and all(day["violations"] == 0 for day in days)
    assert engine.learning_report()["factors"]
