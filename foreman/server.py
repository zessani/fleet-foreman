"""Foreman API server and static host for the dashboard.

Run with:  python -m foreman.server   then open http://localhost:8000

Phase 0: /api/state returns fake data in the final shape (CLAUDE.md section
8c) and the two POST endpoints accept anything, so the dashboard can be built
before the engine exists.
"""

from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from foreman.fake_state import fake_state

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
HOST = "127.0.0.1"
PORT = 8000

app = FastAPI(title="Foreman")


@app.get("/api/state")
def get_state() -> dict:
    return fake_state()


@app.post("/api/control")
def post_control(body: dict[str, Any]) -> dict:
    """Stub. Will take {action: start|pause|reset, speed}."""
    return {"ok": True, "received": body}


@app.post("/api/event")
def post_event(body: dict[str, Any]) -> dict:
    """Stub. Will take {type: breakdown|repair|block_road|open_road|set_priority|add_job, ...}."""
    return {"ok": True, "received": body}


# Mounted last so the /api routes above win over the static files.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


def main() -> None:
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
