"""Runs the AI agent calls without ever making the rest of Foreman wait.

A call is started in the background and shows up at once in the reasoning
list as "thinking". When the model answers (or fails, or runs out of time)
the finished call waits in an inbox until the day applies it. Tests and
experiments use sync=True so calls finish inline and results are repeatable.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable

from foreman.ai_base import AI_TIMEOUT_S, AiCall, ModelClient

GRACE_S = 2.0        # how long past the limit a call may run before it is abandoned
REASONING_KEPT = 10  # calls shown on the dashboard


class AiCrew:
    def __init__(self, client: ModelClient | None, sync: bool = False,
                 timeout_s: float = AI_TIMEOUT_S) -> None:
        self.client = client
        self.sync = sync
        self.timeout_s = timeout_s
        self.calls: list[AiCall] = []  # oldest first
        self._inbox: queue.Queue = queue.Queue()
        self._running: dict[int, tuple[AiCall, str, dict, float]] = {}  # id -> (call, kind, context, started)
        self._lock = threading.Lock()
        self._next_id = 0

    @property
    def enabled(self) -> bool:
        return self.client is not None

    @property
    def model(self) -> str | None:
        return self.client.name if self.client else None

    def thinking(self) -> bool:
        with self._lock:
            return bool(self._running)

    def start(self, kind: str, placeholder: AiCall, work: Callable[[], AiCall],
              context: dict) -> None:
        """Begin one call. placeholder is shown as "thinking" until work() returns."""
        self.calls.append(placeholder)
        with self._lock:
            call_id = self._next_id
            self._next_id += 1
            self._running[call_id] = (placeholder, kind, context, time.monotonic())
        if self.sync:
            self._run(call_id, work)
        else:
            threading.Thread(target=self._run, args=(call_id, work), daemon=True).start()

    def _run(self, call_id: int, work: Callable[[], AiCall]) -> None:
        try:
            result = work()
        except Exception as error:  # an agent module should never raise; if it does, fall back
            result = None
            failure = f"{type(error).__name__}: {str(error)[:150]}"
        with self._lock:
            entry = self._running.pop(call_id, None)
        if entry is None:
            return  # it was abandoned for running past the limit; drop the late answer
        placeholder, kind, context, started = entry
        if result is None:
            placeholder.status, placeholder.error = "fallback", failure
            placeholder.ms = round((time.monotonic() - started) * 1000.0, 1)
        else:
            # Keep the same object the dashboard is already showing.
            placeholder.__dict__.update(result.__dict__)
        self._inbox.put((kind, placeholder, context))

    def finished(self) -> list[tuple[str, AiCall, dict]]:
        """Calls that are ready to be applied, oldest first. Overdue calls come back as fallbacks."""
        self._abandon_overdue()
        ready = []
        while True:
            try:
                ready.append(self._inbox.get_nowait())
            except queue.Empty:
                return ready

    def _abandon_overdue(self) -> None:
        # The client enforces the limit itself; this is the backstop if it does not.
        limit = self.timeout_s + GRACE_S
        with self._lock:
            overdue = [call_id for call_id, (_, _, _, started) in self._running.items()
                       if time.monotonic() - started > limit]
            entries = [self._running.pop(call_id) for call_id in overdue]
        for placeholder, kind, context, _ in entries:
            placeholder.status = "fallback"
            placeholder.error = f"no answer within {self.timeout_s:.0f} s"
            placeholder.ms = round(limit * 1000.0, 1)
            self._inbox.put((kind, placeholder, context))

    def reasoning(self) -> list[dict]:
        """The latest calls, newest first, for state.foreman.reasoning."""
        return [call.to_dict() for call in reversed(self.calls[-REASONING_KEPT:])]
