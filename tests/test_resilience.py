"""Risk-aware planning remains efficient and only breaks close operating ties."""

import pytest

from foreman.model import farm_from_dict
from foreman.nav import RoadMap
from foreman.planner import plan
from foreman.resilience import OPERATING_TOLERANCE, RESILIENCE_WEIGHT, operating_limit
from foreman.sim import Simulation
from foreman.state import WorldState


def one_job_farm():
    raw = {
        "name": "Resilience test",
        "day_start_min": 360,
        "horizon_min": 300,
        "nodes": [
            {"id": "YARD", "kind": "depot", "x": 0, "y": 0},
            {"id": "F1", "kind": "field", "x": 1, "y": 0, "area_ha": 10},
        ],
        "roads": [["YARD", "F1"]],
        "implements": [
            {"id": "mower", "type": "mow", "width_m": 10, "speed_kmh": 10,
             "min_hp": 80, "efficiency": 1.0},
        ],
        "tractors": [
            {"id": "T1", "hp": 120, "implements": ["mower"], "mounted": "mower",
             "speed_kmh": 30, "fuel_capacity": 200, "fuel": 200,
             "burn_km": 0.2, "burn_work": 0.1, "node": "YARD",
             "breakdown_risk_per_hour": 0.30, "typical_repair_min": 90},
            {"id": "T2", "hp": 120, "implements": ["mower"], "mounted": "mower",
             "speed_kmh": 30, "fuel_capacity": 200, "fuel": 200,
             "burn_km": 0.2, "burn_work": 0.1, "node": "YARD",
             "breakdown_risk_per_hour": 0.01, "typical_repair_min": 30},
        ],
        "jobs": [
            {"id": "J1", "type": "mow", "field": "F1", "priority": 5,
             "deadline": 120},
        ],
    }
    return farm_from_dict(raw)


def test_resilience_breaks_an_equal_operating_tie_toward_the_safer_tractor() -> None:
    farm = one_job_farm()
    road_map, world = RoadMap(farm.raw), WorldState.start_of_day(farm)
    efficient = plan(farm, road_map, world, resilience=False)
    resilient = plan(farm, road_map, world)

    assert efficient.stop_for("J1").tractor == "T1"  # deterministic id tie-break
    assert resilient.stop_for("J1").tractor == "T2"
    assert resilient.resilience["changed"] is True
    assert resilient.resilience["operating_increase_pct"] == pytest.approx(0.0)
    assert resilient.resilience["resilience_improvement_pct"] >= 5.0


def test_selected_plan_never_exceeds_the_five_percent_operating_ceiling() -> None:
    farm = one_job_farm()
    result = plan(farm, RoadMap(farm.raw), WorldState.start_of_day(farm))
    audit = result.resilience

    assert audit["selected_operating_cost"] <= operating_limit(audit["best_operating_cost"])
    assert audit["operating_tolerance_pct"] == 100 * OPERATING_TOLERANCE
    assert audit["resilience_weight_pct"] == 100 * RESILIENCE_WEIGHT


def test_operating_ceiling_rejects_a_materially_more_expensive_plan() -> None:
    assert operating_limit(100.0) == pytest.approx(105.0)
    assert 105.01 > operating_limit(100.0)


def test_safer_equal_cost_assignment_survives_the_risky_tractors_failure() -> None:
    farm = one_job_farm()
    road_map = RoadMap(farm.raw)
    efficient = plan(farm, road_map, WorldState.start_of_day(farm), resilience=False)
    resilient = plan(farm, road_map, WorldState.start_of_day(farm))

    ordinary_sim = Simulation(farm, road_map, use_truth=False)
    robust_sim = Simulation(farm, road_map, use_truth=False)
    for tractor_id, stops in efficient.routes.items():
        ordinary_sim.tractors[tractor_id].dispatch([stop.to_dispatch() for stop in stops])
    for tractor_id, stops in resilient.routes.items():
        robust_sim.tractors[tractor_id].dispatch([stop.to_dispatch() for stop in stops])
    ordinary_sim.tractors["T1"].break_down()
    robust_sim.tractors["T1"].break_down()
    ordinary_sim.advance(farm.horizon_min)
    robust_sim.advance(farm.horizon_min)

    assert ordinary_sim.jobs["J1"].status != "done"
    assert robust_sim.jobs["J1"].status == "done"
