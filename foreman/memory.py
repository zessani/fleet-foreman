"""The shared memory of the roles and the AI agents (CLAUDE.md section 6).

Three kinds:
- world state: lives in the simulation and is read through Run.world()
- event log: every decision and its reason, append only, saved as JSON Lines
- learned facts: incidents and measured field speeds, kept across days
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

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


def _key_words(text: str) -> set[str]:
    """The words of a fault report that say what broke. Filler words of three letters or
    fewer are dropped; short ones stay when they are capitals or hold a digit (GPS, RTK, PTO, DEF)."""
    cleaned = "".join(c if c.isalnum() else " " for c in text)
    return {word.lower() for word in cleaned.split()
            if len(word) > 3 or (len(word) >= 2 and (word.isupper() or any(c.isdigit() for c in word)))}


def _same_report(text: str) -> str:
    return " ".join(text.lower().split())


class Memory:
    def __init__(self, day_start_min: float, log_path: Path | None = None,
                 lessons_path: Path | None = None) -> None:
        self.log = EventLog(day_start_min, log_path)
        self.incidents: list[dict] = []  # one entry per breakdown, with a one-line lesson
        self.estimates: list[dict] = []  # AI Recovery agent downtime estimates against the real repair time
        self._lessons_path = lessons_path
        self.lessons: list[dict] = self._load_lessons()  # written by the AI agents, kept across days
        self._concerns_path = (lessons_path.with_name("safety_concerns.jsonl")
                               if lessons_path is not None else None)
        self.concerns: list[dict] = self._load_concerns()  # raised by the AI Safety agent, kept across days
        self._open_concerns: list[dict] = []  # raised today, outcome not known yet

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

    def _load_concerns(self) -> list[dict]:
        if self._concerns_path is None or not self._concerns_path.exists():
            return []
        concerns = []
        for line in self._concerns_path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # a damaged line must not stop the day
            if isinstance(record, dict):
                concerns.append(record)
        return concerns

    def add_concern(self, record: dict) -> dict:
        """Keep a concern raised today. It is written to the file once its outcome is known."""
        self.concerns.append(record)
        self._open_concerns.append(record)
        return record

    def close_concerns(self, outcome_for: Callable[[dict], str]) -> None:
        """Give every concern raised today its outcome and save it."""
        for record in self._open_concerns:
            if record.get("outcome"):
                continue
            record["outcome"] = outcome_for(record)
            if self._concerns_path is not None:
                self._concerns_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._concerns_path, "a", encoding="utf-8") as concerns_file:
                    concerns_file.write(json.dumps(record) + "\n")
        self._open_concerns = []

    def past_concerns(self, limit: int = 8) -> list[dict]:
        """The latest concerns whose story is finished, newest first."""
        finished = [record for record in self.concerns if record.get("outcome")]
        return list(reversed(finished))[:limit]

    def add_lesson(self, lesson: dict) -> None:
        self.lessons.append(lesson)
        if self._lessons_path is not None:
            self._lessons_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._lessons_path, "a", encoding="utf-8") as lessons_file:
                lessons_file.write(json.dumps(lesson) + "\n")

    def relevant_lessons(self, report: str, limit: int = 6) -> list[dict]:
        """The past lessons most like this fault report. A lesson with a measured repair time
        comes before one that only carries a guess, and a guess is dropped when a measured
        lesson exists for the same report text (the measurement supersedes it)."""
        words = _key_words(report)
        measured = {_same_report(item.get("report", "")) for item in self.lessons
                    if "actual_downtime_min" in item}
        candidates = [(index, lesson) for index, lesson in enumerate(self.lessons)
                      if "actual_downtime_min" in lesson
                      or _same_report(lesson.get("report", "")) not in measured]

        def score(item: tuple[int, dict]) -> tuple[int, int, int]:
            index, lesson = item
            shared = len(words & _key_words(lesson.get("report", "")))
            return (1 if "actual_downtime_min" in lesson else 0, shared, index)

        ranked = sorted(candidates, key=score, reverse=True)
        return [lesson for index, lesson in ranked
                if words & _key_words(lesson.get("report", ""))][:limit]

    def downtime_gap(self) -> float | None:
        """Average minutes between the AI Recovery agent's estimate and the real repair time."""
        gaps = [entry["gap_min"] for entry in self.estimates]
        return round(sum(gaps) / len(gaps), 1) if gaps else None
