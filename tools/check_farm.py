"""Farm file checker (format in CLAUDE.md section 8a).

Reads a farm file and prints every mistake it finds in plain English.
If there are none, prints "Farm OK".

Usage:  python tools/check_farm.py scenarios/farms/sample_farm.json

Exit code is 0 when the farm is OK and 1 when there are mistakes, so other
scripts (like the random farm maker) can use it too.
"""

import json
import sys
from collections import Counter

NODE_KINDS = ("depot", "field", "junction")


def _duplicates(items: list[dict], what: str) -> list[str]:
    counts = Counter(item.get("id") for item in items)
    return [f"Duplicate {what} id '{item_id}' is used {n} times."
            for item_id, n in counts.items() if n > 1]


def _reachable(start: str, node_ids: set[str], roads: list) -> set[str]:
    """Node ids that can be reached from start by road."""
    links: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for road in roads:
        if len(road) == 2 and road[0] in links and road[1] in links:
            links[road[0]].add(road[1])
            links[road[1]].add(road[0])
    seen = {start}
    to_visit = [start]
    while to_visit:
        for neighbour in links[to_visit.pop()]:
            if neighbour not in seen:
                seen.add(neighbour)
                to_visit.append(neighbour)
    return seen


def check_farm(farm: dict) -> list[str]:
    """Return a list of mistakes in plain English. An empty list means OK."""
    mistakes: list[str] = []

    for key in ("name", "nodes", "implements", "tractors", "jobs"):
        if key not in farm:
            mistakes.append(f"The farm is missing the '{key}' section.")
    nodes = farm.get("nodes", [])
    implements = farm.get("implements", [])
    tractors = farm.get("tractors", [])
    jobs = farm.get("jobs", [])

    # Duplicate ids.
    mistakes += _duplicates(nodes, "node")
    mistakes += _duplicates(implements, "implement")
    mistakes += _duplicates(tractors, "tractor")
    mistakes += _duplicates(jobs, "job")

    node_by_id = {node.get("id"): node for node in nodes}
    implement_by_id = {imp.get("id"): imp for imp in implements}
    tractor_ids = {tractor.get("id") for tractor in tractors}
    job_by_id = {job.get("id"): job for job in jobs}

    # Nodes.
    depots = [node.get("id") for node in nodes if node.get("kind") == "depot"]
    if len(depots) != 1:
        mistakes.append(f"There must be exactly one yard (kind 'depot'), but there are {len(depots)}.")
    for node in nodes:
        if node.get("kind") not in NODE_KINDS:
            mistakes.append(f"Node '{node.get('id')}' has kind '{node.get('kind')}', "
                            f"which should be one of: {', '.join(NODE_KINDS)}.")
        if "x" not in node or "y" not in node:
            mistakes.append(f"Node '{node.get('id')}' is missing its x or y position.")
        if node.get("kind") == "field" and not node.get("area_ha", 0) > 0:
            mistakes.append(f"Field '{node.get('id')}' needs an area_ha bigger than 0.")

    # Roads.
    roads = farm.get("roads")
    for road in roads or []:
        if len(road) != 2:
            mistakes.append(f"Road {road} should connect exactly two nodes.")
            continue
        for end in road:
            if end not in node_by_id:
                mistakes.append(f"Road {road[0]}-{road[1]} points to node '{end}', which does not exist.")
        if road[0] == road[1]:
            mistakes.append(f"Road {road[0]}-{road[1]} connects a node to itself.")

    # Every field reachable from the yard. No roads section means all nodes are linked.
    if roads is not None and len(depots) == 1:
        reached = _reachable(depots[0], set(node_by_id), roads)
        for node in nodes:
            if node.get("id") not in reached:
                place = "Field" if node.get("kind") == "field" else "Node"
                mistakes.append(f"{place} '{node.get('id')}' can't be reached by road from the yard.")

    # Implements.
    for imp in implements:
        for key in ("width_m", "speed_kmh", "efficiency"):
            if not imp.get(key, 0) > 0:
                mistakes.append(f"Implement '{imp.get('id')}' needs a {key} bigger than 0.")
        if "type" not in imp:
            mistakes.append(f"Implement '{imp.get('id')}' has no type.")

    # Tractors.
    for tractor in tractors:
        tid = tractor.get("id")
        owned = tractor.get("implements", [])
        for imp_id in owned:
            if imp_id not in implement_by_id:
                mistakes.append(f"Tractor '{tid}' lists implement '{imp_id}', which does not exist.")
        mounted = tractor.get("mounted")
        if mounted is not None and mounted not in owned:
            mistakes.append(f"Tractor '{tid}' has '{mounted}' mounted, but it is not in its implements list.")
        if tractor.get("node") not in node_by_id:
            mistakes.append(f"Tractor '{tid}' starts at node '{tractor.get('node')}', which does not exist.")
        capacity = tractor.get("fuel_capacity", 0)
        fuel = tractor.get("fuel", 0)
        if not capacity > 0:
            mistakes.append(f"Tractor '{tid}' needs a fuel_capacity bigger than 0.")
        elif not 0 <= fuel <= capacity:
            mistakes.append(f"Tractor '{tid}' has {fuel} fuel, which should be between 0 and its tank size {capacity}.")
        if not tractor.get("speed_kmh", 0) > 0:
            mistakes.append(f"Tractor '{tid}' needs a speed_kmh bigger than 0.")

    # Jobs.
    for job in jobs:
        jid = job.get("id")
        jtype = job.get("type")
        field = node_by_id.get(job.get("field"))
        if field is None:
            mistakes.append(f"Job '{jid}' is on field '{job.get('field')}', which does not exist.")
        elif field.get("kind") != "field":
            mistakes.append(f"Job '{jid}' is on '{job.get('field')}', which is a {field.get('kind')}, not a field.")

        # Can any tractor do this job?
        matching = [imp for imp in implements if imp.get("type") == jtype]
        owners = [(tractor, implement_by_id[imp_id]) for tractor in tractors
                  for imp_id in tractor.get("implements", [])
                  if imp_id in implement_by_id and implement_by_id[imp_id].get("type") == jtype]
        if not matching:
            mistakes.append(f"Job '{jid}' needs a '{jtype}' implement, but the farm has none.")
        elif not owners:
            mistakes.append(f"Job '{jid}' needs a '{jtype}' implement, but no tractor has one.")
        elif not any(tractor.get("hp", 0) >= imp.get("min_hp", 0) for tractor, imp in owners):
            mistakes.append(f"Job '{jid}' ({jtype}): no tractor that has a '{jtype}' implement "
                            f"has enough horsepower to pull it.")

        priority = job.get("priority")
        if not (isinstance(priority, int) and 1 <= priority <= 5):
            mistakes.append(f"Job '{jid}' has priority {priority}, which should be a whole number from 1 to 5.")
        release = job.get("release", 0) or 0
        deadline = job.get("deadline")
        if deadline is None:
            mistakes.append(f"Job '{jid}' has no deadline.")
        elif deadline < release:
            mistakes.append(f"Job '{jid}' has its deadline ({deadline}) before its release time ({release}).")

        after = job.get("after")
        if after is not None:
            if after == jid:
                mistakes.append(f"Job '{jid}' says it must come after itself.")
            elif after not in job_by_id:
                mistakes.append(f"Job '{jid}' must come after job '{after}', which does not exist.")

    # Loops in "after", like J1 after J2 and J2 after J1.
    for job in jobs:
        seen = {job.get("id")}
        current = job.get("after")
        while current in job_by_id and current not in seen:
            seen.add(current)
            current = job_by_id[current].get("after")
        if current == job.get("id") and job.get("after") != job.get("id"):
            mistakes.append(f"Job '{job.get('id')}' is part of a loop of 'after' links, so it can never start.")

    # Scripted events and hidden truth must name real things.
    for event in farm.get("events", []):
        if "tractor" in event and event["tractor"] not in tractor_ids:
            mistakes.append(f"Event at {event.get('at')} names tractor '{event['tractor']}', which does not exist.")
    for key in farm.get("truth", {}):
        parts = key.split("|")
        if len(parts) != 3:
            mistakes.append(f"Truth key '{key}' should look like 'tractor|job type|field'.")
            continue
        if parts[0] != "*" and parts[0] not in tractor_ids:
            mistakes.append(f"Truth key '{key}' names tractor '{parts[0]}', which does not exist.")
        if parts[2] != "*" and parts[2] not in node_by_id:
            mistakes.append(f"Truth key '{key}' names field '{parts[2]}', which does not exist.")

    return mistakes


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python tools/check_farm.py <farm file>")
        return 2
    try:
        with open(argv[1], encoding="utf-8") as farm_file:
            farm = json.load(farm_file)
    except FileNotFoundError:
        print(f"File not found: {argv[1]}")
        return 1
    except json.JSONDecodeError as error:
        print(f"The file is not valid JSON: {error}")
        return 1

    mistakes = check_farm(farm)
    if not mistakes:
        print("Farm OK")
        return 0
    print(f"Found {len(mistakes)} mistake(s) in {argv[1]}:")
    for mistake in mistakes:
        print(f"  - {mistake}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
