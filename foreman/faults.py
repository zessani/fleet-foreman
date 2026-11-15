"""Sample fault reports: the short text a broken tractor (or its operator) sends in.

The AI Recovery agent reads the report and estimates the downtime. Each sample
also carries the real repair time, which only the simulator uses: the tractor
comes back after that many minutes (None means it is out for the rest of the
day). Comparing the estimate with it is how the AI Recovery agent is measured.
"""

from __future__ import annotations

FAULTS: tuple[dict, ...] = (
    {"report": "Hydraulic hose burst on the implement lift. Oil on the ground. "
               "Mechanic is on site with a spare hose.", "repair_after_min": 35},
    {"report": "Engine overheat warning, coolant temperature in the red. Radiator screen is "
               "packed with chaff.", "repair_after_min": 20},
    {"report": "Rear tyre punctured by a steel post, tyre is flat. Service truck is about "
               "40 minutes away.", "repair_after_min": 90},
    {"report": "GPS receiver lost its RTK fix and will not reacquire. Auto-steer has "
               "disengaged.", "repair_after_min": 15},
    {"report": "PTO shaft sheared at the universal joint. A replacement has to come from the "
               "dealer in town.", "repair_after_min": 240},
    {"report": "Low DEF warning, then the engine derated to idle. The DEF tank is empty.",
     "repair_after_min": 25},
    {"report": "Transmission fault code. The tractor is stuck in neutral and will not select "
               "a gear.", "repair_after_min": None},
    {"report": "Alternator belt snapped, battery light is on. A spare belt is in the yard.",
     "repair_after_min": 45},
)


def fault_sample(index: int) -> dict:
    """The index-th sample, wrapping around. Deterministic so a replay gets the same faults."""
    return dict(FAULTS[index % len(FAULTS)])


# The report for the scripted breakdown of the demo day. That breakdown has no repair
# time, so the simulator keeps the tractor out for the rest of the day, and the report
# says so plainly: no field repair can fix it today. It is kept apart from FAULTS
# because the texts in FAULTS are matched against lessons saved on earlier days.
OUT_ALL_DAY_REPORT = ("Transmission failure: the tractor will not move in any gear and the gearbox "
                      "grinds. The dealer says the gearbox has to come out in their workshop. "
                      "Nothing can be done in the field today.")


def out_all_day_report() -> str:
    """The report for a fault that is not repaired today (real repair time: none, out all day)."""
    return OUT_ALL_DAY_REPORT
