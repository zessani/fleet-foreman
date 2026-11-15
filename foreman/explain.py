"""Plain-language explanation of a re-plan, built only from plan data (CLAUDE.md section 15)."""

from __future__ import annotations

from foreman.model import Farm, clock
from foreman.state import Plan, Stop, WorldState

ING = {"spray": "spraying", "mow": "mowing", "till": "tilling", "plant": "planting"}
IDLE_PHRASES = {
    "traveling": "while traveling",
    "servicing": "during a stop at the yard",
    "waiting": "while waiting at a field",
    "idle": "while idle",
}


def explain(farm: Farm, world: WorldState, old: Plan | None, new: Plan, trigger: dict,
            safety_line: str = "", ms: float = 0.0) -> dict:
    """Describe what happened, what changed, and whether deadlines still hold."""
    open_jobs = _open_jobs(farm, world)
    counts = {"moved": 0, "retimed": 0, "unchanged": 0, "held": 0}
    if old is None:
        changes = _first_plan_changes(farm, new, open_jobs, counts)
    else:
        changes = _replan_changes(farm, world, old, new, open_jobs, counts)
    return {
        "headline": _headline(farm, world, trigger),
        "summary": _summary(new, counts, first=old is None),
        "changes": changes,
        "ms": round(ms, 1),
        "safety": safety_line,
        **counts,
    }


def _open_jobs(farm: Farm, world: WorldState) -> list[str]:
    """Jobs not done, in the order they were submitted."""
    ids = [job_id for job_id in farm.jobs
           if job_id not in world.jobs or world.jobs[job_id].status != "done"]
    return sorted(ids, key=lambda job_id: farm.jobs[job_id].order)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _minutes(value: float) -> int:
    return int(round(value))


def _time(farm: Farm, minute: float) -> str:
    return clock(farm.day_start_min, minute)


def _tractor_name(farm: Farm, tractor_id: str) -> str:
    tractor = farm.tractors.get(tractor_id)
    return tractor.name if tractor else tractor_id


def _node_name(farm: Farm, node_id: str) -> str:
    node = farm.nodes.get(node_id)
    return node.name if node else node_id


def _label(farm: Farm, job_id: str) -> str:
    job = farm.jobs[job_id]
    return f"{job.type.capitalize()} {_node_name(farm, job.field)}"


def _done_percent(farm: Farm, world: WorldState, job_id: str) -> int:
    job = farm.jobs[job_id]
    if job_id not in world.jobs or job.area_ha <= 0:
        return 0
    return _minutes(100 * (1 - world.jobs[job_id].remaining_ha / job.area_ha))


def _remaining_percent(farm: Farm, world: WorldState, job_id: str) -> int:
    return 100 - _done_percent(farm, world, job_id)


# Headline

def _headline(farm: Farm, world: WorldState, trigger: dict) -> str:
    at = _time(farm, world.now)
    kind = trigger.get("type")
    if kind == "jobs_submitted":
        return f"Day plan ready at {at}."
    if kind == "breakdown":
        return _breakdown_headline(farm, world, trigger, at)
    if kind == "repaired":
        return f"{_tractor_name(farm, trigger['tractor'])} is back in service at {at}."
    if kind in ("road_blocked", "road_opened"):
        a, b = _node_name(farm, trigger["a"]), _node_name(farm, trigger["b"])
        verb = "closed" if kind == "road_blocked" else "reopened"
        return f"The road between {a} and {b} {verb} at {at}."
    if kind == "priority_changed":
        return f"Priority of {_label_or_id(farm, trigger['job'])} changed to {trigger['priority']} at {at}."
    if kind == "job_added":
        return f"New job at {at}: {_label_or_id(farm, trigger['job'])}."
    if kind == "drift":
        return (f"{_label_or_id(farm, trigger['job'])} is running "
                f"{_minutes(trigger['behind_min'])} min behind plan at {at}.")
    return f"Plan updated at {at}."


def _label_or_id(farm: Farm, job_id: str) -> str:
    return _label(farm, job_id) if job_id in farm.jobs else job_id


def _breakdown_headline(farm: Farm, world: WorldState, trigger: dict, at: str) -> str:
    name = _tractor_name(farm, trigger["tractor"])
    state = trigger.get("state", "idle")
    job_id = trigger.get("job")
    if state == "working" and job_id in farm.jobs:
        verb = ING.get(farm.jobs[job_id].type, "working on")
        field_name = _node_name(farm, farm.jobs[job_id].field)
        percent = _done_percent(farm, world, job_id)
        return f"{name} went down at {at} while {verb} {field_name} ({percent}% done)."
    return f"{name} went down at {at} {IDLE_PHRASES.get(state, 'while idle')}."


