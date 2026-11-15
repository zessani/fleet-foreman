"""Tests for tools/make_farm.py (Vishva)."""

import importlib.util
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
_spec = importlib.util.spec_from_file_location("make_farm", TOOLS / "make_farm.py")
make_farm_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_farm_module)
make_farm = make_farm_module.make_farm

from check_farm import check_farm  # noqa: E402


def test_same_seed_same_farm() -> None:
    assert make_farm(3, 8, 10, seed=7) == make_farm(3, 8, 10, seed=7)
    assert make_farm(3, 8, 10, seed=7) != make_farm(3, 8, 10, seed=8)


@pytest.mark.parametrize("tractors,fields,jobs", [(1, 1, 1), (2, 5, 7), (3, 8, 10), (5, 15, 25), (8, 20, 40)])
def test_farms_of_any_size_pass_the_checker(tractors: int, fields: int, jobs: int) -> None:
    for seed in range(5):
        farm = make_farm(tractors, fields, jobs, seed)
        assert check_farm(farm) == []
        assert len(farm["tractors"]) == tractors
        assert sum(1 for n in farm["nodes"] if n["kind"] == "field") == fields
        assert len(farm["jobs"]) == jobs


def test_too_many_jobs_for_the_fields_is_refused() -> None:
    with pytest.raises(ValueError):
        make_farm(2, 2, 9, seed=1)
