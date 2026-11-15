"""Fake dashboard state in the exact shape of CLAUDE.md section 8c.

Phase 0 only: this lets the map and the panels be built before the planner and
simulation exist. It is a frozen snapshot of scenarios/example_farm.json at
09:35, the moment Bolt (T2) breaks down and the plan is repaired. Phase 1
replaces this module with the real simulation state.

Shapes chosen here for the parts section 8c leaves open:
- job status:           pending | active | done | held
- tractor status:       idle | traveling | servicing | working | waiting | broken
- explanation.changes:  list of plain sentences, one per changed job
- safety.checks:        {id, name, passed, severity: hard | warning, detail}
- log line:             {t, clock, agent, event, message}
"""

import json
from pathlib import Path

EXAMPLE_FARM = Path(__file__).resolve().parent.parent / "scenarios" / "example_farm.json"

FAKE_NOW_MIN = 215.0


def load_farm(path: Path = EXAMPLE_FARM) -> dict:
    with open(path, encoding="utf-8") as farm_file:
        return json.load(farm_file)


def clock(day_start_min: float, now_min: float) -> str:
    """Turn minutes from the start of the working day into "HH:MM"."""
    total = int(day_start_min + now_min)
    return f"{total // 60 % 24:02d}:{total % 60:02d}"


def fake_state() -> dict:
    farm = load_farm()
    return {
        "now": FAKE_NOW_MIN,
        "clock": clock(farm["day_start_min"], FAKE_NOW_MIN),
        "running": True,
        "speed": 4,
        "farm": {
            "name": farm["name"],
            "nodes": farm["nodes"],
            "roads": farm["roads"],
            "blocked": [],
        },
        "foreman": _FOREMAN,
        "baseline": _BASELINE,
    }


_FOREMAN: dict = {
    "tractors": [
        {"id": "T1", "name": "Atlas", "x": 0.0, "y": 0.0, "status": "servicing",
         "job": "J4", "fuel_pct": 80, "mounted": "disc_6", "color": "#E8A33D"},
        {"id": "T2", "name": "Bolt", "x": 2.8, "y": -0.6, "status": "broken",
         "job": None, "fuel_pct": 71, "mounted": "mower_4", "color": "#4C9BE8"},
        {"id": "T3", "name": "Cedar", "x": -1.5, "y": 1.2, "status": "working",
         "job": "J3", "fuel_pct": 87, "mounted": "planter_6", "color": "#5DBB63"},
    ],
    "jobs": [
        {"id": "J1", "type": "spray", "field": "F1", "priority": 5,
         "deadline": 240, "pct": 100, "status": "done", "tractor": "T2"},
        {"id": "J2", "type": "till", "field": "F4", "priority": 3,
         "deadline": 300, "pct": 100, "status": "done", "tractor": "T1"},
        {"id": "J3", "type": "plant", "field": "F4", "priority": 4,
         "deadline": 600, "pct": 48, "status": "active", "tractor": "T3"},
        {"id": "J4", "type": "mow", "field": "F3", "priority": 2,
         "deadline": 480, "pct": 25, "status": "pending", "tractor": "T1"},
        {"id": "J5", "type": "spray", "field": "F2", "priority": 4,
         "deadline": 360, "pct": 100, "status": "done", "tractor": "T2"},
    ],
    "plan": [
        {"job": "J1", "tractor": "T2", "implement": "sprayer_18",
         "start": 5, "finish": 61, "late_min": 0, "path": ["YARD", "X1", "F1"]},
        {"job": "J5", "tractor": "T2", "implement": "sprayer_18",
         "start": 68, "finish": 110, "late_min": 0, "path": ["F1", "X1", "F2"]},
        {"job": "J2", "tractor": "T1", "implement": "disc_6",
         "start": 4, "finish": 135, "late_min": 0, "path": ["YARD", "F4"]},
        {"job": "J3", "tractor": "T3", "implement": "planter_6",
         "start": 135, "finish": 302, "late_min": 0, "path": ["YARD", "F4"]},
        {"job": "J4", "tractor": "T1", "implement": "mower_4",
         "start": 248, "finish": 437, "late_min": 0, "path": ["YARD", "X2", "F3"]},
    ],
    "totals": {"jobs_done": 3, "on_time": 3, "travel_min": 39, "idle_min": 18,
               "working_share": 81.4, "km": 20.2, "violations": 0},
    "explanation": {
        "headline": "Bolt went down at 09:35 while mowing River Flat (25% done).",
        "summary": "1 job moved, 0 retimed, 4 unchanged. All deadlines are still met.",
        "changes": [
            "Mow River Flat: the remaining 75% moves from Bolt to Atlas. "
            "Finishes 13:17, 43 min before its 14:00 deadline.",
        ],
        "ms": 38,
    },
    "safety": {
        "passed": True,
        "checks": [
            {"id": 1, "name": "One tractor per job, no finished job rescheduled",
             "passed": True, "severity": "hard", "detail": "5 jobs, 5 assignments"},
            {"id": 2, "name": "Right implement, owned, enough power",
             "passed": True, "severity": "hard", "detail": "Atlas (180 hp) pulls mower_4 (needs 90 hp)"},
            {"id": 3, "name": "Fuel stays above the 10% reserve",
             "passed": True, "severity": "hard", "detail": "Lowest planned level: Atlas 51%"},
            {"id": 4, "name": "One machine per field at a time",
             "passed": True, "severity": "hard", "detail": "No overlapping field visits"},
            {"id": 5, "name": "Jobs in a field follow their order",
             "passed": True, "severity": "hard", "detail": "J3 starts after J2 finishes"},
            {"id": 6, "name": "No route uses a closed road",
             "passed": True, "severity": "hard", "detail": "0 roads closed"},
            {"id": 7, "name": "Only working tractors get jobs",
             "passed": True, "severity": "hard", "detail": "Bolt has no jobs"},
            {"id": 8, "name": "No work lost or repeated",
             "passed": True, "severity": "hard", "detail": "J4 remainder (75%) planned once"},
            {"id": 9, "name": "Deadlines met",
             "passed": True, "severity": "warning", "detail": "0 jobs late"},
        ],
    },
    "log": [
        {"t": 215.0, "clock": "09:35", "agent": "Recovery", "event": "breakdown",
         "message": "Bolt reported broken while mowing River Flat at 25%."},
        {"t": 215.0, "clock": "09:35", "agent": "Orchestrator", "event": "route",
         "message": "breakdown: Recovery, Implement, Fleet, Navigation, Safety, dispatch."},
        {"t": 215.0, "clock": "09:35", "agent": "Implement", "event": "implement_chosen",
         "message": "J4 mow: Atlas can pull mower_4. Cedar has no mower."},
        {"t": 215.0, "clock": "09:35", "agent": "Fleet", "event": "plan",
         "message": "J4 remainder (15 ha) moved from Bolt to Atlas."},
        {"t": 215.0, "clock": "09:35", "agent": "Navigation", "event": "route_set",
         "message": "Atlas: swap at Yard, then YARD, X2, F3 (3.7 km)."},
        {"t": 215.0, "clock": "09:35", "agent": "Safety", "event": "plan_passed",
         "message": "Safety Agent passed 9 of 9 checks."},
        {"t": 215.0, "clock": "09:35", "agent": "Orchestrator", "event": "dispatch",
         "message": "Plan sent to Atlas and Cedar. Re-plan took 38 ms."},
    ],
}

