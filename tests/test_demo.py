"""The public demo: days kept apart per visitor, idle days dropped, limits, and the
server itself started both ways (demo off as it runs locally, demo on as it runs public)."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from foreman import demo
from foreman.demo import Visitors, client_address, visitor_id

ROOT = Path(__file__).resolve().parent.parent


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeDay:
    def __init__(self) -> None:
        self.ticks = 0

    def tick(self, seconds: float) -> None:
        self.ticks += 1


def test_each_visitor_has_their_own_day_and_idle_days_are_dropped():
    clock = FakeClock()
    visitors = Visitors(FakeDay, clock=clock, idle_seconds=300)
    a, b = visitors.engine("visitor-a"), visitors.engine("visitor-b")
    assert a is not b and visitors.engine("visitor-a") is a
    clock.now += 200
    visitors.engine("visitor-a")  # a is still looking, b is not
    clock.now += 200
    visitors.tick(0.1)
    assert len(visitors) == 1 and visitors.engine("visitor-a") is a
    assert a.ticks == 1 and b.ticks == 0


def test_a_full_demo_makes_room_only_from_a_day_idle_for_a_minute():
    clock = FakeClock()
    visitors = Visitors(FakeDay, clock=clock, max_visitors=2)
    visitors.engine("visitor-1")
    visitors.engine("visitor-2")
    assert visitors.engine("visitor-3") is None
    clock.now += demo.EVICT_AFTER_SECONDS + 1
    visitors.engine("visitor-2")
    assert visitors.engine("visitor-3") is not None
    assert len(visitors) == 2 and visitors.engine("visitor-2") is not None


def test_a_broken_day_is_dropped_without_stopping_the_others():
    class Broken(FakeDay):
        def tick(self, seconds):
            raise RuntimeError("boom")

    days = iter([Broken(), FakeDay()])
    visitors = Visitors(lambda: next(days), clock=FakeClock())
    visitors.engine("visitor-broken")
    good = visitors.engine("visitor-good")
    visitors.tick(0.1)
    assert good.ticks == 1 and len(visitors) == 1


def test_changes_are_rate_limited_per_visitor():
    visitors = Visitors(FakeDay, clock=FakeClock())
    visitors.engine("visitor-a")
    visitors.engine("visitor-b")
    limit = demo.CHANGES_PER_VISITOR[0]
    replies = [visitors.check("POST", "/api/event", "1.2.3.4", "visitor-a") for _ in range(limit + 1)]
    assert replies[:limit] == [None] * limit and replies[-1][0] == 429
    assert visitors.check("POST", "/api/event", "1.2.3.4", "visitor-b") is None  # another visitor


def test_new_days_are_rate_limited_per_address():
    visitors = Visitors(FakeDay, clock=FakeClock())
    limit = demo.NEW_DAYS_PER_ADDRESS[0]
    for n in range(limit):
        assert visitors.check("GET", "/api/state", "5.6.7.8", f"visitor-{n:04}") is None
    assert visitors.check("GET", "/api/state", "5.6.7.8", "visitor-new1")[0] == 429


def test_visitor_ids_and_addresses():
    assert visitor_id("abcdefgh1234", None) == (True, "abcdefgh1234")
    assert visitor_id("bad id!", "cookie-id-123") == (True, "cookie-id-123")
    known, fresh = visitor_id(None, "x")
    assert not known and len(fresh) >= 16
    assert client_address({"x-forwarded-for": "9.9.9.9, 10.0.0.1"}, "127.0.0.1") == "10.0.0.1"
    assert client_address({}, "127.0.0.1") == "127.0.0.1"
    assert client_address({"cf-connecting-ip": "8.8.8.8", "x-forwarded-for": "1.1.1.1"}, None) == "8.8.8.8"


# ---- the real server, both ways -------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def server(tmp_path, request):
    demo_on = request.param
    port = _free_port()
    env = {**os.environ, "FOREMAN_DEMO": "1" if demo_on else "0", "FOREMAN_AI": "0"}
    process = subprocess.Popen(
        [sys.executable, "-m", "foreman.server", "--port", str(port), "--runs", str(tmp_path)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/api/state", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    yield base
    process.terminate()
    process.wait(timeout=10)


def _call(base, path, body=None, visitor=None):
    headers = {"Content-Type": "application/json"}
    if visitor:
        headers["X-Foreman-Visitor"] = visitor
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.mark.parametrize("server", [False], indirect=True)
def test_local_server_has_one_shared_day(server):
    _call(server, "/api/control", {"action": "start", "speed": 60}, visitor="visitor-aaaa")
    status, state = _call(server, "/api/state", visitor="visitor-bbbb")
    assert status == 200 and state["running"] and "demo" not in state
    assert _call(server, "/api/learning/run", {"days": 1})[1]["ok"]
    _call(server, "/api/control", {"action": "pause"})


@pytest.mark.parametrize("server", [True], indirect=True)
def test_demo_server_keeps_visitors_apart(server):
    _call(server, "/api/control", {"action": "start", "speed": 60}, visitor="visitor-aaaa")
    _, mine = _call(server, "/api/state", visitor="visitor-aaaa")
    _, theirs = _call(server, "/api/state", visitor="visitor-bbbb")
    assert mine["running"] and not theirs["running"]
    assert mine["demo"] is True and mine["foreman"]["ai"]["enabled"] is False

    status, reply = _call(server, "/api/scenario/random", {}, visitor="visitor-bbbb")
    assert status == 200 and reply["ok"]
    _, theirs = _call(server, "/api/state", visitor="visitor-bbbb")
    _, mine = _call(server, "/api/state", visitor="visitor-aaaa")
    assert theirs["scenario"].startswith("random_") and mine["scenario"] == "sample_farm"

    assert not _call(server, "/api/learning/run", {"days": 1}, visitor="visitor-aaaa")[1]["ok"]
    assert _call(server, "/api/selftune", visitor="visitor-aaaa")[1]["settings"] is None

    codes = [_call(server, "/api/control", {"action": "pause"}, visitor="visitor-cccc")[0]
             for _ in range(demo.CHANGES_PER_VISITOR[0] + 1)]
    assert codes[-1] == 429 and set(codes[:-1]) == {200}
