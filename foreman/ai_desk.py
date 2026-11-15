"""Asks the two AI agents and applies their answers to the Foreman world.

The order is always: the rule-based result is acted on first, then the model's
answer is applied when it arrives. The model never assigns a job. Its answers
only add caution, which the planner and the safety gate then work with:
- AI Recovery agent: an expected downtime. The planner compares waiting for the
  tractor with moving its work, and holds the work only if waiting is cheaper.
- AI Safety agent: concerns about a plan the safety gate already passed. It can
  warn, hold one job for a while, or ask for one re-plan with a named caution.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from foreman.ai_base import AiCall
from foreman.ai_crew import AiCrew
from foreman.ai_recovery import RecoveryCase, diagnose
from foreman.ai_safety import SafetyCase, review
from foreman.model import clock

if TYPE_CHECKING:
    from foreman.day import Day

RECOVERY = "AI Recovery agent"
SAFETY = "AI Safety agent"
EXPIRY_GRACE_MIN = 10.0  # how long past the estimate held work waits before it is moved
HOLD_MIN = 30.0          # how long the AI Safety agent can hold a job
LESSONS_SHOWN = 8
LOG_LINES_SHOWN = 12


class AiDesk:
    def __init__(self, day: "Day", crew: AiCrew) -> None:
        self.day = day
        self.crew = crew
        self._reviewed: set[int] = set()       # plans the AI Safety agent has been asked about
        self._made_for_safety: int | None = None  # the plan made at its request; it cannot ask again on that one
        self._draining = False

    # ---- asking -----------------------------------------------------------

    def after_breakdowns(self, events: list[dict], routes_before: dict[str, list[str]]) -> None:
        """Ask the AI Recovery agent about each breakdown that came with a fault report."""
        for event in events:
            fault = self.day.faults.get(event["tractor"])
            if fault is None or fault.get("asked"):
                continue
            fault["asked"] = True
            case = self._recovery_case(event["tractor"], fault, routes_before)
            placeholder = AiCall(RECOVERY, case.plan_no, case.now, case.clock,
                                 f"{case.tractor_name}: {case.report}")
            self.crew.start("recovery", placeholder,
                            lambda case=case: diagnose(self.crew.client, case, self.crew.timeout_s),
                            {"tractor": event["tractor"], "fault": fault})

    def after_dispatch(self) -> None:
        """Ask the AI Safety agent to look at the plan the safety gate just passed. Once per plan."""
        run = self.day.foreman
        if run.plan is None or run.plan_no in self._reviewed:
            return
        self._reviewed.add(run.plan_no)
        case = self._safety_case()
        planned = sum(len(row["jobs"]) for row in case.plan_summary["tractors"].values())
        placeholder = AiCall(SAFETY, case.plan_no, case.now, case.clock,
                             f"Plan {case.plan_no}: {planned} job(s) planned, "
                             f"{len(case.plan_summary['held'])} held.")
        self.crew.start("safety", placeholder,
                        lambda case=case: review(self.crew.client, case, self.crew.timeout_s),
                        {"plan_no": run.plan_no})

    # ---- applying ---------------------------------------------------------

    def drain(self) -> None:
        """Apply every answer that has arrived. Safe to call at any time."""
        if self._draining:
            return  # an answer being applied caused a re-plan; the outer loop picks up the rest
        self._draining = True
        try:
            while True:
                ready = self.crew.finished()
                if not ready:
                    return
                for kind, call, context in ready:
                    if call.answer is None:
                        call.applied = "Nothing. The rule-based plan stands."
                        self._log(call.agent, "ai_fallback",
                                  f"No usable answer ({call.error or 'no answer'}). "
                                  "The rule-based plan stands.")
                    elif kind == "recovery":
                        self._apply_recovery(call, context["tractor"], context["fault"])
                    else:
                        self._apply_safety(call, context["plan_no"])
        finally:
            self._draining = False

    def _apply_recovery(self, call: AiCall, tractor_id: str, fault: dict) -> None:
        day, run, answer = self.day, self.day.foreman, call.answer
        name = day.farm.tractors[tractor_id].name
        minutes = int(answer["downtime_min"])
        fault["estimate_min"] = minutes
        day.memory.add_lesson({
            "tractor": name, "report": fault.get("report", "")[:160],
            "severity": answer["severity"], "downtime_min": minutes, "lesson": answer["lesson"],
        })
        actual = fault.get("true_downtime")
        if actual is not None:
            day.memory.estimates.append({"tractor": name, "estimate_min": minutes,
                                         "actual_min": actual, "gap_min": abs(minutes - actual)})
        back_at = fault.get("at", day.now) + minutes
        back_clock = clock(day.farm.day_start_min, back_at)
        self._log(RECOVERY, "ai_diagnosis",
                  f"{name}: {answer['severity']}, about {minutes} min. {answer['reason']}",
                  tractor=tractor_id, downtime_min=minutes)

        if tractor_id not in run.watch.down:
            call.applied = f"Saved the lesson. {name} was already back in service."
        elif back_at <= day.now + 1.0:
            call.applied = "Saved the lesson. The estimated time has passed, so the work stays moved."
        else:
            run.returning[tractor_id] = back_at
            day.schedule({"at": back_at + EXPIRY_GRACE_MIN, "type": "_downtime_expired",
                          "tractor": tractor_id})
            day.handle([{"type": "downtime_estimate", "tractor": tractor_id,
                         "headline": f"AI Recovery agent expects {name} back at {back_clock} "
                                     f"({minutes} min)."}])
            waiting = [job for job, reason in run.plan.held.items() if reason.startswith("waiting for")]
            call.applied = (f"The planner held {len(waiting)} job(s) for {name}, expected back "
                            f"{back_clock}." if waiting else
                            f"The planner kept the work moved: waiting until {back_clock} "
                            "would cost more.")
        self._log(RECOVERY, "ai_applied", call.applied, tractor=tractor_id)
        day.note_actual_downtime(tractor_id)  # in case the tractor was back before the answer

    def _apply_safety(self, call: AiCall, plan_no: int) -> None:
        day, run = self.day, self.day.foreman
        concerns = call.answer["concerns"]
        if plan_no != run.plan_no:
            call.applied = f"Not applied: plan {plan_no} had already been replaced."
            self._log(SAFETY, "ai_stale", call.applied)
            return
        if not concerns:
            call.applied = "No concerns. Nothing changed."
            self._log(SAFETY, "ai_review", f"Plan {plan_no}: no concerns.")
            return

        notes, changed = [], False
        for concern in concerns:
            self._log(SAFETY, "ai_concern", f"{concern['action']}: {concern['concern']}",
                      action=concern["action"], job=concern.get("job"))
            if concern["action"] == "hold_job" and self._can_hold(concern["job"]):
                run.ai_holds[concern["job"]] = f"held by the AI Safety agent: {concern['concern'][:90]}"
                day.schedule({"at": day.now + HOLD_MIN, "type": "_hold_release",
                              "job": concern["job"]})
                notes.append(f"held {concern['job']} for {HOLD_MIN:.0f} min")
                changed = True
            elif concern["action"] == "request_replan" and not run.spread_chain:
                run.spread_chain = True
                notes.append("asked the planner to keep dependent jobs on different tractors")
                changed = True
        warnings = sum(1 for concern in concerns if concern["action"] == "warn")
        if warnings:
            notes.append(f"logged {warnings} warning(s)")
        if changed:
            day.handle([{"type": "ai_caution",
                         "headline": "AI Safety agent asked for more caution at "
                                     f"{clock(day.farm.day_start_min, day.now)}."}])
            self._made_for_safety = run.plan_no
            notes.append(f"the safety gate passed the new plan {run.plan_no}"
                         if run.safety.get("passed") else "the new plan did not pass the safety gate")
        call.applied = (", ".join(notes) or "Nothing changed").capitalize() + "."
        self._log(SAFETY, "ai_applied", call.applied)

    def _can_hold(self, job_id: str | None) -> bool:
        state = self.day.foreman.sim.jobs.get(job_id)
        return state is not None and state.status == "pending" and job_id not in self.day.foreman.ai_holds

    # ---- what the agents are given ---------------------------------------

    def _recovery_case(self, tractor_id: str, fault: dict,
                       routes_before: dict[str, list[str]]) -> RecoveryCase:
        day, run, farm = self.day, self.day.foreman, self.day.farm
        at_risk = []
        for job_id in routes_before.get(tractor_id, []):
            job, state = farm.jobs[job_id], run.sim.jobs[job_id]
            if state.status == "done":
                continue
            stop = run.plan.stop_for(job_id) if run.plan else None
            at_risk.append({
                "job": job_id, "label": day.job_label(job_id), "priority": job.priority,
                "deadline": clock(farm.day_start_min, job.deadline),
                "pct_done": round(100.0 * (1.0 - state.remaining_ha / job.area_ha)) if job.area_ha else 100,
                "slack_min": round(job.deadline - stop.finish) if stop else None,
            })
        return RecoveryCase(
            tractor_id=tractor_id, tractor_name=farm.tractors[tractor_id].name,
            report=fault["report"], clock=clock(farm.day_start_min, day.now),
            plan_no=run.plan_no, now=day.now,
            tractor_status={row["id"]: {key: row[key] for key in
                                        ("name", "status", "job", "fuel_pct", "mounted")}
                            for row in run.block()["tractors"]},
            jobs_at_risk=at_risk,
            past_lessons=day.memory.relevant_lessons(fault["report"]),
        )

    def _safety_case(self) -> SafetyCase:
        day, run, farm = self.day, self.day.foreman, self.day.farm
        at = lambda minute: clock(farm.day_start_min, minute)  # noqa: E731
        tractors = {}
        for tractor_id, route in run.plan.routes.items():
            tractors[tractor_id] = {
                "name": farm.tractors[tractor_id].name,
                "jobs": [{"job": stop.job, "label": day.job_label(stop.job),
                          "start": at(stop.start), "finish": at(stop.finish),
                          "deadline": at(farm.jobs[stop.job].deadline),
                          "late_min": round(stop.late_min), "after": farm.jobs[stop.job].after}
                         for stop in route],
            }
        history = {}
        for tractor_id, tractor in run.sim.tractors.items():
            history[tractor_id] = {
                "name": farm.tractors[tractor_id].name,
                "jobs_done_today": sum(1 for state in run.sim.jobs.values()
                                       if state.status == "done" and state.tractor == tractor_id),
                "breakdowns_today": sum(1 for incident in day.memory.incidents
                                        if incident["tractor"] == tractor_id),
                "minutes_worked": round(tractor.minutes["work"]),
            }
        open_jobs = [job_id for job_id, state in run.sim.jobs.items() if state.status != "done"]
        return SafetyCase(
            plan_no=run.plan_no, clock=at(day.now), now=day.now,
            plan_summary={"tractors": tractors, "held": dict(run.plan.held),
                          "out_of_service": [farm.tractors[t].name for t in sorted(run.watch.down)]},
            tractor_history=history,
            recent_log=[f"{line['clock']} {line['agent']}: {line['message']}"
                        for line in day.memory.log.lines(LOG_LINES_SHOWN)],
            past_lessons=day.memory.lessons[-LESSONS_SHOWN:],
            job_ids=open_jobs,
            may_request_replan=(run.plan_no != self._made_for_safety and not run.spread_chain),
        )

    def _log(self, agent: str, event: str, message: str, **data) -> None:
        self.day.memory.log.append(self.day.now, agent, event, message, ai=True, **data)