# First come, first served on the same farm with the same breakdown.
_BASELINE: dict = {
    "tractors": [
        {"id": "T1", "name": "Atlas", "x": -1.5, "y": 1.2, "status": "working",
         "job": "J3", "fuel_pct": 74, "mounted": "planter_6", "color": "#E8A33D"},
        {"id": "T2", "name": "Bolt", "x": 2.8, "y": -0.6, "status": "broken",
         "job": None, "fuel_pct": 78, "mounted": "mower_4", "color": "#4C9BE8"},
        {"id": "T3", "name": "Cedar", "x": 0.0, "y": 0.0, "status": "idle",
         "job": None, "fuel_pct": 90, "mounted": "sprayer_18", "color": "#5DBB63"},
    ],
    "jobs": [
        {"id": "J1", "type": "spray", "field": "F1", "priority": 5,
         "deadline": 240, "pct": 100, "status": "done", "tractor": "T2"},
        {"id": "J2", "type": "till", "field": "F4", "priority": 3,
         "deadline": 300, "pct": 100, "status": "done", "tractor": "T1"},
        {"id": "J3", "type": "plant", "field": "F4", "priority": 4,
         "deadline": 600, "pct": 22, "status": "active", "tractor": "T1"},
        {"id": "J4", "type": "mow", "field": "F3", "priority": 2,
         "deadline": 480, "pct": 12, "status": "pending", "tractor": "T1"},
        {"id": "J5", "type": "spray", "field": "F2", "priority": 4,
         "deadline": 360, "pct": 100, "status": "done", "tractor": "T3"},
    ],
    "plan": [
        {"job": "J1", "tractor": "T2", "implement": "sprayer_18",
         "start": 5, "finish": 61, "late_min": 0, "path": ["YARD", "X1", "F1"]},
        {"job": "J2", "tractor": "T1", "implement": "disc_6",
         "start": 4, "finish": 135, "late_min": 0, "path": ["YARD", "F4"]},
        {"job": "J3", "tractor": "T1", "implement": "planter_6",
         "start": 173, "finish": 340, "late_min": 0, "path": ["YARD", "F4"]},
        {"job": "J4", "tractor": "T1", "implement": "mower_4",
         "start": 381, "finish": 603, "late_min": 123, "path": ["YARD", "X2", "F3"]},
        {"job": "J5", "tractor": "T3", "implement": "sprayer_18",
         "start": 93, "finish": 135, "late_min": 0, "path": ["YARD", "X1", "F2"]},
    ],
    "totals": {"jobs_done": 3, "on_time": 3, "travel_min": 58, "idle_min": 147,
               "working_share": 62.9, "km": 31.6, "violations": 0},
}
