"""Foreman API server and static host for the dashboard.

Run with:  python -m foreman.server   then open http://localhost:8000
The endpoints are listed in CLAUDE.md section 8c.

A second copy for testing:  python -m foreman.server --port 8010
A copy on another port keeps its own files in runs/port_<port>/ (event log, learned
rates, lessons, self-tune), so it cannot disturb the main copy on port 8000.
--runs FOLDER picks the folder. FOREMAN_PORT and FOREMAN_RUNS do the same.

Public demo:  FOREMAN_DEMO=1 (or --demo). Off by default. Each visitor gets their own day,
idle days are dropped, changes are rate-limited, the AI agents and self-tune are off, and
nothing from .env is read. foreman/demo.py holds the rules. --host / FOREMAN_HOST sets the
address to listen on (127.0.0.1 unless a host such as a container asks for 0.0.0.0).
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from foreman.demo import Visitors, client_address, visitor_id
from foreman.engine import ROOT, Engine, find_scenario
from foreman.learning import Learning
from foreman.llm import get_client, load_env_file
from foreman.selftune import MIN_DAYS, SelfTune

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
DEFAULT_PORT = 8000


def _option(flag: str, env: str) -> str | None:
    """A setting from the command line (also when started through uvicorn) or the environment."""
    args = sys.argv[1:]
    if flag in args and args.index(flag) + 1 < len(args):
        return args[args.index(flag) + 1]
    return os.environ.get(env) or None


DEMO = "--demo" in sys.argv[1:] or os.environ.get("FOREMAN_DEMO", "").strip() in ("1", "true", "yes")
HOST = _option("--host", "FOREMAN_HOST") or "127.0.0.1"
PORT = int(_option("--port", "FOREMAN_PORT") or DEFAULT_PORT)
# Every copy that is not the main one keeps its own files, so two copies never share a file.
RUNS = Path(_option("--runs", "FOREMAN_RUNS")
            or (ROOT / "runs" if PORT == DEFAULT_PORT else ROOT / "runs" / f"port_{PORT}"))
RUNS.mkdir(parents=True, exist_ok=True)
TICK_SECONDS = 0.1

# The model key is read from .env into the environment once, here. It is never
# logged, written anywhere, or put in the state. With no key the AI agents are off.
# The public demo never reads it: the AI agents are off there.
if not DEMO:
    load_env_file(ROOT / ".env")


def _agopenweb_config() -> tuple[dict[str, str] | None, tuple[float, float]]:
    """Read T1=ws://...;T2=ws://... and lat,lon without hiding bad configuration."""
    raw = os.getenv("FOREMAN_AGOPENWEB_URLS", "").strip()
    urls = None
    if raw:
        urls = {}
        for item in raw.split(";"):
            tractor, separator, url = item.partition("=")
            if not separator or not tractor.strip() or not url.strip():
                raise RuntimeError("FOREMAN_AGOPENWEB_URLS must be T1=ws://host/ws;T2=...")
            urls[tractor.strip()] = url.strip()
    origin_raw = os.getenv("FOREMAN_AGOPENWEB_ORIGIN", "33.4484,-112.0740")
    try:
        latitude, longitude = (float(value.strip()) for value in origin_raw.split(",", 1))
    except ValueError as exc:
        raise RuntimeError("FOREMAN_AGOPENWEB_ORIGIN must be latitude,longitude") from exc
    return urls, (latitude, longitude)


if DEMO:
    # Every visitor gets a fresh day of their own: no model, no saved files, no self-tune.
    selftune = None
    engine = None
    visitors = Visitors(lambda: Engine(learning=Learning()))
else:
    AGOPENWEB_URLS, AGOPENWEB_ORIGIN = _agopenweb_config()
    selftune = SelfTune(RUNS / "selftune")
    engine = Engine(log_path=RUNS / "event_log.jsonl", client=get_client(),
                    learning=Learning(RUNS / "learned.json"),
                    lessons_path=RUNS / "lessons.jsonl",
                    agopenweb_urls=AGOPENWEB_URLS, agopenweb_origin=AGOPENWEB_ORIGIN,
                    selftune=selftune)
    visitors = None


def _clock_loop(stop: threading.Event) -> None:
    """Move simulated time forward in step with real time."""
    last = time.monotonic()
    while not stop.wait(TICK_SECONDS):
        current = time.monotonic()
        if visitors is not None:
            visitors.tick(current - last)
        else:
            engine.tick(current - last)
        last = current


def _engine(request: Request) -> Engine:
    """The day this request belongs to: the one day, or in the demo the visitor's own day."""
    return request.state.engine if visitors is not None else engine


@asynccontextmanager
async def lifespan(_: FastAPI):
    stop = threading.Event()
    threading.Thread(target=_clock_loop, args=(stop,), daemon=True).start()
    yield
    stop.set()


app = FastAPI(title="Foreman", lifespan=lifespan)


@app.get("/api/state")
def get_state(request: Request) -> dict:
    state = _engine(request).state()
    if DEMO:
        state["demo"] = True  # the page says the AI agents are off here
    return state


@app.get("/api/report")
def get_report(request: Request) -> dict:
    """Comparison against the baseline: jobs done, on time, travel, idle, violations."""
    return _engine(request).report()


@app.get("/api/log", response_class=PlainTextResponse)
def get_log(request: Request) -> str:
    """The event log as JSON Lines, one decision per line. This is the hand-in log."""
    return "".join(json.dumps(line) + "\n" for line in _engine(request).log_lines())


@app.get("/api/why")
def get_why(job: str, request: Request) -> dict:
    """Why this job went to its tractor, and the real reason for every other tractor."""
    try:
        return _engine(request).why(job)
    except KeyError:
        return {"ok": False, "error": f"no job named {job}"}


