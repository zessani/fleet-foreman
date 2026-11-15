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
    def __init__(self, day_start_min: float, log_path: Path | None = None,
                 lessons_path: Path | None = None) -> None:
        self.log = EventLog(day_start_min, log_path)
        self.incidents: list[dict] = []  # one entry per breakdown, with a one-line lesson
        self.estimates: list[dict] = []  # AI Recovery agent downtime estimates against the real repair time
        self._lessons_path = lessons_path
        self.lessons: list[dict] = self._load_lessons()  # written by the AI agents, kept across days

    def _load_lessons(self) -> list[dict]:
        if self._lessons_path is None or not self._lessons_path.exists():
            return []
        lessons = []
        for line in self._lessons_path.read_text(encoding="utf-8").splitlines():
            try:
                lessons.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a damaged line must not stop the day
        return lessons

    def add_lesson(self, lesson: dict) -> None:
        self.lessons.append(lesson)
        if self._lessons_path is not None:
            self._lessons_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._lessons_path, "a", encoding="utf-8") as lessons_file:
                lessons_file.write(json.dumps(lesson) + "\n")

    def downtime_gap(self) -> float | None:
        """Average minutes between the AI Recovery agent's estimate and the real repair time."""
        gaps = [entry["gap_min"] for entry in self.estimates]
        return round(sum(gaps) / len(gaps), 1) if gaps else None
