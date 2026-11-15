import json
from pathlib import Path

import pytest

from foreman.day import Day
from foreman.engine import default_scenario
from foreman.handin import main, write_handin

FILES = ["scenario.json", "event_log.jsonl", "comparison.md", "comparison.json",
         "explanations.md", "README.md"]


@pytest.fixture(scope="module")
def handin(tmp_path_factory):
    out = tmp_path_factory.mktemp("handin")
    assert main(["--out", str(out)]) == 0
    raw = json.loads(default_scenario().read_text(encoding="utf-8"))
    day = Day(raw, use_truth=False)
    day.advance(day.farm.horizon_min)
    return out, raw, day


def test_writes_all_six_files(handin):
    out, _, _ = handin
    for name in FILES:
        assert (out / name).is_file()


def test_scenario_is_the_loaded_farm_file(handin):
    out, raw, _ = handin
    assert json.loads((out / "scenario.json").read_text(encoding="utf-8")) == raw


def test_event_log_lines_parse_and_match(handin):
    out, _, day = handin
    lines = (out / "event_log.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(day.memory.log.lines())
    for line in lines:
        json.loads(line)


def test_comparison_json(handin):
    out, _, _ = handin
    data = json.loads((out / "comparison.json").read_text(encoding="utf-8"))
    assert data["foreman"]["violations"] == 0
    assert data["baseline"]["violations"] == 0
    assert any("breaks down" in event for event in data["events"])


def test_comparison_md_rows(handin):
    out, _, _ = handin
    text = (out / "comparison.md").read_text(encoding="utf-8")
    for row in ("Jobs completed", "Travel time", "Idle time"):
        assert row in text


def test_explanations_one_heading_per_plan_change(handin):
    out, _, day = handin
    text = (out / "explanations.md").read_text(encoding="utf-8")
    assert text.count("\n## Plan ") == len(day.foreman.explanations)
    breakdown = next(e for e in day.foreman.explanations if "went down" in e["headline"])
    assert breakdown["headline"] in text


def strip_ms(value):
    if isinstance(value, dict):
        return {k: strip_ms(v) for k, v in value.items() if k != "ms"}
    if isinstance(value, list):
        return [strip_ms(v) for v in value]
    return value


def test_two_runs_are_identical_apart_from_timings(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    write_handin(default_scenario(), first)
    write_handin(default_scenario(), second)
    one = json.loads((first / "comparison.json").read_text(encoding="utf-8"))
    two = json.loads((second / "comparison.json").read_text(encoding="utf-8"))
    assert json.dumps(strip_ms(one), sort_keys=True) == json.dumps(strip_ms(two), sort_keys=True)


def test_unknown_scenario_returns_one(tmp_path):
    assert main(["--scenario", "no_such_farm", "--out", str(tmp_path / "x")]) == 1
