"""Basic rule tests (Vishva), checked from the dashboard state of CLAUDE.md section 8c.

Each test runs whole days through the real engine, reads the state the
dashboard sees every few simulated minutes, and checks the rules from the
outside, without trusting the planner:

1. A tractor only gets a job if it owns the right implement and has the power to pull it.
2. Only one tractor works a field at a time.
3. No tractor runs out of fuel.
4. Finished jobs are never redone, and work already done is never lost.

Both worlds are checked: Foreman and the first come, first served baseline.
Farms: the hand-made ones in scenarios/, plus random farms from tools/make_farm.py,
plus breakdowns of every tractor at different times.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from foreman.engine import Engine

ROOT = Path(__file__).resolve().parent.parent
STEP_MIN = 5.0
WORLDS = ("foreman", "baseline")

sys.path.insert(0, str(ROOT / "tools"))
_spec = importlib.util.spec_from_file_location("make_farm", ROOT / "tools" / "make_farm.py")
_make_farm_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_make_farm_module)
make_farm = _make_farm_module.make_farm

HAND_MADE = sorted(str(p.relative_to(ROOT / "scenarios")) for p in (ROOT / "scenarios").rglob("*.json"))


# ---- running a day -------------------------------------------------------

def engine_for(raw: dict, tmp_path: Path) -> Engine:
    path = tmp_path / "farm.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return Engine(scenario=path)


def states_of_day(engine: Engine, events_at: dict[float, list[dict]] | None = None):
    """Every STEP_MIN simulated minutes, the dashboard state. Events are sent at their minute."""
    events_at = dict(events_at or {})
    yield engine.state()
    while not engine.finished:
        for minute in sorted(m for m in events_at if m <= engine.now):
            for event in events_at.pop(minute):
                assert engine.event(event)["ok"], event
        engine.advance(STEP_MIN)
        yield engine.state()


# ---- the four rules, read from one state ---------------------------------

class RuleChecker:
    """Checks one world's blocks of section 8c, state after state, and remembers history."""

    def __init__(self, raw: dict, world: str) -> None:
        self.world = world
        self.implements = {imp["id"]: imp for imp in raw["implements"]}
        self.tractors = {t["id"]: t for t in raw["tractors"]}
        self.job_field = {j["id"]: j["field"] for j in raw["jobs"]}
        self.job_type = {j["id"]: j["type"] for j in raw["jobs"]}
        self.done: dict[str, dict] = {}     # job id -> its job row when first seen done
        self.done_rows: dict[str, dict] = {}  # job id -> its plan row when first seen done
        self.best_pct: dict[str, float] = {}

    def check(self, state: dict) -> None:
        block = state[self.world]
        where = f"[{self.world} at {state['clock']}]"
        jobs = {row["id"]: row for row in block["jobs"]}
        # Jobs added by an event are not in the farm file.
        for row in block["jobs"]:
            self.job_field.setdefault(row["id"], row["field"])
            self.job_type.setdefault(row["id"], row["type"])

        self.right_implement_and_power(block, where)
        self.one_tractor_per_field(block, jobs, where)
        self.no_tractor_out_of_fuel(block, where)
        self.finished_work_is_kept(block, jobs, where)

    def right_implement_and_power(self, block: dict, where: str) -> None:
        for row in block["plan"]:
            tractor = self.tractors[row["tractor"]]
            implement = self.implements.get(row["implement"])
            job = row["job"]
            assert implement is not None, f"{where} {job}: unknown implement {row['implement']}"
            assert implement["type"] == self.job_type[job], \
                f"{where} {job} is {self.job_type[job]} but gets {implement['id']}"
            assert row["implement"] in tractor["implements"], \
                f"{where} {job}: {tractor['id']} does not own {row['implement']}"
            assert tractor["hp"] >= implement["min_hp"], \
                f"{where} {job}: {tractor['id']} has {tractor['hp']} hp, {row['implement']} needs {implement['min_hp']}"
        for tractor in block["tractors"]:
            if tractor["status"] == "working":
                mounted = self.implements[tractor["mounted"]]
                assert mounted["type"] == self.job_type[tractor["job"]], \
                    f"{where} {tractor['id']} works {tractor['job']} with {tractor['mounted']}"

    def one_tractor_per_field(self, block: dict, jobs: dict, where: str) -> None:
        # What is happening now.
        working: dict[str, str] = {}
        for tractor in block["tractors"]:
            if tractor["status"] == "working":
                field = self.job_field[tractor["job"]]
                assert field not in working, \
                    f"{where} {working.get(field)} and {tractor['id']} both work {field}"
                working[field] = tractor["id"]
        # What is planned: work windows on the same field must not overlap.
        by_field: dict[str, list[dict]] = {}
        for row in block["plan"]:
            if jobs[row["job"]]["status"] not in ("done", "held"):
                by_field.setdefault(self.job_field[row["job"]], []).append(row)
        for field, rows in by_field.items():
            rows.sort(key=lambda r: r["start"])
            for first, second in zip(rows, rows[1:]):
                assert second["start"] >= first["finish"] - 0.11, \
                    f"{where} {first['job']} and {second['job']} overlap on {field}"

    def no_tractor_out_of_fuel(self, block: dict, where: str) -> None:
        for tractor in block["tractors"]:
            assert tractor["fuel_pct"] > 0, f"{where} {tractor['id']} ran out of fuel"

    def finished_work_is_kept(self, block: dict, jobs: dict, where: str) -> None:
        plan = {row["job"]: row for row in block["plan"]}
        for job_id, row in jobs.items():
            # Work done is never lost: progress only goes up.
            assert row["pct"] >= self.best_pct.get(job_id, 0), \
                f"{where} {job_id} fell from {self.best_pct[job_id]}% to {row['pct']}%"
            self.best_pct[job_id] = row["pct"]

            if job_id in self.done:
                first = self.done[job_id]
                assert row["status"] == "done", f"{where} {job_id} was done, now {row['status']}"
                assert row["pct"] == 100 and row["tractor"] == first["tractor"], \
                    f"{where} finished job {job_id} changed"
                # A finished job is never planned again.
                assert plan.get(job_id) == self.done_rows.get(job_id), \
                    f"{where} finished job {job_id} was planned again"
            elif row["status"] == "done":
                self.done[job_id] = dict(row)
                self.done_rows[job_id] = plan.get(job_id)
        # Each job is planned at most once.
        planned = [row["job"] for row in block["plan"]]
        assert len(planned) == len(set(planned)), f"{where} a job is planned twice"


