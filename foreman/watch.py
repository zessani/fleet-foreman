"""The tractor status feed reader: the recovery monitor's tool.

It only looks at what the tractors report through TractorAdapter.status().
A failure is either a reported fault, or a tractor that says it is working
but has made no progress for STALL_MIN minutes.
"""

from __future__ import annotations

STALL_MIN = 5.0     # minutes of "working" with no progress before it counts as a failure
PROGRESS_EPS = 1e-6


class StatusWatch:
    def __init__(self) -> None:
        self.down: dict[str, dict] = {}  # tractor id -> {cause, at}
        self._last: dict[str, dict] = {}  # last status seen per tractor
        self._progress: dict[str, tuple[str, float, float]] = {}  # tractor -> (job, pct, since)

    def poll(self, now: float, statuses: dict[str, dict]) -> list[dict]:
        """Compare this reading with the last one. Returns breakdown and repaired events."""
        events: list[dict] = []
        for tractor_id in sorted(statuses):
            status = statuses[tractor_id]
            if tractor_id in self.down:
                if status["state"] != "broken":
                    del self.down[tractor_id]
                    events.append({"type": "repaired", "tractor": tractor_id})
            elif status["state"] == "broken":
                events.append(self._failure(tractor_id, "fault", now))
            elif self._stalled(tractor_id, status, now):
                events.append(self._failure(tractor_id, "stall", now))
            if status["state"] != "broken":
                self._last[tractor_id] = status
        return events

    def _stalled(self, tractor_id: str, status: dict, now: float) -> bool:
        if status["state"] != "working":
            self._progress.pop(tractor_id, None)
            return False
        seen = self._progress.get(tractor_id)
        if seen is None or seen[0] != status["job"] or status["pct"] > seen[1] + PROGRESS_EPS:
            self._progress[tractor_id] = (status["job"], status["pct"], now)
            return False
        return now - seen[2] >= STALL_MIN

    def _failure(self, tractor_id: str, cause: str, now: float) -> dict:
        # What it was doing comes from the last healthy reading: a broken
        # tractor no longer reports a job.
        before = self._last.get(tractor_id, {})
        self.down[tractor_id] = {"cause": cause, "at": now}
        self._progress.pop(tractor_id, None)
        return {
            "type": "breakdown", "tractor": tractor_id, "cause": cause,
            "state": before.get("state", "idle"),
            "job": before.get("job") if before.get("state") == "working" else None,
        }
