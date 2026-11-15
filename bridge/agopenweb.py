"""Mirror one Foreman tractor into AgOpenWeb's built-in simulator.

Run this beside Foreman and AgOpenWeb::

    python -m bridge.agopenweb --tractor T1 --verbose

The bridge uses AgOpenWeb's real ``/ws`` protocol.  It takes remote-control
authority, refreshes the 1.5 second deadman heartbeat, and sends the selected
tractor's position to ``sim.setCoords``.  No third-party WebSocket package is
needed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import math
import os
import socket
import ssl
import struct
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

LOG = logging.getLogger("foreman.agopenweb")
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
DEFAULT_ORIGIN_LAT = 33.4484
DEFAULT_ORIGIN_LON = -112.0740


class AgOpenWebError(RuntimeError):
    """The AgOpenWeb connection or wire protocol failed."""


@dataclass
class AgOpenWebTelemetry:
    """Small useful subset of AgOpenWeb's binary status feed."""

    connected: bool = False
    sim_enabled: bool | None = None
    latitude: float | None = None
    longitude: float | None = None
    speed_kph: float | None = None
    steer_angle: float | None = None
    easting: float | None = None
    northing: float | None = None
    heading: float | None = None
    worked_area_sqm: float = 0.0
    job_name: str = ""
    active_field: str = ""
    guidance_active: bool = False
    autosteer_engaged: bool = False
    sections_active: bool = False
    recorded_path_playing: bool = False
    control_held: bool = False
    client_id: str = ""
    control_holder_id: str = ""
    control_holder_name: str = ""


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    def unpack(self, fmt: str) -> tuple[Any, ...]:
        size = struct.calcsize(fmt)
        if self.offset + size > len(self.data):
            raise AgOpenWebError("short AgOpenWeb binary frame")
        values = struct.unpack_from(fmt, self.data, self.offset)
        self.offset += size
        return values

    def string(self) -> str:
        (length,) = self.unpack("<i")
        if length < 0 or self.offset + length > len(self.data):
            raise AgOpenWebError("invalid AgOpenWeb string length")
        value = self.data[self.offset:self.offset + length]
        self.offset += length
        return value.decode("utf-8", errors="replace")

    def skip(self, size: int) -> None:
        if size < 0 or self.offset + size > len(self.data):
            raise AgOpenWebError("short AgOpenWeb binary frame")
        self.offset += size