def run_and_check(raw: dict, tmp_path: Path, events_at: dict | None = None) -> dict:
    engine = engine_for(raw, tmp_path)
    checkers = [RuleChecker(raw, world) for world in WORLDS]
    state = None
    for state in states_of_day(engine, events_at):
        for checker in checkers:
            checker.check(state)
    for world in WORLDS:
        # The simulator's own referee must agree: no rule broken at any work start.
        assert state[world]["totals"]["violations"] == 0, f"{world}: referee counted violations"
    return state


# ---- the tests -----------------------------------------------------------

@pytest.mark.parametrize("name", HAND_MADE)
def test_rules_hold_on_hand_made_farms(name: str, tmp_path: Path) -> None:
    raw = json.loads((ROOT / "scenarios" / name).read_text(encoding="utf-8"))
    run_and_check(raw, tmp_path)


@pytest.mark.parametrize("seed", range(20))
def test_rules_hold_on_random_farms(seed: int, tmp_path: Path) -> None:
    raw = make_farm(tractors=2 + seed % 3, fields=5 + seed % 8, jobs=6 + seed % 10, seed=500 + seed)
    run_and_check(raw, tmp_path)


@pytest.mark.parametrize("tractor", ["T1", "T2", "T3"])
@pytest.mark.parametrize("minute", [30, 120, 240])
def test_rules_hold_when_any_tractor_breaks(tractor: str, minute: int, tmp_path: Path) -> None:
    raw = json.loads((ROOT / "scenarios" / "farms" / "sample_farm.json").read_text(encoding="utf-8"))
    raw["events"] = []  # only the breakdown under test
    run_and_check(raw, tmp_path, {minute: [{"type": "breakdown", "tractor": tractor}]})


