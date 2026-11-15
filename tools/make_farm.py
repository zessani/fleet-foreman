"""Random farm maker (format in CLAUDE.md section 8a).

Makes random but valid farm files of any size. The same seed always gives the
same farm. Every farm is checked with tools/check_farm.py and made again if
the checker finds a mistake.

Usage:
    python tools/make_farm.py --tractors 3 --fields 8 --jobs 10 --seed 1 --out scenarios/farms/random_1.json

Other code (like a proof run over many farms) can call make_farm() directly:
    farm = make_farm(tractors=3, fields=8, jobs=10, seed=1)
"""

import argparse
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_farm import check_farm  # noqa: E402

MAX_ATTEMPTS = 50
JOB_TYPES = ("spray", "mow", "till", "plant")
MAX_JOBS_PER_FIELD = len(JOB_TYPES)  # one of each type per field at most
LOAD_SHARE = 0.45  # work may fill at most this share of the capable tractors' day

# Implement catalog. Each farm gets all of these; tractors own a few.
IMPLEMENTS = [
    {"id": "sprayer_18", "type": "spray", "name": "18 m boom sprayer",
     "width_m": 18, "speed_kmh": 12, "min_hp": 100, "efficiency": 0.8},
    {"id": "sprayer_24", "type": "spray", "name": "24 m boom sprayer",
     "width_m": 24, "speed_kmh": 12, "min_hp": 120, "efficiency": 0.8},
    {"id": "mower_4", "type": "mow", "name": "4 m disc mower",
     "width_m": 4, "speed_kmh": 14, "min_hp": 90, "efficiency": 0.85},
    {"id": "mower_6", "type": "mow", "name": "6 m disc mower",
     "width_m": 6, "speed_kmh": 14, "min_hp": 110, "efficiency": 0.85},
    {"id": "disc_4", "type": "till", "name": "4 m tandem disc",
     "width_m": 4, "speed_kmh": 9, "min_hp": 130, "efficiency": 0.85},
    {"id": "disc_6", "type": "till", "name": "6 m tandem disc",
     "width_m": 6, "speed_kmh": 9, "min_hp": 160, "efficiency": 0.85},
    {"id": "planter_6", "type": "plant", "name": "6 m row planter",
     "width_m": 6, "speed_kmh": 8, "min_hp": 140, "efficiency": 0.75},
    {"id": "planter_8", "type": "plant", "name": "8 m row planter",
     "width_m": 8, "speed_kmh": 8, "min_hp": 160, "efficiency": 0.75},
]

FARM_NAMES = ["Copper Ridge", "Dry Creek", "Sonoran Acres", "Mesa Verde", "Red Rock",
              "Cactus Wren", "Silver Wash", "Palo Verde", "Coyote Hill", "Sunset Bend"]
FIELD_NAMES = ["North 40", "East Orchard", "River Flat", "West Bench", "Mesa Top",
               "South Pivot", "Canal Strip", "Mesquite Corner", "Dry Wash", "Upper Terrace",
               "Lower Terrace", "Cottonwood", "Windmill", "Old Homestead", "Pecan Row",
               "Quail Run", "Ironwood", "Juniper Flat", "Butte View", "Arroyo",
               "Sage Field", "Stone Wall", "Long Strip", "Back 80", "Railroad"]
TRACTOR_NAMES = ["Atlas", "Bolt", "Cedar", "Dune", "Ember", "Flint", "Granite", "Hawk",
                 "Iron", "Jasper", "Kestrel", "Lumen"]
CROPS = ["lettuce", "cotton", "alfalfa", "citrus", "sorghum", "wheat"]


def _place(rng: random.Random, taken: list[tuple[float, float]], spread: float,
           min_gap: float = 0.8) -> tuple[float, float]:
    """A random point at least min_gap km from every taken point, if one can be found."""
    best, best_gap = (0.0, 0.0), -1.0
    for _ in range(200):
        x = round(rng.uniform(-spread, spread), 2)
        y = round(rng.uniform(-spread, spread), 2)
        gap = min(math.dist((x, y), p) for p in taken)
        if gap >= min_gap:
            return x, y
        if gap > best_gap:
            best, best_gap = (x, y), gap
    return best


