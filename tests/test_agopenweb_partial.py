"""One tractor in AgOpenWeb, the others simulated, in the same run."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import foreman.agopenweb_adapter as adapter_module
from bridge.agopenweb import AgOpenWebError
from foreman.agopenweb_adapter import AgOpenWebTractorAdapter, build_agopenweb_adapters
from foreman.day import Day
from foreman.model import farm_from_dict

ROOT = Path(__file__).resolve().parent.parent
URL = "ws://127.0.0.1:5174/ws"


def sample_raw() -> dict:
    return json.loads((ROOT / "scenarios" / "farms" / "sample_farm.json").read_text())


class FakeSocket:
    """A fake AgOpenWeb host. `down` makes it refuse connections, like a closed port."""

    def __init__(self, url: str = URL) -> None:
        self.down = False
        self.commands: list[tuple] = []
        self.telemetry = SimpleNamespace(
            connected=False, sim_enabled=True, latitude=33.4484, longitude=-112.0740,
            heading=0.0, speed_kph=0.0, worked_area_sqm=0.0, autosteer_engaged=False,
            sections_active=False, recorded_path_playing=False, active_field="",
            guidance_active=False,
        )

    def connect(self) -> None:
        if self.down:
            self.telemetry.connected = False
            raise AgOpenWebError("host is down")
        self.telemetry.connected = True

    def send(self, command: str, argument: str | None = None) -> None:
        self.commands.append((command, argument))

    def close(self) -> None:
        self.telemetry.connected = False


@pytest.fixture
def sockets(monkeypatch) -> list[FakeSocket]:
    made: list[FakeSocket] = []

    def factory(url: str) -> FakeSocket:
        made.append(FakeSocket(url))
        return made[-1]

    monkeypatch.setattr(adapter_module, "AgOpenWebSocket", factory)
    return made


def live_day(urls: dict[str, str]) -> Day:
    return Day(sample_raw(), use_truth=False, agopenweb_urls=urls)


def test_one_url_builds_one_adapter_and_leaves_the_rest_simulated(sockets) -> None:
    day = live_day({"T1": URL})
    run = day.foreman
    assert len(sockets) == 1
    assert run.live_ids == {"T1"}
    assert isinstance(run.tractors["T1"], AgOpenWebTractorAdapter)
    assert run.tractors["T2"] is run.sim.tractors["T2"]
    assert run.tractors["T3"] is run.sim.tractors["T3"]
    assert day.baseline.live_ids == set()
    day.foreman.close()


def test_simulated_tractors_still_finish_their_jobs(sockets) -> None:
    day = live_day({"T1": URL})
    day.advance(60)
    assert day.now == pytest.approx(60.0)
    day.advance(400)
    done_by_sim = [s for s in day.foreman.sim.jobs.values()
                   if s.status == "done" and s.tractor in ("T2", "T3")]
    assert done_by_sim
    assert day.foreman.sim.tractors["T2"].minutes["work"] > 0
    assert day.foreman.sim.totals()["violations"] == 0
    day.foreman.close()


def test_dropped_live_tractor_is_replanned_onto_simulated_ones(sockets) -> None:
    day = live_day({"T1": URL})
    assert day.foreman.plan.routes.get("T1")
    day.advance(5)
    sockets[0].down = True
    sockets[0].telemetry.connected = False  # the real socket flips this itself
    day.advance(5)
    assert "T1" in day.foreman.watch.down
    assert not day.foreman.plan.routes.get("T1")
    planned = {stop.job for route in day.foreman.plan.routes.values() for stop in route}
    open_jobs = {i for i, s in day.foreman.sim.jobs.items() if s.status != "done"}
    assert open_jobs <= planned | set(day.foreman.plan.held)
    assert day.foreman.safety["passed"] is True
    day.foreman.close()


def test_unknown_tractor_id_is_rejected(sockets) -> None:
    farm = farm_from_dict(sample_raw())
    with pytest.raises(ValueError, match="unknown tractor T9"):
        build_agopenweb_adapters(farm, {"T9": URL}, 33.4484, -112.0740)


def test_full_map_still_works(sockets) -> None:
    urls = {"T1": "ws://a/ws", "T2": "ws://b/ws", "T3": "ws://c/ws"}
    day = live_day(urls)
    assert day.foreman.live_ids == {"T1", "T2", "T3"}
    day.advance(10)
    assert day.now == pytest.approx(10.0)
    day.foreman.close()
