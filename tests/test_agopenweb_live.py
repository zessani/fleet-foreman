"""AgOpenWeb is the live execution source, not a mirror of SimTractor."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from foreman.agopenweb_adapter import (
    AgOpenWebTractorAdapter,
    build_agopenweb_adapters,
    latlon_to_farm_xy,
)
from foreman.model import farm_from_dict
from foreman.nav import RoadMap
from foreman.run import Run
from bridge.agopenweb import AgOpenWebTelemetry, decode_telemetry

ROOT = Path(__file__).resolve().parent.parent


def sample_farm():
    raw = json.loads((ROOT / "scenarios" / "farms" / "sample_farm.json").read_text())
    return farm_from_dict(raw), raw


class FakeAgOpenWeb:
    def __init__(self):
        self.commands = []
        self.telemetry = SimpleNamespace(
            connected=False, sim_enabled=True, latitude=33.4484, longitude=-112.0740,
            heading=90.0, speed_kph=8.0, worked_area_sqm=0.0,
            autosteer_engaged=False, sections_active=False,
            recorded_path_playing=False, active_field="", guidance_active=True,
        )

    def connect(self):
        self.telemetry.connected = True

    def send(self, command, argument=None):
        self.commands.append((command, argument))

    def close(self):
        self.telemetry.connected = False


def test_adapter_dispatches_official_field_job_and_path_commands():
    farm, _ = sample_farm()
    client = FakeAgOpenWeb()
    adapter = AgOpenWebTractorAdapter(farm.tractors["T1"], farm, "ws://tractor-1/ws", client=client)
    job = next(job for job in farm.jobs.values() if job.type == "mow")

    adapter.dispatch([{"job": job.id, "field": job.field, "implement": "mow",
                       "swap": False, "path": [farm.depot, job.field]}], 1)

    commands = dict((name, arg) for name, arg in client.commands if name != "sim.speedUp")
    assert commands["field.startJob"].split("\t") == [
        farm.nodes[job.field].name, job.type, "Dispatched by Foreman", job.id
    ]
    assert commands["recpath.selectFile"] == f"Foreman-{job.id}"
    assert ("recpath.play", None) in client.commands
    assert ("autosteer.toggle", None) in client.commands
    assert ("section.master", None) in client.commands
    adapter.close()


def test_adapter_status_comes_from_agopenweb_telemetry_and_coverage():
    farm, _ = sample_farm()
    client = FakeAgOpenWeb()
    adapter = AgOpenWebTractorAdapter(farm.tractors["T1"], farm, "ws://tractor-1/ws", client=client)
    job = next(job for job in farm.jobs.values() if job.type == "mow")
    adapter.dispatch([{"job": job.id, "field": job.field, "implement": "mow",
                       "swap": False, "path": [farm.depot, job.field]}], 1)
    client.telemetry.latitude = 33.4494
    client.telemetry.longitude = -112.0730
    client.telemetry.worked_area_sqm = job.area_ha * 10000 * 0.42
    client.telemetry.sections_active = True

    status = adapter.status()

    expected_x, expected_y = latlon_to_farm_xy(33.4494, -112.0730, 33.4484, -112.0740)
    assert status["x"] == pytest.approx(expected_x)
    assert status["y"] == pytest.approx(expected_y)
    assert status["pct"] == pytest.approx(42.0)
    assert status["state"] == "working"
    assert status["agopenweb"]["connected"] is True
    adapter.close()


class StubAdapter:
    def __init__(self, tractor, job):
        self.tractor, self.job = tractor, job
        self.pct = 25.0
        self.minutes = {key: 0.0 for key in ("work", "travel", "service", "wait", "idle", "unassigned")}

    def status(self):
        return {"id": self.tractor.id, "x": 1.0, "y": 2.0, "node": self.job.field,
                "heading": 20.0, "state": "working", "job": self.job.id,
                "pct": self.pct, "fuel": self.tractor.fuel, "mounted": self.tractor.mounted}

    def dispatch(self, stops, plan_no): pass
    def stop(self): pass
    def break_down(self): pass
    def repair(self): pass
    def close(self): pass


def test_live_run_updates_job_from_adapter_without_stepping_simtractor():
    farm, raw = sample_farm()
    road_map = RoadMap(raw)
    job = next(iter(farm.jobs.values()))
    adapters = {tractor_id: StubAdapter(tractor, job) for tractor_id, tractor in farm.tractors.items()}
    run = Run(farm, road_map, set(), adapters=adapters)

    run.advance(2.0)

    assert run.sim.now == pytest.approx(2.0)
    assert run.sim.jobs[job.id].remaining_ha == pytest.approx(job.area_ha * 0.75)
    assert all(tractor.phase is None for tractor in run.sim.tractors.values())
    adapters["T1"].pct = 100.0
    run.advance(1.0)
    assert run.sim.jobs[job.id].status == "done"
    assert any(event["type"] == "job_done" for event in run.sim.events)


def test_fleet_requires_one_distinct_agopenweb_host_per_tractor():
    farm, _ = sample_farm()
    same = {tractor_id: "ws://same-host:5174/ws" for tractor_id in farm.tractors}
    with pytest.raises(ValueError, match="different AgOpenWeb host"):
        build_agopenweb_adapters(farm, same, 33.4484, -112.0740)


def test_control_state_only_counts_authority_held_by_this_bridge():
    def wire(value):
        data = value.encode()
        return struct.pack("<i", len(data)) + data

    telemetry = decode_telemetry(bytes([7]) + wire("bridge-client") + bytes([0]),
                                 AgOpenWebTelemetry())
    telemetry = decode_telemetry(bytes([6, 1]) + wire("browser-client") + wire("Operator"),
                                 telemetry)
    assert telemetry.control_held is False
    telemetry = decode_telemetry(bytes([6, 1]) + wire("bridge-client") + wire("Foreman T1"),
                                 telemetry)
    assert telemetry.control_held is True
