"""The planner's self-tune: candidates, the adopt rule, restoring settings, files, and the engine hook."""

import copy
import json
import time
from pathlib import Path

import pytest

from foreman import evaluator
from foreman.engine import Engine
from foreman.selftune import DEFAULTS, MIN_DAYS, SelfTune, candidates, choose

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "scenarios" / "example_farm.json"


@pytest.fixture(autouse=True)
def keep_weight():
    saved = evaluator.W_WAIT
    yield
    evaluator.W_WAIT = saved


def score(weight: float, on_time: int = 10, wasted: float = 100.0, violations: int = 0) -> dict:
    return {"idle_weight": weight, "on_time": on_time, "wasted": wasted, "done": on_time,
            "violations": violations}


def example_raw() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def filled(days: int, folder: Path | None = None) -> SelfTune:
    tuner = SelfTune(folder)
    for k in range(days):
        tuner.record_day(f"day{k}", example_raw(), [])
    return tuner


def test_candidates_are_around_the_current_value() -> None:
    assert candidates(0.7) == [0.7, 0.5, 0.6, 0.85, 1.0]


def test_candidates_are_clamped_and_unique() -> None:
    low = candidates(0.25)
    assert low[0] == 0.25 and min(low) >= 0.2 and len(low) == len(set(low))
    high = candidates(1.95)
    assert max(high) == 2.0 and len(high) == len(set(high))


def test_fewer_than_min_days_keeps_the_setting() -> None:
    tuner = filled(2)
    record = tuner.tune()
    assert record["decision"] == "kept" and record["tried"] == []
    assert "needs 5 past days" in record["reason"] and "it has 2" in record["sentence"]
    assert tuner.settings() == DEFAULTS and MIN_DAYS == 5


def test_choose_adopts_a_candidate_better_on_both() -> None:
    assert choose(score(0.7), [score(0.5), score(0.85, 12, 90)])["idle_weight"] == 0.85


def test_choose_never_adopts_a_violation() -> None:
    assert choose(score(0.7), [score(0.85, 15, 50, violations=1)]) is None


def test_choose_does_not_trade_on_time_for_wasted_minutes() -> None:
    assert choose(score(0.7), [score(0.85, 9, 50), score(1.0, 11, 150)]) is None


def test_choose_keeps_on_a_tie_or_noise() -> None:
    assert choose(score(0.7), [score(0.85)]) is None
    assert choose(score(0.7), [score(0.85, 10, 99.5)]) is None


def test_choose_breaks_ties_by_wasted_then_closeness() -> None:
    picked = choose(score(0.7), [score(1.0, 12, 80), score(0.85, 12, 80), score(0.5, 12, 70)])
    assert picked["idle_weight"] == 0.5
    picked = choose(score(0.7), [score(1.0, 12, 80), score(0.85, 12, 80)])
    assert picked["idle_weight"] == 0.85


def test_tune_adopts_and_applies_the_value() -> None:
    tuner = filled(MIN_DAYS)
    fake = lambda weight, days: score(weight, 12 if weight == 0.85 else 10, 90 if weight == 0.85 else 100)
    record = tuner.tune(score=fake)
    assert record["decision"] == "adopted" and record["adopted"] == 0.85
    assert "from 0.7 to 0.85" in record["sentence"] and "2 more jobs on time" in record["sentence"]
    assert tuner.settings()["idle_weight"] == 0.85 and evaluator.W_WAIT == 0.85


def test_real_replay_restores_the_weight_when_it_keeps() -> None:
    tuner = filled(MIN_DAYS)
    evaluator.W_WAIT = 0.7
    record = tuner.tune()
    assert len(record["tried"]) == 5 and record["tried"][0]["idle_weight"] == 0.7
    assert all(t["violations"] == 0 for t in record["tried"])
    assert evaluator.W_WAIT == tuner.settings()["idle_weight"]


def test_weight_is_restored_even_if_scoring_fails() -> None:
    tuner = filled(MIN_DAYS)
    evaluator.W_WAIT = 0.7

    def boom(weight, days):
        evaluator.W_WAIT = 9.0
        raise RuntimeError("replay failed")

    with pytest.raises(RuntimeError):
        tuner.tune(score=boom)
    assert evaluator.W_WAIT == 0.7


def test_settings_log_and_days_survive_a_reload(tmp_path: Path) -> None:
    tuner = filled(MIN_DAYS, tmp_path)
    tuner.tune(score=lambda w, d: score(w, 12 if w == 1.0 else 10, 80 if w == 1.0 else 100))
    again = SelfTune(tmp_path)
    assert again.settings()["idle_weight"] == 1.0
    assert again.day_count() == MIN_DAYS
    assert again.latest()["adopted"] == 1.0


def test_reset_returns_to_the_defaults_and_logs_it(tmp_path: Path) -> None:
    tuner = filled(MIN_DAYS, tmp_path)
    tuner.tune(score=lambda w, d: score(w, 12 if w == 1.0 else 10))
    assert tuner.settings()["idle_weight"] == 1.0
    assert tuner.reset() == DEFAULTS and evaluator.W_WAIT == 0.7
    assert tuner.day_count() == MIN_DAYS and tuner.latest()["decision"] == "reset"
    assert SelfTune(tmp_path).settings() == DEFAULTS


def test_engine_records_one_day_and_exposes_the_sentence() -> None:
    tuner = SelfTune(None)
    engine = Engine(EXAMPLE, selftune=tuner)
    assert engine.state()["foreman"]["selftune"] is None
    engine.advance(engine.day.farm.horizon_min)
    engine.advance(10)  # a second call after the end must not record again
    assert tuner.day_count() == 1
    deadline = time.monotonic() + 15
    while engine.state()["foreman"]["selftune"] is None and time.monotonic() < deadline:
        time.sleep(0.05)
    block = engine.state()["foreman"]["selftune"]
    assert block["days"] == 1 and "needs 5 past days" in block["sentence"]
    assert block["decision"] == "kept" and block["current"] == 0.7
    assert tuner.day_count() == 1


def test_engine_without_selftune_has_none_in_the_state() -> None:
    assert Engine(EXAMPLE).state()["foreman"]["selftune"] is None