# Summary

def _late_count(plan: Plan) -> int:
    return sum(1 for stop in plan.stops() if stop.late_min > 0)


def _verdict(plan: Plan, first: bool) -> str:
    late = _late_count(plan)
    if late == 0:
        return "All deadlines are met." if first else "All deadlines are still met."
    return f"{_plural(late, 'job')} will finish late."


def _summary(new: Plan, counts: dict[str, int], first: bool) -> str:
    held = len(new.held)
    if first:
        planned = len(new.stops())
        tractors = sum(1 for route in new.routes.values() if route)
        parts = [f"{_plural(planned, 'job')} planned across {_plural(tractors, 'tractor')}."]
    else:
        parts = [f"{_plural(counts['moved'], 'job')} moved, {counts['retimed']} retimed, "
                 f"{counts['unchanged']} unchanged."]
    parts.append(_verdict(new, first))
    if held:
        parts.append(f"{_plural(held, 'job')} {'is' if held == 1 else 'are'} held.")
    return " ".join(parts)


# Change sentences

def _deadline_tail(farm: Farm, stop: Stop) -> str:
    deadline = farm.jobs[stop.job].deadline
    when = _time(farm, deadline)
    if stop.late_min > 0:
        return f"{max(1, _minutes(stop.late_min))} min past its {when} deadline."
    return f"{_minutes(deadline - stop.finish)} min before its {when} deadline."


def _first_plan_changes(farm: Farm, new: Plan, open_jobs: list[str], counts: dict[str, int]) -> list[str]:
    changes = []
    for job_id in open_jobs:
        stop = new.stop_for(job_id)
        if stop is None:
            if job_id in new.held:
                counts["held"] += 1
                changes.append(_held_sentence(farm, job_id, new))
            continue
        implement = farm.implements[stop.implement].name
        changes.append(
            f"{_label(farm, job_id)}: {_tractor_name(farm, stop.tractor)} with the {implement}, "
            f"{_time(farm, stop.start)} to {_time(farm, stop.finish)}, {_deadline_tail(farm, stop)}")
    return changes


def _held_sentence(farm: Farm, job_id: str, new: Plan) -> str:
    return f"{_label(farm, job_id)} is held: {new.held[job_id].rstrip('.')}."


def _replan_changes(farm: Farm, world: WorldState, old: Plan, new: Plan, open_jobs: list[str],
                    counts: dict[str, int]) -> list[str]:
    changes = []
    for job_id in open_jobs:
        sentence = _job_change(farm, world, old, new, job_id, counts)
        if sentence:
            changes.append(sentence)
    return changes


def _job_change(farm: Farm, world: WorldState, old: Plan, new: Plan, job_id: str,
                counts: dict[str, int]) -> str | None:
    new_stop = new.stop_for(job_id)
    if new_stop is None:
        counts["held"] += 1
        return _held_sentence(farm, job_id, new) if job_id in new.held else None
    old_stop = old.stop_for(job_id)
    label = _label(farm, job_id)
    tail = f"Finishes {_time(farm, new_stop.finish)}, {_deadline_tail(farm, new_stop)}"
    if old_stop is None:
        counts["moved"] += 1
        return f"{label}: new, goes to {_tractor_name(farm, new_stop.tractor)}. {tail}"
    if old_stop.tractor != new_stop.tractor:
        counts["moved"] += 1
        return _moved_sentence(farm, world, label, job_id, old_stop, new_stop, tail)
    if abs(new_stop.finish - old_stop.finish) > 1:
        counts["retimed"] += 1
        return (f"{label}: stays with {_tractor_name(farm, new_stop.tractor)}, "
                f"now finishes {_time(farm, new_stop.finish)} (was {_time(farm, old_stop.finish)}), "
                f"{_deadline_tail(farm, new_stop)}")
    counts["unchanged"] += 1
    return None


def _moved_sentence(farm: Farm, world: WorldState, label: str, job_id: str, old_stop: Stop,
                    new_stop: Stop, tail: str) -> str:
    done = _done_percent(farm, world, job_id)
    what = f"the remaining {_remaining_percent(farm, world, job_id)}% moves" if done > 0 else "moves"
    return (f"{label}: {what} from {_tractor_name(farm, old_stop.tractor)} "
            f"to {_tractor_name(farm, new_stop.tractor)}. {tail}")