def _roads(rng: random.Random, nodes: list[dict]) -> list[list[str]]:
    """Connect every node to the yard network, then add a few extra links for loops."""
    xy = {n["id"]: (n["x"], n["y"]) for n in nodes}
    order = sorted(xy, key=lambda node_id: math.dist(xy[node_id], xy["YARD"]))
    connected = [order[0]]
    links: set[frozenset[str]] = set()
    for node_id in order[1:]:
        nearest = min(connected, key=lambda other: math.dist(xy[node_id], xy[other]))
        links.add(frozenset((node_id, nearest)))
        connected.append(node_id)
    # Extra links so a blocked road usually has a way around.
    for _ in range(max(1, len(nodes) // 4)):
        a = rng.choice(order)
        candidates = sorted((b for b in order if b != a and frozenset((a, b)) not in links),
                            key=lambda b: math.dist(xy[a], xy[b]))
        if candidates:
            links.add(frozenset((a, candidates[0])))
    return sorted(sorted(link) for link in links)


def _tractors(rng: random.Random, count: int) -> list[dict]:
    tractors = []
    for i in range(count):
        hp = rng.randrange(110, 230, 10)
        pullable = [imp for imp in IMPLEMENTS if imp["min_hp"] <= hp]
        rng.shuffle(pullable)
        owned: list[str] = []
        types: set[str] = set()
        for imp in pullable:  # 2 or 3 implements, all of different types
            if imp["type"] not in types and len(owned) < rng.choice((2, 3)):
                owned.append(imp["id"])
                types.add(imp["type"])
        capacity = rng.randrange(260, 360, 20)
        name = TRACTOR_NAMES[i] if i < len(TRACTOR_NAMES) else f"Tractor {i + 1}"
        tractors.append({
            "id": f"T{i + 1}", "name": name, "hp": hp,
            "implements": owned, "mounted": owned[0],
            "speed_kmh": rng.randrange(24, 34, 2),
            "fuel_capacity": capacity, "fuel": capacity,
            "burn_km": round(rng.uniform(0.5, 0.65), 2),
            "burn_work": round(rng.uniform(0.35, 0.45), 2),
            "node": "YARD",
        })

    # Every job type must have at least one tractor that can do it.
    by_id = {imp["id"]: imp for imp in IMPLEMENTS}
    for job_type in JOB_TYPES:
        if any(by_id[i]["type"] == job_type for t in tractors for i in t["implements"]):
            continue
        lightest = min((imp for imp in IMPLEMENTS if imp["type"] == job_type),
                       key=lambda imp: imp["min_hp"])
        strongest = max(tractors, key=lambda t: t["hp"])
        strongest["hp"] = max(strongest["hp"], lightest["min_hp"])
        strongest["implements"].append(lightest["id"])
    return tractors


def _jobs(rng: random.Random, fields: list[dict], count: int, horizon: int) -> list[dict]:
    jobs: list[dict] = []
    used: dict[str, set[str]] = {f["id"]: set() for f in fields}

    def add(job_type: str, field: str, after: str | None, earliest_deadline: int) -> str:
        job_id = f"J{len(jobs) + 1}"
        release = 0 if rng.random() < 0.8 else rng.randrange(30, 150, 10)
        low = max(release + 150, earliest_deadline)
        deadline = rng.randrange(low, horizon + 1, 10) if low < horizon else horizon
        jobs.append({"id": job_id, "type": job_type, "field": field,
                     "priority": rng.randint(1, 5), "deadline": deadline,
                     "release": release, "after": after})
        used[field].add(job_type)
        return job_id

    while len(jobs) < count:
        field = rng.choice([f["id"] for f in fields if len(used[f["id"]]) < MAX_JOBS_PER_FIELD])
        free = [t for t in JOB_TYPES if t not in used[field]]
        # Sometimes a "till, then plant" pair on one field.
        if count - len(jobs) >= 2 and {"till", "plant"} <= set(free) and rng.random() < 0.35:
            till = add("till", field, None, 180)
            add("plant", field, till, jobs[-1]["deadline"] + 120)
        else:
            choices = [t for t in free if t != "plant"] or free
            add(rng.choice(choices), field, None, 120)
    return jobs


def _fit_to_fleet(fields: list[dict], tractors: list[dict], jobs: list[dict], horizon: int) -> None:
    """Shrink the fields if the work would not fit in a day.

    For each job type, the work minutes must stay under LOAD_SHARE of the day
    of the tractors that can do that type. That leaves room for driving,
    implement swaps, and a breakdown.
    """
    by_id = {imp["id"]: imp for imp in IMPLEMENTS}
    area = {f["id"]: f["area_ha"] for f in fields}
    factor = 1.0
    for job_type in JOB_TYPES:
        rates = [by_id[i]["width_m"] * by_id[i]["speed_kmh"] / 10 * by_id[i]["efficiency"]
                 for t in tractors for i in t["implements"]
                 if by_id[i]["type"] == job_type and by_id[i]["min_hp"] <= t["hp"]]
        capable = len({t["id"] for t in tractors for i in t["implements"]
                       if by_id[i]["type"] == job_type and by_id[i]["min_hp"] <= t["hp"]})
        work_min = sum(area[j["field"]] / max(rates) * 60 for j in jobs if j["type"] == job_type) \
            if rates else 0.0
        budget = LOAD_SHARE * horizon * capable
        if work_min > budget:
            factor = min(factor, budget / work_min)
    if factor < 1.0:
        for f in fields:
            f["area_ha"] = max(2.0, round(f["area_ha"] * factor * 2) / 2)


def _one_farm(rng: random.Random, tractors: int, fields: int, jobs: int, seed: int) -> dict:
    horizon = 660
    spread = 1.2 * math.sqrt(fields) + 1.0
    nodes = [{"id": "YARD", "kind": "depot", "name": "Yard", "x": 0.0, "y": 0.0}]
    taken = [(0.0, 0.0)]
    for i in range(fields):
        x, y = _place(rng, taken, spread)
        taken.append((x, y))
        name = FIELD_NAMES[i] if i < len(FIELD_NAMES) else f"Field {i + 1}"
        nodes.append({"id": f"F{i + 1}", "kind": "field", "name": name, "x": x, "y": y,
                      "area_ha": rng.randrange(4, 17), "crop": rng.choice(CROPS)})
    for i in range(max(1, fields // 3)):
        x, y = _place(rng, taken, spread * 0.7)
        taken.append((x, y))
        nodes.append({"id": f"X{i + 1}", "kind": "junction", "x": x, "y": y})

    field_nodes = [n for n in nodes if n["kind"] == "field"]
    tractor_list = _tractors(rng, tractors)
    job_list = _jobs(rng, field_nodes, jobs, horizon)
    _fit_to_fleet(field_nodes, tractor_list, job_list, horizon)

    truth = {}
    for _ in range(2):
        job = rng.choice(job_list)
        truth[f"*|{job['type']}|{job['field']}"] = round(rng.uniform(0.75, 1.15), 2)

    events = [{"at": rng.randrange(120, 361, 5), "type": "breakdown",
               "tractor": rng.choice(tractor_list)["id"]}]

    return {
        "name": f"{rng.choice(FARM_NAMES)} Farm (random, seed {seed})",
        "day_start_min": 360,
        "horizon_min": horizon,
        "nodes": nodes,
        "roads": _roads(rng, nodes),
        "implements": IMPLEMENTS,
        "tractors": tractor_list,
        "jobs": job_list,
        "truth": truth,
        "events": events,
    }


def make_farm(tractors: int = 3, fields: int = 8, jobs: int = 10, seed: int = 1) -> dict:
    """A random farm that passes check_farm. Same arguments, same farm."""
    if tractors < 1 or fields < 1 or jobs < 1:
        raise ValueError("tractors, fields and jobs must all be at least 1")
    if jobs > MAX_JOBS_PER_FIELD * fields:
        raise ValueError(f"at most {MAX_JOBS_PER_FIELD} jobs per field, "
                         f"so {fields} fields can hold at most {MAX_JOBS_PER_FIELD * fields} jobs")
    rng = random.Random(seed)
    for _ in range(MAX_ATTEMPTS):
        farm = _one_farm(rng, tractors, fields, jobs, seed)
        if not check_farm(farm):
            return farm
    raise RuntimeError(f"could not make a valid farm in {MAX_ATTEMPTS} tries")


def main() -> int:
    parser = argparse.ArgumentParser(description="Make a random, valid farm file.")
    parser.add_argument("--tractors", type=int, default=3)
    parser.add_argument("--fields", type=int, default=8)
    parser.add_argument("--jobs", type=int, default=10)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", help="file to write; prints to the screen if left out")
    args = parser.parse_args()

    try:
        farm = make_farm(args.tractors, args.fields, args.jobs, args.seed)
    except (ValueError, RuntimeError) as error:
        print(f"Error: {error}")
        return 1

    text = json.dumps(farm, indent=2) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}: {args.tractors} tractors, {args.fields} fields, "
              f"{args.jobs} jobs (seed {args.seed}). Farm OK.")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
