import copy
import json
from pathlib import Path

import pytest

from foreman.learning import Learning, chart_days, chart_truth, run_days
from foreman.model import farm_from_dict, truth_factor

ROOT = Path(__file__).resolve().parent.parent
TRUTH = {"*|mow|F3": 0.7, "*|till|F4": 0.8, "T2|spray|*": 1.15}


def load(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def example(truth=TRUTH) -> dict:
    raw = copy.deepcopy(load("scenarios/example_farm.json"))
    raw["truth"] = dict(truth)
    return raw


@pytest.fixture(scope="module")
def trained():
    learning = Learning()
    results = run_days(example(), 5, learning)
    return learning, results


def test_estimate_error_falls(trained):
    _, results = trained
    gaps = [r["gap_pct"] for r in results]
    print("gaps", gaps)
    assert gaps[-1] < gaps[0] * 0.5 and gaps[-1] < 5


def test_learned_factors_match_hidden(trained):
    learning, _ = trained
    farm = farm_from_dict(example())
    assert learning.summary()["factors"]
    for row in learning.summary()["factors"]:
        hidden = truth_factor(farm, row["tractor"], farm.implements[row["implement"]].type, row["field"])
        learned = learning.factor(row["tractor"], row["implement"], row["field"])
        print(row["tractor"], row["implement"], row["field"], learned, hidden)
        assert abs(learned - hidden) / hidden < 0.05


def test_survives_save_and_reload(tmp_path):
    path = tmp_path / "learned.json"
    learning = Learning(path)
    run_days(example(), 2, learning)
    again = Learning(path)
    assert again.summary() == learning.summary()
    for row in learning.summary()["factors"]:
        key = (row["tractor"], row["implement"], row["field"])
        assert again.factor(*key) == learning.factor(*key)
    assert again.factor("T9", "x", "F9") == 1.0


@pytest.mark.parametrize("name", ["scenarios/example_farm.json", "scenarios/farms/sample_farm.json"])
def test_learning_days_are_safe_and_complete(name):
    raw = load(name)
    if "example" in name:
        raw["truth"] = dict(TRUTH)
    jobs = len(raw["jobs"])
    for result in run_days(raw, 3, Learning()):
        assert result["violations"] == 0
        assert result["jobs_done"] == jobs


def test_no_hidden_factors_stays_at_one():
    learning = Learning()
    results = run_days(example({}), 3, learning)
    assert all(r["gap_pct"] < 3 for r in results)
    assert all(abs(row["factor"] - 1) <= 0.02 for row in learning.summary()["factors"])


def sample(tractor, implement, field, hectares, minutes):
    return {"tractor": tractor, "implement": implement, "field": field,
            "hectares": hectares, "minutes": minutes}


def one_farm():
    farm = farm_from_dict(example())
    implement = next(iter(farm.implements))
    return farm, implement, farm.implements[implement].ha_per_hour


def test_fallback_order_and_default():
    farm, imp, rate = one_farm()
    learning = Learning()
    assert learning.factor("T1", imp, "F1") == 1.0
    learning.record(farm, sample("T1", imp, "F1", 1.0, 60 / rate / 0.8))  # factor 0.8
    assert learning.factor("T1", imp, "F1") == pytest.approx(0.8)
    assert learning.factor("T2", imp, "F1") == pytest.approx(0.8)  # same implement and field
    assert learning.factor("T1", imp, "F2") == pytest.approx(0.8)  # same tractor and implement
    assert learning.factor("T2", imp, "F2") == 1.0


def test_clamping_and_tiny_samples_ignored():
    farm, imp, rate = one_farm()
    learning = Learning()
    learning.record(farm, sample("T1", imp, "F1", 1.0, 60 / rate / 5))
    assert learning.factor("T1", imp, "F1") == 1.6
    learning.record(farm, sample("T1", imp, "F2", 1.0, 0.4))
    learning.record(farm, sample("T1", imp, "F2", 0.0, 30))
    assert learning.summary()["factors"][-1]["field"] == "F1"
    assert len(learning.summary()["factors"]) == 1


def test_corrupt_file_starts_empty(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    learning = Learning(path)
    assert learning.summary() == {"days": [], "factors": [], "days_run": 0}
    path.write_text('{"sums": 3}', encoding="utf-8")
    assert Learning(path).summary()["factors"] == []


def sample_farm() -> dict:
    return load("scenarios/farms/sample_farm.json")


def test_chart_is_repeatable():
    assert chart_days(sample_farm()) == chart_days(sample_farm())


def test_chart_factors_are_10_to_25_percent_off():
    factors = chart_truth(sample_farm())
    assert factors
    assert all(0.10 <= abs(f - 1) <= 0.25 + 1e-9 for f in factors.values())


def test_chart_curve_shape():
    days = chart_days(sample_farm())["days"]
    assert days[0]["gap_pct"] > 8
    assert 0 < days[-1]["gap_pct"] < days[0]["gap_pct"] / 2
    assert all(d["violations"] == 0 for d in days)
    assert all(d["jobs_done"] == len(sample_farm()["jobs"]) for d in days)


def test_chart_days_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    raw = sample_farm()
    before = copy.deepcopy(raw)
    existing = Learning()
    sums, history = copy.deepcopy(existing.sums), copy.deepcopy(existing.history)
    chart_days(raw)
    assert raw == before
    assert (existing.sums, existing.history) == (sums, history)
    assert list(tmp_path.iterdir()) == []
