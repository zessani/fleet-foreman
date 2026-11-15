"""Tests for tools/check_farm.py (Vishva)."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_farm", ROOT / "tools" / "check_farm.py")
check_farm_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_farm_module)
check_farm = check_farm_module.check_farm


def load(name: str) -> dict:
    with open(ROOT / "scenarios" / name, encoding="utf-8") as farm_file:
        return json.load(farm_file)


@pytest.mark.parametrize("name", ["example_farm.json", "farms/sample_farm.json"])
def test_real_farms_are_ok(name: str) -> None:
    assert check_farm(load(name)) == []


def broken(change) -> list[str]:
    farm = copy.deepcopy(load("farms/sample_farm.json"))
    change(farm)
    return check_farm(farm)


def has(mistakes: list[str], text: str) -> bool:
    return any(text in mistake for mistake in mistakes)


def test_road_to_missing_node() -> None:
    assert has(broken(lambda f: f["roads"].append(["YARD", "NOPE"])), "'NOPE', which does not exist")


def test_unreachable_field() -> None:
    def cut_off_f8(farm):
        farm["roads"] = [r for r in farm["roads"] if "F8" not in r]
    assert has(broken(cut_off_f8), "Field 'F8' can't be reached")


def test_no_tractor_has_implement() -> None:
    def remove_sprayer(farm):
        farm["tractors"][1]["implements"].remove("sprayer_24")
        farm["tractors"][1]["mounted"] = "mower_6"
    assert has(broken(remove_sprayer), "no tractor has one")


def test_not_enough_horsepower() -> None:
    def weak_bolt(farm):
        farm["tractors"][1]["hp"] = 50
    assert has(broken(weak_bolt), "enough horsepower")


def test_mounted_not_owned() -> None:
    def bad_mount(farm):
        farm["tractors"][0]["mounted"] = "sprayer_24"
    assert has(broken(bad_mount), "not in its implements list")


def test_after_missing_job() -> None:
    def bad_after(farm):
        farm["jobs"][0]["after"] = "J99"
    assert has(broken(bad_after), "'J99', which does not exist")


def test_deadline_before_release() -> None:
    def bad_times(farm):
        farm["jobs"][0]["release"] = 200
        farm["jobs"][0]["deadline"] = 100
    assert has(broken(bad_times), "before its release time")


def test_duplicate_ids() -> None:
    def dup(farm):
        farm["jobs"][1]["id"] = "J1"
    assert has(broken(dup), "Duplicate job id 'J1'")
