"""Live TractorAdapter backed by one AgOpenWeb host.

AgOpenWeb owns motion, steering, GPS and worked-area measurement.  Foreman
only dispatches a field job and normalizes the returned telemetry.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any

from bridge.agopenweb import (
    DEFAULT_ORIGIN_LAT,
    DEFAULT_ORIGIN_LON,
    AgOpenWebError,
    AgOpenWebSocket,
    farm_xy_to_latlon,
)

LOG = logging.getLogger("foreman.agopenweb.adapter")


def latlon_to_farm_xy(latitude: float, longitude: float, origin_lat: float,
                      origin_lon: float) -> tuple[float, float]:
    """Inverse of bridge.farm_xy_to_latlon; result is local kilometres."""
    y_km = (latitude - origin_lat) * 110.574
    scale = 111.320 * math.cos(math.radians(origin_lat))
    return (longitude - origin_lon) * scale, y_km


class AgOpenWebTractorAdapter:
    """Translate Foreman stops to AgOpenWeb commands and expose live status."""

    def __init__(self, tractor: Any, farm: Any, url: str,
                 origin_lat: float = DEFAULT_ORIGIN_LAT,
                 origin_lon: float = DEFAULT_ORIGIN_LON,
                 client: Any | None = None) -> None:
        self.tractor = tractor
        self.farm = farm
        self.id = tractor.id
        self.url = url
        self.origin_lat = origin_lat
        self.origin_lon = origin_lon
        self.client = client or AgOpenWebSocket(url)
        self.plan_no = 0
        self.queue: list[dict] = []
        self.active: dict | None = None
        self._completed_pending: str | None = None
        self._broken = False
        self._connected = False
        self._closed = False
        self._lock = threading.RLock()
        self._heartbeat: threading.Thread | None = None
        self._last_x, self._last_y = self._start_xy()
        self._node = tractor.node
        self._mounted = tractor.mounted
        self._fuel = tractor.fuel
        self._coverage_floor_sqm = 0.0
        self.fault_at: float | None = None
        self.minutes = {"work": 0.0, "travel": 0.0, "service": 0.0, "wait": 0.0,
                        "idle": 0.0, "unassigned": 0.0}

    def _start_xy(self) -> tuple[float, float]:
        node = self.farm.nodes[self.tractor.node]
        return float(node.x), float(node.y)

    def connect(self) -> None:
        with self._lock:
            if self._connected and self.client.telemetry.connected:
                return
            self.client.connect()
            self.client.send("control.acquire", f"Foreman {self.id}")
            self.client.send("control.presence")
            if hasattr(self.client.telemetry, "control_held"):
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline and not self.client.telemetry.control_held:
                    time.sleep(0.05)
                if not self.client.telemetry.control_held:
                    raise AgOpenWebError(
                        f"{self.id} could not acquire AgOpenWeb control; release the current operator"
                    )
            latitude, longitude = farm_xy_to_latlon(
                self._last_x, self._last_y, self.origin_lat, self.origin_lon
            )
            # Initial placement only. After this, AgOpenWeb owns movement.
            self.client.send("sim.setCoords", f"{latitude:.8f},{longitude:.8f}")
            self._connected = True
            if self._heartbeat is None or not self._heartbeat.is_alive():
                self._heartbeat = threading.Thread(target=self._heartbeat_loop, daemon=True)
                self._heartbeat.start()

    def _heartbeat_loop(self) -> None:
        while not self._closed:
            try:
                if self.client.telemetry.connected:
                    self.client.send("control.presence")
                else:
                    self._connected = False
            except AgOpenWebError:
                self._connected = False
            time.sleep(0.5)

    def dispatch(self, stops: list[dict], plan_no: int) -> None:
        with self._lock:
            if plan_no < self.plan_no:
                return
            self.plan_no = plan_no
            incoming = [dict(stop) for stop in stops]
            if self.active and incoming and incoming[0].get("job") == self.active.get("job"):
                incoming.pop(0)
            elif self.active:
                self._halt_work()
                self.active = None
            self.queue = incoming
            if self.active is None and not self._broken:
                self._start_next()

    def _start_next(self) -> None:
        if not self.queue:
            return
        try:
            self.connect()
        except (AgOpenWebError, OSError) as exc:
            LOG.warning("%s cannot start queued work: %s", self.id, exc)
            return
        self.active = self.queue.pop(0)
        stop = self.active
        job = self.farm.jobs[stop["job"]]
        field_name = self.farm.nodes[job.field].name
        path_name = stop.get("agopenweb_path") or f"Foreman-{job.id}"
        # Keep the previous job's area from being counted while AgOpenWeb opens
        # the new job and resets its worked-area counter asynchronously.
        self._coverage_floor_sqm = self.client.telemetry.worked_area_sqm
        if self.client.telemetry.sim_enabled is False:
            self.client.send("sim.toggleEnable")
        if stop.get("swap"):
            self._mounted = stop["implement"]
        # Official format: field<TAB>workType<TAB>notes<TAB>taskName.
        argument = "\t".join((field_name, job.type, "Dispatched by Foreman", job.id))
        self.client.send("field.startJob", argument)
        self.client.send("recpath.selectFile", path_name)
        self.client.send("recpath.play")
        if not self.client.telemetry.autosteer_engaged:
            self.client.send("autosteer.toggle")
        if not self.client.telemetry.sections_active:
            self.client.send("section.master")
        # AgOpenWeb exposes speed as step commands, not an absolute setter.
        for _ in range(max(1, min(12, round(self.tractor.speed_kmh)))):
            self.client.send("sim.speedUp")
        LOG.info("%s executing %s in AgOpenWeb field %s via %s",
                 self.id, job.id, field_name, path_name)

    def _halt_work(self) -> None:
        if not self._connected:
            return
        self.client.send("sim.stop")
        if self.client.telemetry.autosteer_engaged:
            self.client.send("autosteer.toggle")
        if self.client.telemetry.sections_active:
            self.client.send("section.master")
        if self.client.telemetry.recorded_path_playing:
            self.client.send("recpath.turnOff")

    def status(self) -> dict:
        with self._lock:
            if self._completed_pending is not None:
                # The previous sample exposed 100% for one full polling cycle.
                self._completed_pending = None
                self._start_next()
            try:
                self.connect()
            except (AgOpenWebError, OSError) as exc:
                LOG.warning("%s AgOpenWeb unavailable: %s", self.id, exc)
                self._connected = False
            if self.active is None and self.queue and self.client.telemetry.connected and not self._broken:
                self._start_next()
            telemetry = self.client.telemetry
            if telemetry.latitude is not None and telemetry.longitude is not None:
                self._last_x, self._last_y = latlon_to_farm_xy(
                    telemetry.latitude, telemetry.longitude, self.origin_lat, self.origin_lon
                )
            job_id = self.active.get("job") if self.active else None
            pct = 0.0
            if job_id:
                area = self.farm.jobs[job_id].area_ha
                if telemetry.worked_area_sqm < self._coverage_floor_sqm:
                    self._coverage_floor_sqm = telemetry.worked_area_sqm
                covered = max(0.0, telemetry.worked_area_sqm - self._coverage_floor_sqm)
                pct = min(100.0, 100.0 * covered / (area * 10000.0)) if area else 100.0
                if pct >= 99.9:
                    completed = job_id
                    self.active = None
                    self._completed_pending = completed
                    pct = 100.0
                    LOG.info("%s completed %s from AgOpenWeb coverage", self.id, completed)
            if self._broken or not telemetry.connected:
                state = "broken"
            elif job_id and (telemetry.speed_kph or 0) > 0.05:
                state = "working" if telemetry.sections_active else "traveling"
            elif job_id:
                state = "waiting"
            else:
                state = "idle"
            return {
                "id": self.id, "x": self._last_x, "y": self._last_y,
                "node": self.active.get("field") if self.active else self._node,
                "heading": telemetry.heading or 0.0, "state": state, "job": job_id,
                "pct": pct, "fuel": self._fuel, "mounted": self._mounted,
                "speed_kph": telemetry.speed_kph or 0.0,
                "worked_area_sqm": telemetry.worked_area_sqm,
                "agopenweb": {"connected": telemetry.connected, "url": self.url,
                              "field": telemetry.active_field,
                              "autosteer": telemetry.autosteer_engaged,
                              "guidance": telemetry.guidance_active},
            }

    def stop(self) -> None:
        with self._lock:
            self.queue = []
            self._halt_work()
            self.active = None

    def break_down(self) -> None:
        with self._lock:
            self._broken = True
            self.fault_at = time.monotonic() / 60.0
            self.stop()

    def repair(self) -> None:
        with self._lock:
            self._broken = False
            self.fault_at = None
            if self.queue:
                self._start_next()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            try:
                self._halt_work()
                self.client.send("control.release")
            except AgOpenWebError:
                pass
            self.client.close()


def build_agopenweb_adapters(farm: Any, urls: dict[str, str], origin_lat: float,
                             origin_lon: float) -> dict[str, AgOpenWebTractorAdapter]:
    # A partial map is fine: tractors left out stay on the simulator.
    unknown = sorted(set(urls) - set(farm.tractors))
    if unknown:
        raise ValueError("AgOpenWeb URL given for unknown tractor " + ", ".join(unknown)
                         + "; farm tractors are " + ", ".join(sorted(farm.tractors)))
    endpoints = list(urls.values())
    if len(set(endpoints)) != len(endpoints):
        raise ValueError("each tractor needs a different AgOpenWeb host; one host controls one tractor")
    return {
        tractor_id: AgOpenWebTractorAdapter(
            farm.tractors[tractor_id], farm, urls[tractor_id], origin_lat, origin_lon
        )
        for tractor_id in sorted(urls)
    }
