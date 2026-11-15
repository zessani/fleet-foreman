"""Ahmed's Phase 2 bridge, dashboard, and breakdown integration tests."""

from __future__ import annotations

import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from bridge.agopenweb import (
    AgOpenWebError,
    AgOpenWebLink,
    AgOpenWebTelemetry,
    decode_telemetry,
    farm_xy_to_latlon,
)
from foreman.day import Day

ROOT = Path(__file__).resolve().parent.parent


class FakeClient:
    def __init__(self, sim_enabled: bool | None = True) -> None:
        self.telemetry = SimpleNamespace(sim_enabled=sim_enabled)
        self.commands: list[tuple[str, str | None]] = []
        self.connected = False
        self.closed = False

    def connect(self) -> None:
        self.connected = True

    def send(self, command: str, argument: str | None = None) -> None:
        self.commands.append((command, argument))

    def close(self) -> None:
        self.closed = True


def state(status: str = "traveling", x: float = 1.2, y: float = 2.4) -> dict:
    return {
        "foreman": {
            "tractors": [
                {"id": "T1", "name": "Atlas", "x": x, "y": y,
                 "status": status, "job": "J1", "heading": 45.0}
            ]
        }
    }


def test_link_acquires_control_heartbeats_and_updates_position() -> None:
    client = FakeClient()
    link = AgOpenWebLink(client, "T1", origin_lat=33.0, origin_lon=-112.0)

    link.start()
    link.update(state())

    assert client.connected
    assert client.commands[:2] == [
        ("control.acquire", "Foreman AgOpenWeb Bridge"),
        ("control.presence", None),
    ]
    assert client.commands[2] == ("control.presence", None)
    command, coordinates = client.commands[3]
    assert command == "sim.setCoords"
    latitude, longitude = map(float, coordinates.split(","))
    assert latitude > 33.0
    assert longitude > -112.0


def test_link_stops_once_on_breakdown_and_resumes_after_repair() -> None:
    client = FakeClient()
    link = AgOpenWebLink(client, "T1")
    link.start()
    link.update(state())
    link.update(state("broken"))
    link.update(state("broken"))
    link.update(state("traveling", 1.3, 2.5))

    assert client.commands.count(("sim.stop", None)) == 1
    assert sum(command == "sim.setCoords" for command, _ in client.commands) == 2


def test_link_enables_a_disabled_agopenweb_simulator_once() -> None:
    client = FakeClient(sim_enabled=False)
    link = AgOpenWebLink(client, "T1")
    link.start()
    link.update(state())
    link.update(state())

    assert client.commands.count(("sim.toggleEnable", None)) == 1


def test_unknown_tractor_is_reported_clearly() -> None:
    link = AgOpenWebLink(FakeClient(), "missing")
    with pytest.raises(AgOpenWebError, match="missing"):
        link.update(state())


def test_local_farm_projection_uses_kilometres() -> None:
    latitude, longitude = farm_xy_to_latlon(1.0, 1.0, 0.0, 0.0)
    assert latitude == pytest.approx(1 / 110.574)
    assert longitude == pytest.approx(1 / 111.320)


def test_decodes_agopenweb_tick_prefix() -> None:
    frame = struct.pack("<Bqddff", 2, 4, 12.5, -3.25, 91.0, 7.5)
    telemetry = decode_telemetry(frame, AgOpenWebTelemetry())
    assert telemetry.easting == 12.5
    assert telemetry.northing == -3.25
    assert telemetry.heading == 91.0
    assert telemetry.speed_kph == 7.5


def test_decodes_agopenweb_status_simulator_fields() -> None:
    def wire_string(value: str) -> bytes:
        encoded = value.encode()
        return struct.pack("<i", len(encoded)) + encoded

    frame = bytearray(struct.pack("<Bi", 5, 4))
    frame += wire_string("RTK")
    frame += struct.pack("<fiBBBBB", 0.1, 18, 1, 1, 1, 1, 1)
    frame += wire_string("imu") + wire_string("steer") + wire_string("machine")
    frame += struct.pack("<BBBB", 1, 1, 1, 1)
    frame += wire_string("J1")
    frame += struct.pack("<dddffBffB", 123.0, 33.1, -112.2, 340.0, 0.8, 1, 9.5, -2.5, 0)

    telemetry = decode_telemetry(bytes(frame), AgOpenWebTelemetry())
    assert telemetry.sim_enabled is True
    assert telemetry.latitude == pytest.approx(33.1)
    assert telemetry.longitude == pytest.approx(-112.2)
    assert telemetry.speed_kph == pytest.approx(9.5)
    assert telemetry.steer_angle == pytest.approx(-2.5)


def test_dashboard_has_live_breakdown_control() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="break-tractor"' in html
    assert 'id="break-button"' in html
    assert 'JSON.stringify({ type: "breakdown", tractor })' in html
    assert 'fetch("/api/event"' in html


def test_dashboard_breakdown_replans_and_preserves_progress() -> None:
    raw = __import__("json").loads(
        (ROOT / "scenarios" / "example_farm.json").read_text(encoding="utf-8")
    )
    raw["events"] = []
    day = Day(raw)
    day.advance(80)
    working = next(
        tractor_id for tractor_id, tractor in day.foreman.sim.tractors.items()
        if tractor.status()["state"] == "working"
    )
    job_id = day.foreman.sim.tractors[working].status()["job"]
    progress_before = day.foreman.sim.jobs[job_id].remaining_ha
    plan_before = day.foreman.plan_no

    assert day.apply({"type": "breakdown", "tractor": working})["ok"]
    day.advance(1.0)

    assert day.foreman.plan_no > plan_before
    assert working in day.foreman.watch.down
    assert day.foreman.sim.jobs[job_id].remaining_ha == pytest.approx(progress_before)
    reassigned = day.foreman.plan.stop_for(job_id)
    assert reassigned is not None
    assert reassigned.tractor != working
