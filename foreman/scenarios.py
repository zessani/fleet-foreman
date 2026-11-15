"""Make the busy day from the sample farm. One written rule, applied once.

The rule:
- Keep the farm, tractors, implements, roads, hidden factors and the scripted
  breakdown exactly as they are.
- Keep the ten original jobs and add one more job on every field, in the order
  the fields appear in the file. Its type is the next type after that field's
  last existing job in the cycle spray, mow, till, plant, spray, skipping a type
  the field already has. A new plant job goes after that field's till job if the
  field has one, otherwise it has no job before it. New job ids continue the
  numbering (J11, J12, ...). New jobs get priority 3, release 0 and the farm's
  latest existing deadline.
- Then tighten every deadline, old and new: multiply it by 0.75 and round down
  to a multiple of 10 minutes, but never earlier than the job's release plus
  60 minutes.

Run "python -m foreman.scenarios" to write scenarios/busy_day.json.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "scenarios" / "farms" / "sample_farm.json"
TARGET = ROOT / "scenarios" / "busy_day.json"
CYCLE = ["spray", "mow", "till", "plant"]


def next_type(existing: list[str]) -> str:
    """The next type in the cycle after the last one, skipping types the field has."""
    index = CYCLE.index(existing[-1])
    for step in range(1, len(CYCLE) + 1):
        candidate = CYCLE[(index + step) % len(CYCLE)]
        if candidate not in existing:
            return candidate
    return CYCLE[(index + 1) % len(CYCLE)]


def tighten(deadline: int, release: int) -> int:
    return max(int(deadline * 0.75) // 10 * 10, release + 60)


def busy_day(raw: dict) -> dict:
    farm = copy.deepcopy(raw)
    farm["name"] = "Saguaro Flats Farm, busy day"
    jobs = farm["jobs"]
    latest = max(job["deadline"] for job in jobs)
    number = len(jobs)
    for node in farm["nodes"]:
        if node.get("kind") != "field":
            continue
        mine = [job for job in jobs if job["field"] == node["id"]]
        if not mine:
            continue
        kind = next_type([job["type"] for job in mine])
        after = None
        if kind == "plant":
            till = next((job["id"] for job in mine if job["type"] == "till"), None)
            after = till
        number += 1
        jobs.append({"id": f"J{number}", "type": kind, "field": node["id"], "priority": 3,
                     "deadline": latest, "release": 0, "after": after})
    for job in jobs:
        job["deadline"] = tighten(job["deadline"], job.get("release", 0))
    return farm


def main() -> None:
    raw = json.loads(SOURCE.read_text(encoding="utf-8"))
    TARGET.write_text(json.dumps(busy_day(raw), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {TARGET}")


if __name__ == "__main__":
    main()
