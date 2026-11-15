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

from foreman.engine import EVENT_LOG_PATH, ROOT, Engine, find_scenario
from foreman.learning import Learning
from foreman.llm import get_client, load_env_file

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
HOST = "127.0.0.1"
PORT = 8000
TICK_SECONDS = 0.1

# The model key is read from .env into the environment once, here. It is never
# logged, written anywhere, or put in the state. With no key the AI agents are off.
load_env_file(ROOT / ".env")
engine = Engine(log_path=EVENT_LOG_PATH, client=get_client(),
                learning=Learning(ROOT / "runs" / "learned.json"),
                lessons_path=ROOT / "runs" / "lessons.jsonl")


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


@app.get("/api/why")
def get_why(job: str) -> dict:
    """Why this job went to its tractor, and the real reason for every other tractor."""
    try:
        return engine.why(job)
    except KeyError:
        return {"ok": False, "error": f"no job named {job}"}


@app.get("/api/learning")
def get_learning() -> dict:
    """Estimate error per day, learned work rates, AI lessons, and AI downtime estimate accuracy."""
    return engine.learning_report()


@app.get("/api/learning/chart")
def get_learning_chart() -> dict:
    """The five-day learning curve for the chart: seeded hidden factors and noise, nothing saved."""
    return engine.learning_chart()


@app.post("/api/learning/run")
def post_learning_run(body: dict[str, Any]) -> dict:
    """{days}: run that many days off screen with hidden speed factors on, and learn from them."""
    days = body.get("days", 5)
    if not isinstance(days, int) or not 1 <= days <= 30:
        return {"ok": False, "error": "days must be a whole number from 1 to 30"}
    return {"ok": True, "days": engine.run_learning_days(days)}


@app.post("/api/control")
def post_control(body: dict[str, Any]) -> dict:
    """{action: start|pause|reset, speed: 1|4|16|60}. Both keys are optional."""
    return engine.control(body.get("action"), body.get("speed"))


@app.post("/api/scenario")
def post_scenario(body: dict[str, Any]) -> dict:
    """{name}: load <name>.json from scenarios/ or scenarios/farms/ and reset the day."""
    name = str(body.get("name", ""))
    # Names only, so a request cannot reach files outside scenarios/.
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        return {"ok": False, "error": "name must be letters, digits, - or _"}
    path = find_scenario(name)
    if path is None:
        return {"ok": False, "error": f"no scenario named {name}"}
    engine.load(path)
    return {"ok": True, "scenario": name}


@app.post("/api/event")
def post_event(body: dict[str, Any]) -> dict:
    """An event for both worlds, now. Payloads are in CLAUDE.md section 8c."""
    return engine.event(body)


# Mounted last so the /api routes above win over the static files.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


def main() -> None:
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
