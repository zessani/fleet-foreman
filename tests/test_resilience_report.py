"""The on/off switch for the resilience step and the report's shape."""

from foreman import planner
from foreman.resilience_report import compare, random_days, step_enabled, summarise
from foreman.sweep import test_days


def test_switch_turns_step_off_and_restores_it():
    original = planner.improve_within_tolerance
    with step_enabled(False):
        assert planner.improve_within_tolerance is not original
        assert planner.improve_within_tolerance().changed is False
    assert planner.improve_within_tolerance is original
    with step_enabled(True):
        assert planner.improve_within_tolerance is original


def test_report_on_two_days_has_expected_keys():
    days = test_days()[:1] + random_days(1)[:1]
    summary = summarise(compare(days))
    assert summary["days"] == 2
    for key in ("changed", "better", "same", "worse", "plans_changed", "differing", "totals"):
        assert key in summary
    assert set(summary["totals"]) == {"on", "off"}
