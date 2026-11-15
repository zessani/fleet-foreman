"""AI Safety agent: reviews a plan after the rule-based safety gate passed it.

It looks for risks the nine rules do not cover. It can only add caution.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from foreman.ai_base import AI_TIMEOUT_S, AiCall, ModelClient, ModelReply, Tool

AGENT = "AI Safety agent"
ACTIONS = ("warn", "hold_job", "request_replan")
CAUTIONS = ("spread_dependent_jobs",)  # the only caution the planner understands
MAX_CONCERNS = 5
MAX_TEXT = 200

SYSTEM = """You are the AI Safety agent in Foreman, a system that coordinates a small fleet of autonomous farm tractors.
A rule-based safety gate has already passed this plan against nine fixed rules. Your only job is to look for risks those rules do not cover.

Fixed principles:
- The model never assigns jobs to tractors. A rule-based planner does that.
- You cannot approve a plan the safety gate rejected, and you cannot change or remove a rule. You can only add caution.
- Answer only with the JSON asked for. No other text.
- Be brief.
- Base every statement on the data from the tools. If the data does not show a problem, there is no concern.

Use the tools to read the plan, each tractor's history today, the recent log, and lessons from the past.
You can also look up the concerns you raised on earlier plans and what happened after them. A concern that was followed by a clean day is weaker evidence, and one that was followed by the problem it named is stronger. This memory changes nothing about your limits: it can only add caution and cannot approve anything."""

SCHEMA: dict = {
    "type": "object",
    "properties": {
        "concerns": {
            "type": "array",
            "maxItems": MAX_CONCERNS,
            "items": {
                "type": "object",
                "properties": {
                    "concern": {"type": "string"},
                    "action": {"type": "string", "enum": list(ACTIONS)},
                    "job": {"type": ["string", "null"]},
                    "caution": {"type": ["string", "null"]},
                },
                "required": ["concern", "action", "job", "caution"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["concerns"],
    "additionalProperties": False,
}


@dataclass
class SafetyCase:
    plan_no: int
    clock: str
    now: float
    plan_summary: dict
    tractor_history: dict
    recent_log: list[str]
    past_lessons: list[dict]
    job_ids: list[str]
    may_request_replan: bool
    past_concerns: list[dict] = field(default_factory=list)


def _tools(case: SafetyCase) -> list[Tool]:
    # Deep copies: the tools are a frozen snapshot the model cannot use to change anything.
    return [
        Tool("plan_summary", "The plan: each tractor's jobs with times and dependencies, held jobs, tractors out of service.",
             copy.deepcopy(case.plan_summary)),
        Tool("tractor_history", "Jobs done, breakdowns and minutes worked today. Pass the tractor id.",
             copy.deepcopy(case.tractor_history), select_by="tractor"),
        Tool("recent_log", "The latest log lines, newest last.", list(case.recent_log)),
        Tool("past_lessons", "Lessons from earlier incidents.", copy.deepcopy(case.past_lessons)),
        Tool("past_concerns", "Warnings and holds you raised on earlier plans, with what Foreman did and what "
             "happened afterwards. Use them to judge whether a similar concern is worth raising again.",
             copy.deepcopy(case.past_concerns)),
    ]


def _prompt(case: SafetyCase) -> str:
    replan_rule = (
        "You may use request_replan." if case.may_request_replan
        else "You may NOT use request_replan this time: this plan was already made at your request. "
             "Use warn or hold_job."
    )
    return (
        f"Review plan {case.plan_no} made at {case.clock}. The nine rules have already passed it.\n\n"
        "For each concern, choose one action:\n"
        "- warn: just tell the operator.\n"
        "- hold_job: hold one named job back for now. Only for a job that looks unsafe to start.\n"
        "- request_replan: ask the planner to plan again with one named caution. The only caution the "
        "planner understands is spread_dependent_jobs. Use it when ONE tractor holds a job AND the job "
        "that depends on it (the after field), so that a single breakdown of that tractor would stall "
        "the whole chain. Name it only when another tractor could take part of the chain.\n"
        f"{replan_rule}\n\n"
        "Rules for the answer:\n"
        "- An empty list is the right answer when the plan looks fine. Do not invent concerns.\n"
        "- At most 3 concerns.\n"
        "- Do not repeat what the nine rules already check: implements, fuel, one machine per field, "
        "order, roads and deadlines.\n"
        "- For hold_job give the job id in job. For request_replan give the caution. Otherwise use null."
    )


def _input_line(case: SafetyCase) -> str:
    tractors = case.plan_summary.get("tractors", {})
    jobs = sum(len(t.get("jobs", [])) for t in tractors.values())
    held = len(case.plan_summary.get("held", {}))
    return f"{jobs} jobs on {len(tractors)} tractors, {held} held"


def _check_concern(raw: Any, case: SafetyCase, replan_used: bool) -> tuple[dict | None, bool]:
    """Return the checked concern (None if unusable) and whether a replan request is now used."""
    if not isinstance(raw, dict):
        return None, replan_used
    text = raw.get("concern")
    if not isinstance(text, str) or not text.strip():
        return None, replan_used
    action, job, caution = raw.get("action"), raw.get("job"), raw.get("caution")
    if action not in ACTIONS:
        return None, replan_used
    if action == "hold_job" and job not in case.job_ids:
        action = "warn"
    if action == "request_replan":
        if caution in CAUTIONS and case.may_request_replan and not replan_used:
            replan_used = True
        else:
            action = "warn"
    checked = {
        "concern": text.strip()[:MAX_TEXT],
        "action": action,
        "job": job if action == "hold_job" else None,
        "caution": caution if action == "request_replan" else None,
    }
    return checked, replan_used


def check_answer(answer: Any, case: SafetyCase) -> tuple[dict | None, str | None]:
    """Return ({"concerns": [...]}, None) or (None, what was wrong)."""
    if not isinstance(answer, dict) or not isinstance(answer.get("concerns"), list):
        return None, "concerns must be a list"
    concerns: list[dict] = []
    replan_used = False
    for raw in answer["concerns"][:MAX_CONCERNS]:
        checked, replan_used = _check_concern(raw, case, replan_used)
        if checked is not None:
            concerns.append(checked)
    return {"concerns": concerns}, None


def review(client: ModelClient, case: SafetyCase, timeout_s: float = AI_TIMEOUT_S) -> AiCall:
    """Ask the model to review the plan. Never raises; a bad answer leaves status "fallback"."""
    call = AiCall(agent=AGENT, plan_no=case.plan_no, at=case.now, clock=case.clock,
                  input=_input_line(case))
    try:
        reply = client.ask(SYSTEM, _prompt(case), _tools(case), SCHEMA, timeout_s)
    except Exception as exc:  # the client should not raise, but the rules must carry on if it does
        reply = ModelReply(ok=False, error=f"client error: {exc}")
    if not reply.ok:
        call.finish(reply, None, reply.error or "model call failed")
        return call
    answer, error = check_answer(reply.answer, case)
    call.finish(reply, answer, error)
    return call