def decode_telemetry(frame: bytes, current: AgOpenWebTelemetry | None = None) -> AgOpenWebTelemetry:
    """Decode the pose/status prefix published by AgOpenWeb's WireCodec."""
    telemetry = current or AgOpenWebTelemetry()
    if not frame:
        return telemetry
    reader = _Reader(frame)
    (kind,) = reader.unpack("<B")
    if kind == 2:  # WireCodec.Tick
        _, easting, northing, heading, speed = reader.unpack("<qddff")
        telemetry.easting = easting
        telemetry.northing = northing
        telemetry.heading = heading
        telemetry.speed_kph = speed
        # Older hosts/tests published only this pose prefix.
        if reader.offset == len(reader.data):
            return telemetry
        reader.unpack("<B")  # GPS fix
        (section_count,) = reader.unpack("<i")
        if section_count < 0 or section_count > 256:
            raise AgOpenWebError("invalid AgOpenWeb section count")
        sections = reader.data[reader.offset:reader.offset + section_count]
        reader.skip(section_count)
        telemetry.sections_active = any(value != 0 for value in sections)
        reader.unpack("<f")  # cross-track error
        (guidance,) = reader.unpack("<B")
        telemetry.guidance_active = bool(guidance)
        reader.string()  # line label
        reader.string()  # active track name
        reader.unpack("<ddfB")  # tool pose and ready
        (autosteer,) = reader.unpack("<B")
        telemetry.autosteer_engaged = bool(autosteer)
    elif kind == 5:  # WireCodec.Status
        reader.unpack("<i")
        reader.string()
        reader.unpack("<fi")
        reader.unpack("<BBBBB")
        reader.string()
        reader.string()
        reader.string()
        reader.unpack("<BBBB")
        telemetry.job_name = reader.string()
        (telemetry.worked_area_sqm,) = reader.unpack("<d")
        latitude, longitude = reader.unpack("<dd")
        reader.unpack("<ff")
        enabled, speed, steer, _ = reader.unpack("<BffB")
        telemetry.latitude = latitude
        telemetry.longitude = longitude
        telemetry.sim_enabled = bool(enabled)
        telemetry.speed_kph = speed
        telemetry.steer_angle = steer
    elif kind == 6:  # WireCodec.ControlState
        (held,) = reader.unpack("<B")
        telemetry.control_holder_id = reader.string()
        telemetry.control_holder_name = reader.string()
        telemetry.control_held = bool(held) and telemetry.control_holder_id == telemetry.client_id
    elif kind == 7:  # WireCodec.Hello
        telemetry.client_id = reader.string()
    elif kind == 12:  # WireCodec.FieldOps
        (fields,) = reader.unpack("<i")
        for _ in range(fields):
            reader.string()
            reader.unpack("<Bdd")
        (jobs,) = reader.unpack("<i")
        for _ in range(jobs):
            reader.string(); reader.string(); reader.string()
            reader.unpack("<i")
            reader.string(); reader.string()
        (suggestions,) = reader.unpack("<i")
        for _ in range(suggestions): reader.string()
        (iso_files,) = reader.unpack("<i")
        for _ in range(iso_files): reader.string()
        (kml_files,) = reader.unpack("<i")
        for _ in range(kml_files): reader.string()
        telemetry.active_field = reader.string()
    elif kind == 16:  # WireCodec.RecordedPath
        (files,) = reader.unpack("<i")
        for _ in range(files): reader.string()
        reader.unpack("<B")  # recording
        (playing,) = reader.unpack("<B")
        telemetry.recorded_path_playing = bool(playing)
    return telemetry


