"""The planner's self-tune: candidates, the adopt rule, restoring settings, files, and the engine hook."""

import copy
import json
import threading
import time
from pathlib import Path

import pytest

from foreman import evaluator, planner, safety
from foreman.engine import Engine
from foreman.selftune import (DEFAULTS, MIN_DAYS, MIN_HELD_OUT, MIN_TUNING, TUNABLE, SelfTune, candidate_settings,
                              candidates, choose, split_days, trial)

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "scenarios" / "example_farm.json"


@pytest.fixture(autouse=True)
def keep_weight():
    saved = (evaluator.W_WAIT, planner.CHANGE_STRICT, planner.CHANGE_LIGHT)
    yield
    evaluator.W_WAIT, planner.CHANGE_STRICT, planner.CHANGE_LIGHT = saved


def settings_with(**changes: float) -> dict:
    return {**DEFAULTS, **changes}


def score(weight: float | dict, on_time: int = 10, wasted: float = 100.0, violations: int = 0) -> dict:
    settings = weight if isinstance(weight, dict) else settings_with(idle_weight=weight)
    return {"settings": settings, "on_time": on_time, "wasted": wasted, "done": on_time,
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
    assert "needs 6 past days" in record["reason"] and "it has 2" in record["sentence"]
    assert tuner.settings() == DEFAULTS and MIN_DAYS == 6


def test_choose_adopts_a_candidate_better_on_both() -> None:
    assert choose(score(0.7), [score(0.5), score(0.85, 12, 90)])["settings"]["idle_weight"] == 0.85


def test_choose_never_adopts_a_violation() -> None:
    assert choose(score(0.7), [score(0.85, 15, 50, violations=1)]) is None


def test_choose_does_not_trade_on_time_for_wasted_minutes() -> None:
    assert choose(score(0.7), [score(0.85, 9, 50), score(1.0, 11, 150)]) is None


def test_choose_keeps_on_a_tie_or_noise() -> None:
    assert choose(score(0.7), [score(0.85)]) is None
    assert choose(score(0.7), [score(0.85, 10, 99.5)]) is None


def test_choose_breaks_ties_by_wasted_then_closeness() -> None:
    picked = choose(score(0.7), [score(1.0, 12, 80), score(0.85, 12, 80), score(0.5, 12, 70)])
    assert picked["settings"]["idle_weight"] == 0.5
    picked = choose(score(0.7), [score(1.0, 12, 80), score(0.85, 12, 80)])
    assert picked["settings"]["idle_weight"] == 0.85


def test_tune_adopts_and_applies_the_value() -> None:
    tuner = filled(MIN_DAYS)
    fake = lambda s, days: score(s, 12 if s["idle_weight"] == 0.85 else 10, 90 if s["idle_weight"] == 0.85 else 100)
    record = tuner.tune(score=fake)
    assert record["decision"] == "adopted" and record["adopted"]["idle_weight"] == 0.85
    assert "from 0.7 to 0.85" in record["sentence"] and "2 more jobs on time" in record["sentence"]
    assert tuner.settings()["idle_weight"] == 0.85 and evaluator.W_WAIT == 0.85


def test_real_replay_restores_the_weight_when_it_keeps() -> None:
    tuner = filled(MIN_DAYS)
    evaluator.W_WAIT = 0.7
    record = tuner.tune()
    assert len(record["tried"]) == len(candidate_settings(DEFAULTS)) <= 10
    assert record["tried"][0]["settings"] == DEFAULTS
    assert all(t["violations"] == 0 for t in record["tried"])
    assert evaluator.W_WAIT == tuner.settings()["idle_weight"]


def test_weight_is_restored_even_if_scoring_fails() -> None:
    tuner = filled(MIN_DAYS)
    evaluator.W_WAIT = 0.7

    def boom(settings, days):
        evaluator.W_WAIT = 9.0
        raise RuntimeError("replay failed")

    with pytest.raises(RuntimeError):
        tuner.tune(score=boom)
    assert evaluator.W_WAIT == 0.7


def test_settings_log_and_days_survive_a_reload(tmp_path: Path) -> None:
    tuner = filled(MIN_DAYS, tmp_path)
    tuner.tune(score=lambda s, d: score(s, 12 if s["idle_weight"] == 1.0 else 10, 80 if s["idle_weight"] == 1.0 else 100))
    again = SelfTune(tmp_path)
    assert again.settings()["idle_weight"] == 1.0
    assert again.day_count() == MIN_DAYS
    assert again.latest()["adopted"]["idle_weight"] == 1.0


def test_reset_returns_to_the_defaults_and_logs_it(tmp_path: Path) -> None:
    tuner = filled(MIN_DAYS, tmp_path)
    tuner.tune(score=lambda s, d: score(s, 12 if s["idle_weight"] == 1.0 else 10))
    assert tuner.settings()["idle_weight"] == 1.0
    assert tuner.reset() == DEFAULTS and evaluator.W_WAIT == 0.7 and planner.CHANGE_STRICT == 500.0
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
    assert block["days"] == 1 and "needs 6 past days" in block["sentence"]
    assert block["decision"] == "kept" and block["current"] == DEFAULTS
    assert tuner.day_count() == 1


def test_engine_without_selftune_has_none_in_the_state() -> None:
    assert Engine(EXAMPLE).state()["foreman"]["selftune"] is None


# more settings

def stability_winner(name: str, value: float):
    return lambda s, d: score(s, 12 if s[name] == value else 10, 90 if s[name] == value else 100)


def test_allow_list_has_exactly_the_three_settings() -> None:
    assert set(TUNABLE) == {"idle_weight", "stability_strict", "stability_light"}
    assert DEFAULTS == {"idle_weight": 0.7, "stability_strict": planner.CHANGE_STRICT,
                        "stability_light": planner.CHANGE_LIGHT}


def test_safety_margins_are_never_tunable() -> None:
    forbidden = {"FUEL_MARGIN", "FUEL_RESERVE", "REFUEL_BELOW", "SWAP_MIN", "REFUEL_MIN", "W_UNPLANNED",
                 "W_LATE_JOB", "W_LATE_MIN"}
    names = {name.upper() for name in TUNABLE}
    assert not names & forbidden
    assert not any(hasattr(safety, name) for name in TUNABLE)


@pytest.mark.parametrize("bad", ["FUEL_MARGIN", "fuel_reserve", "W_UNPLANNED", "swap_min"])
def test_settings_file_naming_anything_else_is_refused(tmp_path: Path, bad: str) -> None:
    (tmp_path / "settings.json").write_text(json.dumps({"idle_weight": 0.7, bad: 0.1}), encoding="utf-8")
    with pytest.raises(ValueError, match="allow-list"):
        SelfTune(tmp_path)


def test_settings_file_out_of_range_is_refused(tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text(json.dumps({"stability_strict": 0}), encoding="utf-8")
    with pytest.raises(ValueError, match="between"):
        SelfTune(tmp_path)


def test_old_settings_file_with_only_the_idle_weight_loads(tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text(json.dumps({"idle_weight": 1.0}), encoding="utf-8")
    assert SelfTune(tmp_path).settings() == settings_with(idle_weight=1.0)


def test_candidates_change_one_setting_at_a_time_and_stay_in_range() -> None:
    for current in (DEFAULTS, settings_with(stability_strict=110.0, stability_light=58.0, idle_weight=1.95)):
        found = candidate_settings(current)
        assert found[0] == current and len(found) <= 10
        assert len({json.dumps(c, sort_keys=True) for c in found}) == len(found)
        for candidate in found[1:]:
            assert sum(candidate[n] != current[n] for n in TUNABLE) == 1
        for candidate in found:
            for name, value in candidate.items():
                low, high = TUNABLE[name]["range"]
                assert low <= value <= high


def test_a_stability_trial_is_seen_by_its_own_thread_only() -> None:
    seen: dict = {}

    def other_thread() -> None:
        seen["other"] = (planner.stability_cost(True), planner.stability_cost(False))

    with trial(settings_with(stability_strict=350.0, stability_light=25.0)):
        seen["mine"] = (planner.stability_cost(True), planner.stability_cost(False))
        worker = threading.Thread(target=other_thread)
        worker.start()
        worker.join()
    assert seen["mine"] == (350.0, 25.0)
    assert seen["other"] == (500.0, 15.0)
    assert (planner.stability_cost(True), planner.stability_cost(False)) == (500.0, 15.0)


def test_a_kept_tune_leaves_every_constant_at_the_adopted_value() -> None:
    tuner = filled(MIN_DAYS)

    def messy(settings, days):
        evaluator.W_WAIT, planner.CHANGE_STRICT, planner.CHANGE_LIGHT = 9.0, 9.0, 9.0
        return score(settings)

    record = tuner.tune(score=messy)
    assert record["decision"] == "kept"
    assert (evaluator.W_WAIT, planner.CHANGE_STRICT, planner.CHANGE_LIGHT) == (0.7, 500.0, 15.0)


def test_adopting_a_stability_setting_applies_it_and_names_it() -> None:
    tuner = filled(MIN_DAYS)
    record = tuner.tune(score=stability_winner("stability_strict", 350.0))
    assert record["decision"] == "adopted" and record["changed"] == "stability_strict"
    assert record["adopted"] == settings_with(stability_strict=350.0)
    assert "stability cost after a breakdown from 500 to 350" in record["sentence"]
    assert "2 more jobs on time" in record["sentence"] and "10% fewer wasted minutes" in record["sentence"]
    assert planner.CHANGE_STRICT == 350.0 and planner.CHANGE_LIGHT == 15.0 and evaluator.W_WAIT == 0.7
    assert record["tried"][0]["settings"] == DEFAULTS


def test_reset_returns_stability_settings_to_the_defaults() -> None:
    tuner = filled(MIN_DAYS)
    tuner.tune(score=stability_winner("stability_light", 5.0))
    assert planner.CHANGE_LIGHT == 5.0
    assert tuner.reset() == DEFAULTS and planner.CHANGE_LIGHT == 15.0


# the held-out check

def split_scorer(tuning_days: int, tuning_result: tuple, held_result: tuple, name: str = "idle_weight",
                 value: float = 1.0):
    """Fake scores. The current settings get (10, 100). The winner gets one result on the tuning
    days and another on the held-out days, told apart by how many days it was given."""
    calls: list = []

    def fake(settings: dict, days: list) -> dict:
        calls.append((dict(settings), len(days)))
        if settings == DEFAULTS or settings[name] != value:
            return score(settings, 10, 100.0)
        on_time, wasted = tuning_result if len(days) == tuning_days else held_result
        return score(settings, on_time, wasted)

    fake.calls = calls
    return fake


def test_split_is_repeatable_and_never_overlaps() -> None:
    for count in range(0, 15):
        tuning, held = split_days(count)
        assert (tuning, held) == split_days(count)
        assert not set(tuning) & set(held) and sorted(tuning + held) == list(range(count))
    assert split_days(9) == ([0, 1, 3, 4, 6, 7], [2, 5, 8])


def test_minimum_history_leaves_enough_days_on_both_sides() -> None:
    tuning, held = split_days(MIN_DAYS)
    assert (len(tuning), len(held)) == (4, 2) and MIN_DAYS == 6
    assert len(held) >= MIN_HELD_OUT and len(tuning) >= MIN_TUNING
    assert len(split_days(MIN_DAYS - 1)[1]) < MIN_HELD_OUT


def test_too_few_days_adopts_nothing_and_says_how_many_more() -> None:
    tuner = filled(MIN_DAYS - 1)
    record = tuner.tune(score=split_scorer(4, (12, 80.0), (12, 80.0)))
    assert record["decision"] == "kept" and record["tried"] == [] and record["tuning"] is None
    assert "it has 5, so it needs 1 more" in record["sentence"] and "4 to tune on, 2 held out" in record["sentence"]
    assert tuner.settings() == DEFAULTS


def test_a_tuning_winner_that_loses_on_held_out_days_is_not_adopted() -> None:
    tuner = filled(MIN_DAYS)
    record = tuner.tune(score=split_scorer(4, (12, 80.0), (10, 103.0)))
    assert record["decision"] == "kept" and record["adopted"] is None
    assert tuner.settings() == DEFAULTS and evaluator.W_WAIT == 0.7
    assert "did not hold on the 2 held-out days (3% more wasted minutes)" in record["sentence"]
    assert "every setting stays as it was" in record["sentence"]
    assert record["rejected"]["idle_weight"] == 1.0


def test_a_tuning_winner_that_loses_jobs_on_held_out_days_is_not_adopted() -> None:
    tuner = filled(MIN_DAYS)
    record = tuner.tune(score=split_scorer(4, (12, 80.0), (9, 50.0)))
    assert record["decision"] == "kept" and "1 fewer job on time" in record["sentence"]


def test_a_tuning_winner_that_only_ties_on_held_out_days_is_not_adopted() -> None:
    tuner = filled(MIN_DAYS)
    for tie in ((10, 100.0), (10, 99.5)):
        record = tuner.tune(score=split_scorer(4, (12, 80.0), tie))
        assert record["decision"] == "kept" and "no clear gain" in record["sentence"]


def test_a_tuning_winner_with_a_held_out_violation_is_not_adopted() -> None:
    tuner = filled(MIN_DAYS)
    base = split_scorer(4, (12, 80.0), (12, 80.0))

    def fake(settings: dict, days: list) -> dict:
        if settings["idle_weight"] == 1.0 and len(days) == 2:
            return score(settings, 15, 10.0, violations=1)
        return base(settings, days)

    record = tuner.tune(score=fake)
    assert record["decision"] == "kept" and "1 rule violations" in record["sentence"]


def test_a_winner_that_also_wins_on_held_out_days_is_adopted() -> None:
    tuner = filled(MIN_DAYS)
    record = tuner.tune(score=split_scorer(4, (12, 84.0), (10, 89.0)))
    assert record["decision"] == "adopted" and record["adopted"]["idle_weight"] == 1.0
    assert tuner.settings()["idle_weight"] == 1.0 and evaluator.W_WAIT == 1.0
    assert record["sentence"].startswith("Self-tune tried 9 settings on 4 past days and checked the best one on "
                                         "2 days it had not tuned on.")
    assert "2 more jobs on time, 16% fewer wasted minutes on the tuning days" in record["sentence"]
    assert "11% fewer wasted minutes on the held-out days" in record["sentence"]
    assert "no fewer jobs on time, zero rule violations" in record["sentence"]


def test_the_record_carries_both_score_blocks() -> None:
    tuner = filled(MIN_DAYS)
    record = tuner.tune(score=split_scorer(4, (12, 84.0), (10, 89.0)))
    for block, days, winner in (("tuning", 4, (12, 84.0)), ("held_out", 2, (10, 89.0))):
        assert record[block]["days"] == days
        assert record[block]["current"]["settings"] == DEFAULTS
        assert (record[block]["winner"]["on_time"], record[block]["winner"]["wasted"]) == winner
        for who in ("current", "winner"):
            assert {"on_time", "wasted", "violations"} <= set(record[block][who])


def test_no_tuning_winner_means_the_held_out_days_are_never_touched() -> None:
    tuner = filled(MIN_DAYS)
    fake = split_scorer(4, (10, 100.0), (10, 100.0))
    record = tuner.tune(score=fake)
    assert record["decision"] == "kept" and record["held_out"] is None
    assert all(days == 4 for _, days in fake.calls)


def test_only_the_single_winner_is_scored_on_the_held_out_days() -> None:
    tuner = filled(MIN_DAYS)
    fake = split_scorer(4, (12, 84.0), (10, 89.0))
    tuner.tune(score=fake)
    on_held = [settings for settings, days in fake.calls if days == 2]
    assert len(on_held) == 2 and on_held[0] == DEFAULTS and on_held[1]["idle_weight"] == 1.0
    assert sum(1 for _, days in fake.calls if days == 4) == len(candidate_settings(DEFAULTS))


def test_the_state_block_keys_are_unchanged() -> None:
    engine = Engine(EXAMPLE, selftune=SelfTune(None))
    engine.advance(engine.day.farm.horizon_min)
    deadline = time.monotonic() + 15
    while engine.state()["foreman"]["selftune"] is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert set(engine.state()["foreman"]["selftune"]) == {"sentence", "decision", "current", "days"}
