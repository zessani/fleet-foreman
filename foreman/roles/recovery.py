"""recovery monitor: watches the tractors, spots a failure, records the lesson.

Tool: the tractor status feed (foreman/watch.py).
"""

from __future__ import annotations

from foreman.roles.base import Cycle, Role
from foreman.watch import STALL_MIN


class RecoveryMonitor(Role):
    name = "Recovery monitor"

    def watch(self) -> list[dict]:
        """Read the status feed. Returns the breakdown and repaired events it found."""
        events = self.run.watch.poll(self.run.now, self.run.statuses())
        for event in events:
            if event["type"] == "breakdown":
                self._report_failure(event)
        return events

    def _report_failure(self, event: dict) -> None:
        tractor = self.run.sim.tractors[event["tractor"]]
        name = self.tractor_name(event["tractor"])
        if event["cause"] == "stall":
            # It still claims to be working. Take it out of service so it gets no more work.
            tractor.break_down()
            seen = f"{name} reported working but made no progress for {STALL_MIN:.0f} min"
        else:
            seen = f"{name} reported a fault"
        delay = self.run.now - tractor.fault_at if tractor.fault_at is not None else 0.0
        event["detection_min"] = round(delay, 2)
        self.say("failure_detected", f"{seen}. Detected {delay:.1f} min after it happened.",
                 tractor=event["tractor"], cause=event["cause"], detection_min=round(delay, 2))

    def on_event(self, cycle: Cycle, event: dict) -> None:
        if event["type"] == "breakdown":
            self._record_incident(event)
        elif event["type"] == "repaired":
            self.say("back_in_service", f"{self.tractor_name(event['tractor'])} is back in service.")
        elif event["type"] == "drift":
            self.say("drift", f"{self.job_label(event['job'])} finished {event['behind_min']:.0f} "
                              "min behind plan. Re-planning the rest of the day from where "
                              "things really are.", job=event["job"],
                     behind_min=event["behind_min"])

    def _record_incident(self, event: dict) -> None:
        tractor_id, job_id = event["tractor"], event.get("job")
        name = self.tractor_name(tractor_id)
        queued = [stop.job for stop in (self.run.plan.routes.get(tractor_id, []) if self.run.plan else [])
                  if self.run.sim.jobs[stop.job].status != "done" and stop.job != job_id]
        parts = [f"{name} is out of service."]
        if job_id:
            job, state = self.farm.jobs[job_id], self.run.sim.jobs[job_id]
            left = round(100.0 * state.remaining_ha / job.area_ha) if job.area_ha else 0
            parts.append(f"{self.job_label(job_id)} stopped with {left}% left; only that "
                         "remainder is re-planned.")
        if queued:
            parts.append(f"{len(queued)} queued job(s) need a new tractor.")
        parts.append("Finished work is frozen.")
        self.say("incident", " ".join(parts), tractor=tractor_id, job=job_id, queued=queued)
        self.memory.incidents.append({
            "t": round(self.run.now, 2), "tractor": tractor_id, "cause": event.get("cause"),
            "job": job_id, "state": event.get("state"),
            "lesson": f"{name} failed while {event.get('state', 'idle')} at minute "
                      f"{self.run.now:.0f}.",
        })

    def note_progress(self, sim_events: list[dict]) -> None:
        """job_started and job_done from the tractors. Logged; no re-plan."""
        for event in sim_events:
            if event["type"] not in ("job_started", "job_done"):
                continue
            name, label = self.tractor_name(event["tractor"]), self.job_label(event["job"])
            verb = "started" if event["type"] == "job_started" else "finished"
            self.memory.log.append(event["t"], self.name, event["type"], f"{name} {verb}: {label}.")