def _sample_start(tmp_path: Path) -> tuple[dict, dict]:
    raw = json.loads((ROOT / "scenarios" / "farms" / "sample_farm.json").read_text(encoding="utf-8"))
    engine = engine_for(raw, tmp_path)
    engine.advance(60)
    return raw, engine.state()


def _bad(raw: dict, state: dict, change) -> None:
    """The checker must reject the state after `change` breaks a rule."""
    checker = RuleChecker(raw, "foreman")
    checker.check(state)  # the real state passes
    broken = json.loads(json.dumps(state))
    change(broken["foreman"])
    with pytest.raises(AssertionError):
        checker.check(broken)


def test_checker_catches_wrong_implement(tmp_path: Path) -> None:
    raw, state = _sample_start(tmp_path)
    _bad(raw, state, lambda b: b["plan"][0].update(implement="sprayer_24", tractor="T1"))


def test_checker_catches_too_little_power(tmp_path: Path) -> None:
    raw, state = _sample_start(tmp_path)
    raw["tractors"][0]["hp"] = 10
    with pytest.raises(AssertionError):
        RuleChecker(raw, "foreman").check(state)


def test_checker_catches_two_tractors_on_one_field(tmp_path: Path) -> None:
    raw, state = _sample_start(tmp_path)
    def crowd(block):
        rows = [r for r in block["plan"] if r["job"] in ("J4", "J5")]
        rows[1].update(start=rows[0]["start"], finish=rows[0]["finish"])
    _bad(raw, state, crowd)


def test_checker_catches_empty_tank(tmp_path: Path) -> None:
    raw, state = _sample_start(tmp_path)
    _bad(raw, state, lambda b: b["tractors"][0].update(fuel_pct=0))


def test_checker_catches_redone_and_lost_work(tmp_path: Path) -> None:
    raw, state = _sample_start(tmp_path)
    done = [j for j in state["foreman"]["jobs"] if j["status"] == "done"]
    assert done, "expected a finished job by 07:00"
    job_id = done[0]["id"]
    # Redone: a finished job goes back to active.
    _bad(raw, state, lambda b: next(j for j in b["jobs"] if j["id"] == job_id).update(status="active"))
    # Lost: progress drops.
    _bad(raw, state, lambda b: next(j for j in b["jobs"] if j["id"] == job_id).update(pct=40))
    # Planned again with new times.
    _bad(raw, state, lambda b: next(r for r in b["plan"] if r["job"] == job_id).update(start=500, finish=520))


def test_half_done_field_is_finished_once_after_breakdown(tmp_path: Path) -> None:
    """Break the tractor in the middle of a job: the job keeps its progress and finishes once."""
    raw = json.loads((ROOT / "scenarios" / "farms" / "sample_farm.json").read_text(encoding="utf-8"))
    raw["events"] = []
    engine = engine_for(raw, tmp_path)
    checkers = [RuleChecker(raw, world) for world in WORLDS]

    # Run until a till job (long work) is part done, then break that tractor.
    target = None
    for state in states_of_day(engine):
        for checker in checkers:
            checker.check(state)
        half = [j for j in state["foreman"]["jobs"] if j["type"] == "till" and 20 <= j["pct"] <= 80]
        if half:
            target = half[0]
            break
    assert target is not None, "no till job was ever half done"
    assert engine.event({"type": "breakdown", "tractor": target["tractor"]})["ok"]

    final = None
    for final in states_of_day(engine):
        for checker in checkers:
            checker.check(final)
    job = next(j for j in final["foreman"]["jobs"] if j["id"] == target["id"])
    assert job["status"] == "done" and job["pct"] == 100
    assert job["tractor"] != target["tractor"], "the broken tractor cannot have finished it"
    assert final["foreman"]["totals"]["violations"] == 0
