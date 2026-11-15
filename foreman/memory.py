"""The shared memory of the agents (CLAUDE.md section 6).

Three kinds:
- world state: lives in the simulation and is read through Run.world()
- event log: every decision and its reason, append only, saved as JSON Lines
- learned facts: incidents and measured field speeds, kept across days
"""

from __future__ import annotations

import json
from pathlib import Path

from foreman.model import clock


class EventLog:
    """Append-only log. Each line is {t, clock, agent, event, message}."""

    def __init__(self, day_start_min: float, path: Path | None = None) -> None:
        self._day_start_min = day_start_min
        self._lines: list[dict] = []
        self._path = path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")  # a new day starts a new file

    def append(self, t: float, agent: str, event: str, message: str, **data) -> dict:
        line = {"t": round(t, 2), "clock": clock(self._day_start_min, t),
                "agent": agent, "event": event, "message": message}
        if data:
            line["data"] = data
        self._lines.append(line)
        if self._path is not None:
            with open(self._path, "a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(line) + "\n")
        return line

    def lines(self, last: int | None = None) -> list[dict]:
        return list(self._lines if last is None else self._lines[-last:])

    def of(self, event: str) -> list[dict]:
        return [line for line in self._lines if line["event"] == event]


class Memory:
    def __init__(self, day_start_min: float, log_path: Path | None = None) -> None:
        self.log = EventLog(day_start_min, log_path)
        self.incidents: list[dict] = []  # one entry per breakdown, with a one-line lesson