@app.get("/api/learning")
def get_learning(request: Request) -> dict:
    """Estimate error per day, learned work rates, AI lessons, and AI downtime estimate accuracy."""
    return _engine(request).learning_report()


@app.get("/api/learning/chart")
def get_learning_chart(request: Request) -> dict:
    """The five-day learning curve for the chart: seeded hidden factors and noise, nothing saved."""
    return _engine(request).learning_chart()


@app.get("/api/selftune")
def get_selftune() -> dict:
    """The planner's current settings, the latest self-tune, and the last 10 self-tune records."""
    if selftune is None:
        return {"settings": None, "latest": None, "min_days": MIN_DAYS, "history": [],
                "off": "Self-tune is off in the public demo."}
    return {"settings": selftune.settings(), "latest": selftune.latest(), "min_days": MIN_DAYS,
            "history": selftune.history(10)}


@app.post("/api/learning/run")
def post_learning_run(body: dict[str, Any], request: Request) -> dict:
    """{days}: run that many days off screen with hidden speed factors on, and learn from them."""
    if DEMO:
        return {"ok": False, "error": "Off-screen learning runs are off in the public demo."}
    days = body.get("days", 5)
    if not isinstance(days, int) or not 1 <= days <= 30:
        return {"ok": False, "error": "days must be a whole number from 1 to 30"}
    return {"ok": True, "days": _engine(request).run_learning_days(days)}


@app.post("/api/control")
def post_control(body: dict[str, Any], request: Request) -> dict:
    """{action: start|pause|reset, speed: 1|4|16|60}. Both keys are optional."""
    reply = _engine(request).control(body.get("action"), body.get("speed"))
    _note_request(request, "control", body, reply)
    return reply


@app.post("/api/scenario")
def post_scenario(body: dict[str, Any], request: Request) -> dict:
    """{name}: load <name>.json from scenarios/ or scenarios/farms/ and reset the day."""
    name = str(body.get("name", ""))
    # Names only, so a request cannot reach files outside scenarios/.
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        return {"ok": False, "error": "name must be letters, digits, - or _"}
    path = find_scenario(name)
    if path is None:
        return {"ok": False, "error": f"no scenario named {name}"}
    _engine(request).load(path)
    _note_request(request, "scenario", body, {"ok": True})
    return {"ok": True, "scenario": name}


@app.post("/api/scenario/random")
def post_random_scenario(body: dict[str, Any], request: Request) -> dict:
    """{seed?}: build a random farm with the farm maker, check it, load it and reset the day."""
    seed = body.get("seed")
    if seed is not None and not (isinstance(seed, int) and 0 < seed < 10_000_000):
        return {"ok": False, "error": "seed must be a whole number above 0"}
    # The farm maker's size is fixed here (3 tractors, 8 fields, 10 jobs): a request cannot change it.
    reply = _engine(request).load_random(seed)
    _note_request(request, "random farm", {"seed": reply.get("seed", seed)}, reply)
    return reply


@app.post("/api/event")
def post_event(body: dict[str, Any], request: Request) -> dict:
    """An event for both worlds, now. Payloads are in CLAUDE.md section 8c."""
    reply = _engine(request).event(body)
    _note_request(request, "event", body, reply)
    return reply


def _note_request(request: Request, kind: str, body: dict, reply: dict) -> None:
    """Keep a record of who changed the day: one line per event, control or farm load.

    The event log starts again with every day, so this file is what answers "who sent that".
    The public demo keeps no such record: it would only collect visitors' addresses.
    """
    if DEMO:
        return
    line = {
        "wall": datetime.now().isoformat(timespec="seconds"), "kind": kind,
        "client": f"{request.client.host}:{request.client.port}" if request.client else None,
        "sender": request.headers.get("x-foreman-client"),
        "agent": (request.headers.get("user-agent") or "")[:80],
        "page": request.headers.get("referer"),
        "body": {key: body.get(key) for key in ("type", "action", "speed", "tractor", "a", "b",
                                                 "job", "priority", "name", "seed") if key in body},
        "ok": reply.get("ok"), "clock": _engine(request).state().get("clock"),
    }
    try:
        with open(RUNS / "requests.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")
    except OSError:
        pass  # a full disk must not stop the day


@app.middleware("http")
async def demo_visitor(request: Request, call_next):
    """Public demo only: find the visitor's own day, and refuse requests that come too fast."""
    if visitors is None or not request.url.path.startswith("/api/"):
        return await call_next(request)
    address = client_address(request.headers, request.client.host if request.client else None)
    if int(request.headers.get("content-length") or 0) > 20_000:
        return JSONResponse({"ok": False, "error": "Request too large."}, status_code=413)
    known, visitor = visitor_id(request.headers.get("x-foreman-visitor"),
                                request.cookies.get("foreman_visitor"))
    refusal = visitors.check(request.method, request.url.path, address, visitor)
    if refusal is not None:
        return JSONResponse({"ok": False, "error": refusal[1]}, status_code=refusal[0])
    day = visitors.engine(visitor, address)
    if day is None:
        return JSONResponse({"ok": False, "error": "The demo is full right now. Try again in a few "
                                                   "minutes."}, status_code=503)
    request.state.engine = day
    response = await call_next(request)
    if not known:
        response.set_cookie("foreman_visitor", visitor, max_age=86_400, httponly=True,
                            samesite="none", secure=True)
    return response


@app.middleware("http")
async def fresh_pages(request: Request, call_next):
    """The dashboard files are always checked for a newer copy, so a browser never shows an old page."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# Mounted last so the /api routes above win over the static files.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


def main() -> None:
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
