"""Foreman API server and static host for the dashboard.

Run with:  python -m foreman.server   then open http://localhost:8000
The endpoints are listed in CLAUDE.md section 8c.
"""

from __future__ import annotations

import json
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from foreman.engine import SCENARIO_DIR, Engine

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
HOST = "127.0.0.1"
PORT = 8000
TICK_SECONDS = 0.1

engine = Engine()


def _clock_loop(stop: threading.Event) -> None:
    """Move simulated time forward in step with real time."""
    last = time.monotonic()
    while not stop.wait(TICK_SECONDS):
        current = time.monotonic()
        engine.tick(current - last)
        last = current


@asynccontextmanager
async def lifespan(_: FastAPI):
    stop = threading.Event()
    threading.Thread(target=_clock_loop, args=(stop,), daemon=True).start()
    yield
    stop.set()


app = FastAPI(title="Foreman", lifespan=lifespan)


@app.get("/api/state")
def get_state() -> dict:
    return engine.state()


@app.get("/api/report")
def get_report() -> dict:
    """Comparison against the baseline: jobs done, on time, travel, idle, violations."""
    return engine.report()


@app.get("/api/log", response_class=PlainTextResponse)
def get_log() -> str:
    """The event log as JSON Lines, one decision per line. This is the hand-in log."""
    return "".join(json.dumps(line) + "\n" for line in engine.log_lines())


@app.post("/api/control")
def post_control(body: dict[str, Any]) -> dict:
    """{action: start|pause|reset, speed: 1|4|16|60}. Both keys are optional."""
    return engine.control(body.get("action"), body.get("speed"))


@app.post("/api/scenario")
def post_scenario(body: dict[str, Any]) -> dict:
    """{name}: load scenarios/<name>.json and reset the day."""
    name = str(body.get("name", ""))
    # Names only, so a request cannot reach files outside scenarios/.
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        return {"ok": False, "error": "name must be letters, digits, - or _"}
    path = SCENARIO_DIR / f"{name}.json"
    if not path.exists():
        return {"ok": False, "error": f"no scenario named {name}"}
    engine.load(path)
    return {"ok": True, "scenario": name}


@app.post("/api/event")
def post_event(body: dict[str, Any]) -> dict:
    """Stub until Phase 2: {type: breakdown|repair|block_road|open_road|set_priority|add_job}."""
    return {"ok": True, "received": body, "applied": False}


# Mounted last so the /api routes above win over the static files.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


def main() -> None:
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
