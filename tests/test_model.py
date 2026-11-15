import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from foreman.model import (  # noqa: E402
    clock,
    farm_from_dict,
    load_farm,
    pick_implement,
    travel_minutes,
    truth_factor,
    work_minutes,
)

EXAMPLE = ROOT / "scenarios" / "example_farm.json"


@pytest.fixture
def farm():
    return load_farm(str(EXAMPLE))


@pytest.fixture
def raw_farm():
    import json

    with open(EXAMPLE, encoding="utf-8") as farm_file:
        return json.load(farm_file)


def test_example_farm_counts_and_depot(farm):
    # 3 tractors, 5 jobs, 4 implements, 7 nodes (YARD, X1, X2, F1-F4), depot YARD
    assert len(farm.tractors) == 3
    assert len(farm.jobs) == 5
    assert len(farm.implements) == 4
    assert len(farm.nodes) == 7
    assert farm.depot == "YARD"


def test_example_farm_job_fields(farm):
    # J3 is submitted with after "J2"
    assert farm.jobs["J3"].after == "J2"
    # J1 has no area_ha in the file, so it takes F1's 16 ha
    assert farm.jobs["J1"].area_ha == 16
    # order follows submission: J1 is first (0), J5 is last (4)
    assert farm.jobs["J1"].order == 0
    assert farm.jobs["J5"].order == 4


def test_ha_per_hour_sprayer(farm):
    # 18 * 12 / 10 * 0.8 = 17.28
    assert farm.implements["sprayer_18"].ha_per_hour == pytest.approx(17.28)


def test_work_minutes_plain_and_with_factor(farm):
    sprayer = farm.implements["sprayer_18"]
    # 16 / 17.28 * 60 = 55.56
    assert work_minutes(sprayer, 16) == pytest.approx(55.56, abs=0.01)
    # 16 / (17.28 * 0.8) * 60 = 69.44
    assert work_minutes(sprayer, 16, factor=0.8) == pytest.approx(69.44, abs=0.01)


def test_travel_minutes():
    # 2.8 km at 28 km/h = 0.1 h = 6.0 min
    assert travel_minutes(2.8, 28) == pytest.approx(6.0)


def test_pick_implement_catalog_cases(farm):
    # T1 has 180 hp and owns disc_6 (min 160 hp): till -> disc_6
    assert pick_implement(farm, farm.tractors["T1"], "till").id == "disc_6"
    # T2 owns sprayer_18 and mower_4 only, so no disc: till -> None
    assert pick_implement(farm, farm.tractors["T2"], "till") is None
    # T2 (120 hp) owns sprayer_18 (min 100 hp): spray -> sprayer_18
    assert pick_implement(farm, farm.tractors["T2"], "spray").id == "sprayer_18"


def test_pick_implement_none_when_too_little_hp(raw_farm):
    # T2 (120 hp) is given disc_6 (min 160 hp) as its only till implement.
    # 120 < 160, so it cannot pull it: None
    raw = copy.deepcopy(raw_farm)
    raw["tractors"][1]["implements"] = ["disc_6"]
    farm = farm_from_dict(raw)
    assert pick_implement(farm, farm.tractors["T2"], "till") is None


def test_pick_implement_chooses_faster_of_same_type(raw_farm):
    # sprayer_12: 12 * 12 / 10 * 0.8 = 11.52 ha/h
    # sprayer_18: 18 * 12 / 10 * 0.8 = 17.28 ha/h
    # Slower one is listed first, so the choice is not just list order.
    raw = copy.deepcopy(raw_farm)
    raw["implements"].append({
        "id": "sprayer_12", "type": "spray", "name": "12 m boom sprayer",
        "width_m": 12, "speed_kmh": 12, "min_hp": 100, "efficiency": 0.8,
    })
    raw["tractors"][1]["implements"] = ["sprayer_12", "sprayer_18"]
    farm = farm_from_dict(raw)
    assert pick_implement(farm, farm.tractors["T2"], "spray").id == "sprayer_18"


def test_truth_factor_exact_key(farm):
    # "T2|mow|F3" is an exact key in truth: 0.8
    assert truth_factor(farm, "T2", "mow", "F3") == pytest.approx(0.8)


def test_truth_factor_wildcard_tractor(farm):
    # "*|till|F4" matches any tractor doing till on F4: 0.9
    assert truth_factor(farm, "T1", "till", "F4") == pytest.approx(0.9)
    assert truth_factor(farm, "T3", "till", "F4") == pytest.approx(0.9)


def test_truth_factor_unknown_combination(farm):
    # No truth entry matches T3 spray on F1: default 1.0
    assert truth_factor(farm, "T3", "spray", "F1") == pytest.approx(1.0)


def test_clock_formatting():
    # 360 + 215 = 575 minutes = 9 h 35 min -> "09:35"
    assert clock(360, 215) == "09:35"


def test_farm_from_dict_requires_a_depot(raw_farm):
    # Turning the only depot (YARD) into a junction leaves zero depots
    raw = copy.deepcopy(raw_farm)
    raw["nodes"][0]["kind"] = "junction"
    with pytest.raises(ValueError):
        farm_from_dict(raw)
