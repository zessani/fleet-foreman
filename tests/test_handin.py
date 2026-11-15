import json
from pathlib import Path

import pytest

from foreman.day import Day
from foreman.engine import default_scenario
from foreman.handin import main, test_days_section, write_builtin, write_handin
from foreman.scenarios import busy_day

BUILTIN_FILES = ["scenario.json", "scenario_large_farm.json", "event_log.jsonl",
                 "event_log_large_farm.jsonl", "comparison.md", "comparison.json",
                 "explanations.md", "README.md"]
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
    for name in BUILTIN_FILES:
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
    assert list(data["scenarios"]) == ["sample_farm", "large_farm"]
    for one in data["scenarios"].values():
        assert one["foreman"]["violations"] == 0
        assert one["baseline"]["violations"] == 0
    assert any("breaks down" in event for event in data["scenarios"]["sample_farm"]["events"])


def test_comparison_md_rows(handin):
    out, _, _ = handin
    text = (out / "comparison.md").read_text(encoding="utf-8")
    for row in ("Jobs completed", "Travel time", "Idle time", "## Sample farm", "## Large farm",
                "Large farm: 5 tractors and 25 jobs",
                "Sample farm: 3 tractors, 10 jobs, Cedar fails at 09:30"):
        assert row in text
    assert text.index("## Sample farm") < text.index("## Large farm")


def test_explanations_one_heading_per_plan_change(handin):
    out, _, day = handin
    text = (out / "explanations.md").read_text(encoding="utf-8")
    assert "## Sample farm" in text and "## Large farm" in text
    assert text.count("\n### Plan ") >= len(day.foreman.explanations)
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
    assert (first / "comparison.json").is_file()
    one = json.loads((first / "comparison.json").read_text(encoding="utf-8"))
    two = json.loads((second / "comparison.json").read_text(encoding="utf-8"))
    assert json.dumps(strip_ms(one), sort_keys=True) == json.dumps(strip_ms(two), sort_keys=True)


def test_unknown_scenario_returns_one(tmp_path):
    assert main(["--scenario", "no_such_farm", "--out", str(tmp_path / "x")]) == 1


SAMPLE = Path(__file__).resolve().parent.parent / "scenarios" / "farms" / "sample_farm.json"
BUSY = Path(__file__).resolve().parent.parent / "scenarios" / "busy_day.json"


def sample():
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def test_busy_day_adds_one_job_per_field_and_keeps_originals():
    raw, busy = sample(), busy_day(sample())
    fields = [n["id"] for n in raw["nodes"] if n["kind"] == "field"]
    new = busy["jobs"][len(raw["jobs"]):]
    assert [j["field"] for j in new] == fields
    assert [j["id"] for j in busy["jobs"]][:10] == [j["id"] for j in raw["jobs"]]
    assert [j["id"] for j in new] == [f"J{11 + i}" for i in range(len(fields))]
    for old, kept in zip(raw["jobs"], busy["jobs"]):
        assert {k: v for k, v in old.items() if k != "deadline"} == \
            {k: v for k, v in kept.items() if k != "deadline"}
    for job in new:
        assert job["priority"] == 3 and job["release"] == 0
    for key in ("nodes", "roads", "tractors", "implements", "truth", "events"):
        assert busy[key] == raw[key]
    assert busy["name"] == "Saguaro Flats Farm, busy day"


def test_busy_day_tightens_every_deadline():
    raw, busy = sample(), busy_day(sample())
    latest = max(j["deadline"] for j in raw["jobs"])
    original = raw["jobs"] + [{"deadline": latest, "release": 0}] * 8
    for before, job in zip(original, busy["jobs"]):
        expected = max(int(before["deadline"] * 0.75) // 10 * 10, before.get("release", 0) + 60)
        assert job["deadline"] == expected and job["deadline"] % 10 == 0


def test_busy_day_is_deterministic_and_on_disk():
    assert busy_day(sample()) == busy_day(sample())
    assert json.loads(BUSY.read_text(encoding="utf-8")) == busy_day(sample())


def test_busy_day_passes_the_farm_checker():
    import sys
    sys.path.insert(0, str(SAMPLE.parent.parent.parent / "tools"))
    from check_farm import check_farm
    assert check_farm(busy_day(sample())) == []


def test_scenario_flag_writes_its_own_folder(monkeypatch, tmp_path):
    import foreman.handin as handin_module
    monkeypatch.setattr(handin_module, "DEFAULT_OUT", tmp_path)
    assert main(["--scenario", "busy_day"]) == 0
    for name in FILES:
        assert (tmp_path / "busy_day" / name).is_file()


def test_test_days_section_with_injected_days(tmp_path):
    raw = sample()
    raw["events"] = []
    days = [("sample", "quiet", raw)]
    text, data = test_days_section(days)
    assert "## All test days" in text and "Jobs on time" in text
    assert data["days"] == 1
    out = tmp_path / "out"
    result = write_handin(SAMPLE, out, test_days=True, days=days)
    assert "## All test days" in (out / "comparison.md").read_text(encoding="utf-8")
    assert json.loads((out / "comparison.json").read_text(encoding="utf-8"))["test_days"]["days"] == 1
    assert result["test_days"]["days"] == 1
    both = tmp_path / "both"
    write_builtin(both, test_days=True, days=days)
    text = (both / "comparison.md").read_text(encoding="utf-8")
    assert text.index("## Large farm") < text.index("## All test days")
    for row in ("Jobs on time", "Wasted minutes", "Idle minutes", "Rule violations"):
        assert row in text.split("## All test days")[1]
    saved = json.loads((both / "comparison.json").read_text(encoding="utf-8"))
    assert set(saved) == {"scenarios", "test_days"}
