"""Farm data model, loader, and core formulas (CLAUDE.md sections 8a and 10)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

SWAP_MIN = 25.0        # implement swap at the yard
REFUEL_MIN = 10.0      # refuel at the yard
FUEL_RESERVE = 0.10    # never plan a tractor below this share of its tank
REFUEL_BELOW = 0.60    # a tractor passing the yard below this share refuels
# Real field speeds differ from the catalog, and a slower job burns more fuel.
# Plans budget this much extra work fuel so a slow field cannot eat the reserve.
FUEL_MARGIN = 1.3
DEFAULT_PRIORITY = 3


@dataclass(frozen=True)
class Node:
    id: str
    kind: str  # depot | field | junction
    name: str
    x: float
    y: float
    area_ha: float = 0.0
    crop: str = ""


@dataclass(frozen=True)
class Implement:
    id: str
    type: str  # spray | mow | till | plant
    name: str
    width_m: float
    speed_kmh: float
    min_hp: float
    efficiency: float

    @property
    def ha_per_hour(self) -> float:
        return self.width_m * self.speed_kmh / 10 * self.efficiency


@dataclass(frozen=True)
class Tractor:
    id: str
    name: str
    hp: float
    implements: tuple[str, ...]
    mounted: str
    speed_kmh: float
    fuel_capacity: float
    fuel: float
    burn_km: float
    burn_work: float
    node: str
    # Transparent reliability assumptions for risk-aware planning.  They are
    # scenario inputs, not claims learned from telemetry.
    breakdown_risk_per_hour: float = 0.02
    typical_repair_min: float = 45.0


@dataclass(frozen=True)
class Job:
    id: str
    type: str
    field: str
    priority: int
    deadline: float
    release: float
    after: str | None
    area_ha: float
    order: int  # position in the submitted list; the baseline serves in this order


@dataclass
class Farm:
    name: str
    day_start_min: float
    horizon_min: float
    depot: str
    nodes: dict[str, Node]
    implements: dict[str, Implement]
    tractors: dict[str, Tractor]
    jobs: dict[str, Job]
    truth: dict[str, float]
    events: list[dict]
    raw: dict  # the farm file as loaded; RoadMap is built from this


def farm_from_dict(raw: dict) -> Farm:
    nodes = {
        n["id"]: Node(n["id"], n["kind"], n.get("name", n["id"]), float(n["x"]), float(n["y"]),
                      float(n.get("area_ha", 0.0)), n.get("crop", ""))
        for n in raw["nodes"]
    }
    depots = [node.id for node in nodes.values() if node.kind == "depot"]
    if len(depots) != 1:
        raise ValueError(f"a farm needs exactly one depot, found {len(depots)}")

    implements = {
        i["id"]: Implement(i["id"], i["type"], i.get("name", i["id"]), float(i["width_m"]),
                           float(i["speed_kmh"]), float(i["min_hp"]), float(i["efficiency"]))
        for i in raw["implements"]
    }
    tractors = {
        t["id"]: Tractor(t["id"], t.get("name", t["id"]), float(t["hp"]), tuple(t["implements"]),
                         t["mounted"], float(t["speed_kmh"]), float(t["fuel_capacity"]),
                         float(t.get("fuel", t["fuel_capacity"])), float(t["burn_km"]),
                         float(t["burn_work"]), t.get("node", depots[0]),
                         float(t.get("breakdown_risk_per_hour", 0.02)),
                         float(t.get("typical_repair_min", 45.0)))
        for t in raw["tractors"]
    }
    horizon = float(raw.get("horizon_min", 660))
    jobs = {
        j["id"]: job_from_dict(j, order, nodes, horizon)
        for order, j in enumerate(raw.get("jobs", []))
    }
    return Farm(
        name=raw.get("name", "Farm"),
        day_start_min=float(raw.get("day_start_min", 360)),
        horizon_min=horizon,
        depot=depots[0],
        nodes=nodes,
        implements=implements,
        tractors=tractors,
        jobs=jobs,
        truth=dict(raw.get("truth") or {}),
        events=list(raw.get("events") or []),
        raw=raw,
    )


def job_from_dict(j: dict, order: int, nodes: dict[str, Node], horizon: float) -> Job:
    return Job(
        id=j["id"],
        type=j["type"],
        field=j["field"],
        priority=int(j.get("priority", DEFAULT_PRIORITY)),
        deadline=float(j.get("deadline", horizon)),
        release=float(j.get("release", 0.0)),
        after=j.get("after") or None,
        area_ha=float(j.get("area_ha", nodes[j["field"]].area_ha)),
        order=order,
    )


def load_farm(path: str | Path) -> Farm:
    with open(path, encoding="utf-8") as farm_file:
        return farm_from_dict(json.load(farm_file))


def pick_implement(farm: Farm, tractor: Tractor, job_type: str) -> Implement | None:
    """The fastest implement of the right type that the tractor owns and can pull."""
    usable = [
        farm.implements[implement_id]
        for implement_id in tractor.implements
        if farm.implements[implement_id].type == job_type
        and farm.implements[implement_id].min_hp <= tractor.hp
    ]
    if not usable:
        return None
    # Ties broken by id so the choice never depends on list order.
    return max(usable, key=lambda implement: (implement.ha_per_hour, implement.id))


def why_not(farm: Farm, tractor: Tractor, job_type: str) -> str:
    """Why this tractor cannot do this type of job, in plain words. "" if it can."""
    owned = [farm.implements[i] for i in tractor.implements if farm.implements[i].type == job_type]
    if not owned:
        return f"has no {job_type} implement"
    if all(implement.min_hp > tractor.hp for implement in owned):
        need = min(implement.min_hp for implement in owned)
        return f"has {tractor.hp:.0f} hp and its {job_type} implement needs {need:.0f}"
    return ""


def work_minutes(implement: Implement, hectares: float, factor: float = 1.0) -> float:
    """Minutes to cover the given area. factor scales the catalog rate."""
    return hectares / (implement.ha_per_hour * factor) * 60.0


def travel_minutes(km: float, speed_kmh: float) -> float:
    return km / speed_kmh * 60.0


def truth_factor(farm: Farm, tractor_id: str, job_type: str, field_id: str) -> float:
    """Hidden real-world speed factor. Only the simulator may call this."""
    candidates = [
        (tractor_id, job_type, field_id),
        ("*", job_type, field_id), (tractor_id, "*", field_id), (tractor_id, job_type, "*"),
        ("*", "*", field_id), ("*", job_type, "*"), (tractor_id, "*", "*"),
        ("*", "*", "*"),
    ]
    for key in candidates:
        factor = farm.truth.get("|".join(key))
        if factor is not None:
            return float(factor)
    return 1.0


def clock(day_start_min: float, now_min: float) -> str:
    """Turn minutes from the start of the working day into "HH:MM"."""
    total = int(day_start_min + now_min)
    return f"{total // 60 % 24:02d}:{total % 60:02d}"