class AgOpenWebSocket:
    """Minimal RFC 6455 client for AgOpenWeb's command/status socket."""

    def __init__(self, url: str = "ws://127.0.0.1:5174/ws", timeout: float = 3.0) -> None:
        self.url = url
        self.timeout = timeout
        self.telemetry = AgOpenWebTelemetry()
        self._socket: socket.socket | ssl.SSLSocket | None = None
        self._send_lock = threading.Lock()
        self._receiver: threading.Thread | None = None
        self._stop = threading.Event()
        self._recv_buffer = bytearray()

    def connect(self) -> None:
        if self._socket is not None:
            return
        parsed = urlparse(self.url)
        if parsed.scheme not in ("ws", "wss") or not parsed.hostname:
            raise AgOpenWebError("AgOpenWeb URL must start with ws:// or wss://")
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        raw = socket.create_connection((parsed.hostname, port), timeout=self.timeout)
        sock: socket.socket | ssl.SSLSocket = raw
        if parsed.scheme == "wss":
            sock = ssl.create_default_context().wrap_socket(raw, server_hostname=parsed.hostname)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        host = parsed.hostname if port in (80, 443) else f"{parsed.hostname}:{port}"
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        sock.sendall(request)
        response, remainder = self._read_headers(sock)
        status = response.split("\r\n", 1)[0]
        expected = base64.b64encode(
            hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()
        ).decode("ascii")
        headers = {
            name.strip().lower(): value.strip()
            for line in response.split("\r\n")[1:]
            if ":" in line
            for name, value in [line.split(":", 1)]
        }
        if " 101 " not in f" {status} " or headers.get("sec-websocket-accept") != expected:
            sock.close()
            raise AgOpenWebError(f"AgOpenWeb rejected WebSocket upgrade: {status}")
        # The receiver owns blocking reads. close() shuts the socket from the
        # main thread, which unblocks it without risking a timeout halfway
        # through a binary frame.
        sock.settimeout(None)
        self._socket = sock
        self._recv_buffer = bytearray(remainder)
        self.telemetry.connected = True
        self._stop.clear()
        self._receiver = threading.Thread(target=self._receive_loop, daemon=True)
        self._receiver.start()
        LOG.info("connected to AgOpenWeb at %s", self.url)

    @staticmethod
    def _read_headers(sock: socket.socket | ssl.SSLSocket) -> tuple[str, bytes]:
        data = bytearray()
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                raise AgOpenWebError("AgOpenWeb closed during WebSocket handshake")
            data.extend(chunk)
            if len(data) > 65536:
                raise AgOpenWebError("AgOpenWeb sent oversized handshake")
        headers, remainder = bytes(data).split(b"\r\n\r\n", 1)
        return headers.decode("iso-8859-1"), remainder

    def send(self, command: str, argument: str | None = None) -> None:
        message = command if argument is None else f"{command}|{argument}"
        self._send_frame(0x1, message.encode("utf-8"))
        LOG.debug("AgOpenWeb command: %s", message)

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        sock = self._socket
        if sock is None:
            raise AgOpenWebError("AgOpenWeb WebSocket is not connected")
        mask = os.urandom(4)
        size = len(payload)
        if size < 126:
            header = bytes((0x80 | opcode, 0x80 | size))
        elif size < 65536:
            header = bytes((0x80 | opcode, 0x80 | 126)) + struct.pack("!H", size)
        else:
            header = bytes((0x80 | opcode, 0x80 | 127)) + struct.pack("!Q", size)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        try:
            with self._send_lock:
                sock.sendall(header + mask + masked)
        except OSError as exc:
            self._mark_disconnected()
            raise AgOpenWebError(f"AgOpenWeb send failed: {exc}") from exc

    def _receive_loop(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    opcode, payload = self._receive_frame()
                except socket.timeout:
                    continue
                if opcode == 0x2:
                    try:
                        decode_telemetry(payload, self.telemetry)
                    except AgOpenWebError:
                        LOG.debug("ignored incomplete AgOpenWeb telemetry frame", exc_info=True)
                elif opcode == 0x9:
                    self._send_frame(0xA, payload)
                elif opcode == 0x8:
                    break
        except (OSError, AgOpenWebError):
            if not self._stop.is_set():
                LOG.warning("AgOpenWeb connection dropped", exc_info=True)
        finally:
            self._mark_disconnected()

    def _receive_frame(self) -> tuple[int, bytes]:
        first = self._recv_exact(2)
        opcode = first[0] & 0x0F
        masked = bool(first[1] & 0x80)
        size = first[1] & 0x7F
        if size == 126:
            (size,) = struct.unpack("!H", self._recv_exact(2))
        elif size == 127:
            (size,) = struct.unpack("!Q", self._recv_exact(8))
        mask = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(size)
        if masked:
            payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        return opcode, payload

    def _recv_exact(self, size: int) -> bytes:
        sock = self._socket
        if sock is None:
            raise AgOpenWebError("AgOpenWeb WebSocket is not connected")
        data = bytearray()
        if self._recv_buffer:
            take = min(size, len(self._recv_buffer))
            data.extend(self._recv_buffer[:take])
            del self._recv_buffer[:take]
        while len(data) < size:
            chunk = sock.recv(size - len(data))
            if not chunk:
                raise AgOpenWebError("AgOpenWeb closed the WebSocket")
            data.extend(chunk)
        return bytes(data)

    def close(self) -> None:
        self._stop.set()
        if self._socket is not None:
            try:
                self._send_frame(0x8, b"")
            except (OSError, AgOpenWebError):
                pass
            try:
                self._socket.close()
            except OSError:
                pass
        self._mark_disconnected()

    def _mark_disconnected(self) -> None:
        self.telemetry.connected = False
        self._socket = None


def farm_xy_to_latlon(
    x_km: float,
    y_km: float,
    origin_lat: float = DEFAULT_ORIGIN_LAT,
    origin_lon: float = DEFAULT_ORIGIN_LON,
) -> tuple[float, float]:
    """Project the farm's local kilometre coordinates around a WGS84 origin."""
    latitude = origin_lat + y_km / 110.574
    longitude_scale = 111.320 * math.cos(math.radians(origin_lat))
    longitude = origin_lon + x_km / longitude_scale
    return latitude, longitude


class AgOpenWebLink:
    """Synchronize one tractor row from ``GET /api/state`` to AgOpenWeb."""

    def __init__(
        self,
        client: Any,
        tractor_id: str,
        origin_lat: float = DEFAULT_ORIGIN_LAT,
        origin_lon: float = DEFAULT_ORIGIN_LON,
    ) -> None:
        self.client = client
        self.tractor_id = tractor_id
        self.origin_lat = origin_lat
        self.origin_lon = origin_lon
        self._last_position: tuple[float, float] | None = None
        self._last_status: str | None = None
        self._enable_requested = False

    def start(self) -> None:
        self.client.connect()
        self.client.send("control.acquire", "Foreman AgOpenWeb Bridge")
        self.client.send("control.presence")
        # A new socket must receive the current point even if the farm tractor
        # has not moved since the previous connection dropped.
        self._last_position = None
        self._enable_requested = False

    def update(self, state: dict[str, Any]) -> dict[str, Any]:
        tractors = state.get("foreman", {}).get("tractors", [])
        tractor = next((item for item in tractors if item.get("id") == self.tractor_id), None)
        if tractor is None:
            raise AgOpenWebError(f"tractor {self.tractor_id!r} is not in Foreman state")

        self.client.send("control.presence")
        telemetry = getattr(self.client, "telemetry", None)
        if telemetry is not None and telemetry.sim_enabled is False and not self._enable_requested:
            self.client.send("sim.toggleEnable")
            self._enable_requested = True
        elif telemetry is not None and telemetry.sim_enabled is True:
            self._enable_requested = False

        status = str(tractor.get("status", "idle"))
        if status == "broken":
            if self._last_status != "broken":
                self.client.send("sim.stop")
                LOG.warning("%s broke down; AgOpenWeb simulator stopped", self.tractor_id)
        else:
            x, y = float(tractor["x"]), float(tractor["y"])
            position = (x, y)
            if position != self._last_position:
                latitude, longitude = farm_xy_to_latlon(
                    x, y, self.origin_lat, self.origin_lon
                )
                self.client.send("sim.setCoords", f"{latitude:.8f},{longitude:.8f}")
                self._last_position = position
                LOG.info(
                    "%s %-10s farm=(%.4f, %.4f) gps=(%.8f, %.8f) job=%s",
                    self.tractor_id, status, x, y, latitude, longitude,
                    tractor.get("job") or "-",
                )
        self._last_status = status
        return tractor

    def close(self) -> None:
        try:
            self.client.send("sim.stop")
            self.client.send("control.release")
        except AgOpenWebError:
            pass
        self.client.close()


def get_foreman_state(url: str, timeout: float = 3.0) -> dict[str, Any]:
    request = Request(url.rstrip("/") + "/api/state", headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def run(args: argparse.Namespace) -> None:
    client = AgOpenWebSocket(args.agopenweb)
    link = AgOpenWebLink(client, args.tractor, args.origin_lat, args.origin_lon)
    try:
        while True:
            started = time.monotonic()
            try:
                if not client.telemetry.connected:
                    link.start()
                link.update(get_foreman_state(args.foreman))
            except Exception as exc:
                LOG.error("sync failed: %s", exc)
            time.sleep(max(0.0, args.interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        LOG.info("stopping bridge")
    finally:
        link.close()


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="Mirror one Foreman tractor in AgOpenWeb")
    cli.add_argument("--tractor", default="T1", help="Foreman tractor id (default: T1)")
    cli.add_argument("--foreman", default="http://127.0.0.1:8000", help="Foreman base URL")
    cli.add_argument("--agopenweb", default="ws://127.0.0.1:5174/ws", help="AgOpenWeb WebSocket URL")
    cli.add_argument("--origin-lat", type=float, default=DEFAULT_ORIGIN_LAT)
    cli.add_argument("--origin-lon", type=float, default=DEFAULT_ORIGIN_LON)
    cli.add_argument("--interval", type=float, default=0.5, help="sync interval in seconds")
    cli.add_argument("--verbose", action="store_true", help="log every AgOpenWeb command")
    return cli


def main() -> None:
    args = parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run(args)


if __name__ == "__main__":
    main()
