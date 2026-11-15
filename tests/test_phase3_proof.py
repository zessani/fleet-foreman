"""Fast checks for Ahmed's Phase 3 random-farm proof runner."""

import json
from pathlib import Path

from foreman.day import Day
from tests.proof_run import farm_for_run, run_proof

ROOT = Path(__file__).resolve().parent.parent


def test_proof_farms_are_distinct_and_reproducible() -> None:
    first = farm_for_run(0, 9000)
    again = farm_for_run(0, 9000)
    second = farm_for_run(1, 9000)
    assert first == again
    assert first["name"] != second["name"]
    assert first["events"][0]["type"] == "breakdown"


def test_proof_reports_required_metrics() -> None:
    report = run_proof(count=3, seed=9100)
    assert report["random_farms"] == 3
    assert report["jobs"] > 0
    assert report["foreman"]["violations"] == 0
    assert {"travel_min", "idle_min", "violations"} <= report["foreman"].keys()
    assert {"mean_ms", "p95_ms", "max_ms"} <= report["replan"].keys()
    assert report["replan"]["dispatches"] >= 3


def sample_day() -> Day:
    raw = json.loads(
        (ROOT / "scenarios" / "farms" / "sample_farm.json").read_text(encoding="utf-8")
    )
    raw["events"] = []
    return Day(raw)


def test_blocked_road_replans_without_using_the_closed_edge() -> None:
    day = sample_day()
    a, b = next(
        pair for pair in day.farm.raw["roads"]
        if any(pair[0] in stop.path and pair[1] in stop.path for stop in day.foreman.plan.stops())
    )
    previous_plan = day.foreman.plan_no
    assert day.apply({"type": "block_road", "a": a, "b": b})["ok"]
    assert day.foreman.plan_no > previous_plan
    assert frozenset((a, b)) in day.blocked
    for stop in day.foreman.plan.stops():
        assert frozenset((a, b)) not in {
            frozenset(edge) for edge in zip(stop.path, stop.path[1:])
        }
    assert day.foreman.safety["passed"]

    assert day.apply({"type": "open_road", "a": a, "b": b})["ok"]
    assert frozenset((a, b)) not in day.blocked


def test_changed_priority_replans_and_is_visible_in_dashboard_state() -> None:
    day = sample_day()
    previous_plan = day.foreman.plan_no
    assert day.apply({"type": "set_priority", "job": "J4", "priority": 5})["ok"]
    assert day.foreman.plan_no > previous_plan
    assert day.farm.jobs["J4"].priority == 5
    row = next(job for job in day.foreman.block()["jobs"] if job["id"] == "J4")
    assert row["priority"] == 5
    assert day.foreman.safety["passed"]


def test_dashboard_exposes_phase3_event_controls_and_plan_status() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="road-select"' in html and 'id="road-button"' in html
    assert 'id="priority-job"' in html and 'id="priority-button"' in html
    assert '{ type: opening ? "open_road" : "block_road", a, b }' in html
    assert 'postEvent({ type: "set_priority", job, priority }' in html
    assert 'id="summary-safety"' in html
    assert 'id="explanation-headline"' in html
