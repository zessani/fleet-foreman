"""AI Recovery agent: reads a broken tractor's fault report and estimates how bad it is.

The model only estimates. The rules still decide what happens to the jobs.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from foreman.ai_base import AI_TIMEOUT_S, AiCall, ModelClient, ModelReply, Tool

AGENT = "AI Recovery agent"
SEVERITIES = ("minor", "moderate", "severe")
MAX_DOWNTIME_MIN = 720
MAX_TEXT = 200

SYSTEM = """You are the AI Recovery agent in Foreman, a system that coordinates a small fleet of autonomous farm tractors.
A tractor has reported a fault. Your only job is to estimate how bad it is.

Fixed principles:
- The model never assigns jobs to tractors. A rule-based planner does that.
- You cannot approve a plan the safety gate rejected, and you cannot change or remove a safety rule. You can only add caution.
- Answer only with the JSON asked for. No other text.
- Be brief.
- Base every statement on the data from the tools. If the data does not say, do not guess about it.

Use the tools to read the fleet status, the jobs this tractor had, and lessons from past faults."""

SCHEMA: dict = {
    "type": "object",
    "properties": {
        "severity": {"type": "string", "enum": list(SEVERITIES)},
        "downtime_min": {"type": "integer"},
        "out_for_day": {"type": "boolean"},
        "reason": {"type": "string"},
        "lesson": {"type": "string"},
    },
    "required": ["severity", "downtime_min", "out_for_day", "reason", "lesson"],
    "additionalProperties": False,
}


@dataclass
class RecoveryCase:
    tractor_id: str
    tractor_name: str
    report: str
    clock: str
    plan_no: int
    now: float
    tractor_status: dict
    jobs_at_risk: list[dict]
    past_lessons: list[dict]
    day_ends_in_min: float | None = None  # minutes from the fault to the end of the working day


def _tools(case: RecoveryCase) -> list[Tool]:
    # Deep copies: the tools are a frozen snapshot the model cannot use to change anything.
    return [
        Tool("tractor_status", "State, job, fuel and mounted implement of each tractor. Pass the tractor id.",
             copy.deepcopy(case.tractor_status), select_by="tractor"),
        Tool("jobs_at_risk", "The jobs this tractor had, with priority, deadline, progress and slack.",
             copy.deepcopy(case.jobs_at_risk)),
        Tool("past_lessons", "Lessons from earlier faults: report, downtime and what was learned. "
             "actual_downtime_min in a lesson is a measured repair time for that kind of fault: it outweighs "
             "any earlier estimate (downtime_min, estimated_min). For the same kind of fault, start your "
             "estimate from it and move only if this report clearly differs. still_down_after_min means the "
             "tractor was still out of service that many minutes after the fault, so the real downtime "
             "was longer than that; not_repaired_that_day means it never came back that day. "
             "estimated_out_for_day means the answer then was out for the day: with not_repaired_that_day "
             "that answer proved right, with actual_downtime_min the tractor came back that soon.",
             copy.deepcopy(case.past_lessons)),
    ]


def _day_left(case: RecoveryCase) -> str:
    if case.day_ends_in_min is None:
        return ""
    return f"The working day ends {round(case.day_ends_in_min)} min from now.\n"


def _prompt(case: RecoveryCase) -> str:
    return (
        f"Tractor {case.tractor_name} reported a fault at {case.clock}.\n"
        f"Fault report: {case.report}\n"
        f"{_day_left(case)}\n"
        "Give me:\n"
        "- severity: minor, moderate or severe.\n"
        "- downtime_min: expected minutes until the tractor can work again, a realistic field-repair "
        "estimate. If a past lesson has actual_downtime_min for the same kind of fault, that is a measured "
        "repair time and it outweighs any earlier estimate, including your own: start from it and move "
        "only if this report clearly differs (for example a part has to be fetched, or a mechanic is "
        "already on site). If a past lesson has still_down_after_min for the same kind of fault, an "
        "earlier estimate was too short: the real downtime was longer than that number, and with "
        "not_repaired_that_day it lasted the rest of the day. Otherwise use the lessons you have.\n"
        "- out_for_day: true if the tractor will not work again today, for example when the repair "
        "needs a workshop or a part that cannot arrive today. Then downtime_min means at least the rest "
        "of the working day. An estimate that reaches past the end of the working day counts as out "
        "for the day.\n"
        "- reason: one short line.\n"
        "- lesson: one sentence about this kind of fault that will be useful next time."
    )


def _trim(text: str) -> str:
    return text.strip()[:MAX_TEXT]


def _whole_minutes(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def check_answer(answer: Any) -> tuple[dict | None, str | None]:
    """Return (checked answer, None) or (None, what was wrong) so the caller falls back to the rules."""
    if not isinstance(answer, dict):
        return None, "answer is not an object"
    severity = answer.get("severity")
    if severity not in SEVERITIES:
        return None, f"severity must be one of {', '.join(SEVERITIES)}"
    downtime = _whole_minutes(answer.get("downtime_min"))
    if downtime is None:
        return None, "downtime_min must be a whole number"
    if not 0 <= downtime <= MAX_DOWNTIME_MIN:
        return None, f"downtime_min must be between 0 and {MAX_DOWNTIME_MIN}"
    out_for_day = answer.get("out_for_day", False)  # an answer without it is a plain estimate
    if not isinstance(out_for_day, bool):
        return None, "out_for_day must be true or false"
    if out_for_day and severity == "minor":
        return None, "a minor fault cannot keep the tractor out for the day"
    texts = {}
    for key in ("reason", "lesson"):
        value = answer.get(key)
        if not isinstance(value, str) or not value.strip():
            return None, f"{key} must be a non-empty string"
        texts[key] = _trim(value)
    return {"severity": severity, "downtime_min": downtime, "out_for_day": out_for_day, **texts}, None


def diagnose(client: ModelClient, case: RecoveryCase, timeout_s: float = AI_TIMEOUT_S) -> AiCall:
    """Ask the model about the fault. Never raises; a bad answer leaves status "fallback"."""
    call = AiCall(agent=AGENT, plan_no=case.plan_no, at=case.now, clock=case.clock,
                  input=f"{case.tractor_name}: {case.report}")
    try:
        reply = client.ask(SYSTEM, _prompt(case), _tools(case), SCHEMA, timeout_s)
    except Exception as exc:  # the client should not raise, but the rules must carry on if it does
        reply = ModelReply(ok=False, error=f"client error: {exc}")
    if not reply.ok:
        call.finish(reply, None, reply.error or "model call failed")
        return call
    answer, error = check_answer(reply.answer)
    call.finish(reply, answer, error)
    return call
