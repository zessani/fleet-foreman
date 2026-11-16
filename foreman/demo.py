"""The public demo: one simulated day per visitor, kept apart, cleaned up when idle.

Used only when the server runs with FOREMAN_DEMO=1. Nothing here runs locally.

- A visitor is known by a random id the page keeps in the browser (sent as the
  X-Foreman-Visitor header, with a cookie as a fallback). A new id gets a new day.
- A day nobody has looked at for IDLE_SECONDS is dropped.
- At most MAX_VISITORS days run at once. When full, the longest idle day is dropped
  if it has been idle for a minute; otherwise the newcomer is asked to come back later.
- Changes (control, events, farm loads) and the heavy report are rate-limited per
  address and per visitor. So is starting new days, so a script cannot fill the demo.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Callable

IDLE_SECONDS = 5 * 60
MAX_VISITORS = 40
EVICT_AFTER_SECONDS = 60          # when full, a day idle this long may make room
# One address can be a whole room on the same network, so the per-address limits are wide
# and the per-visitor limits are the tight ones.
NEW_DAYS_PER_ADDRESS = (30, 600)  # new days per address in 10 minutes
CHANGES_PER_ADDRESS = (200, 60)   # changes per address per minute
CHANGES_PER_VISITOR = (30, 60)    # changes per visitor per minute
READS_PER_VISITOR = (1200, 60)    # the dashboard and 3D view poll about 9 times a second together
READS_PER_ADDRESS = (12000, 60)
LIMITED_PATHS = ("/api/control", "/api/event", "/api/scenario", "/api/scenario/random",
                 "/api/report", "/api/learning/run")
ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{8,64}")
TOO_FAST = "Too many requests. Wait a few seconds and try again."
TOO_MANY_NEW = "Too many new demo days from your network. Try again in a few minutes."


def visitor_id(header: str | None, cookie: str | None) -> tuple[bool, str]:
    """(known, id): the id the browser sent, or a new one."""
    for candidate in (header, cookie):
        if candidate and ID_PATTERN.fullmatch(candidate):
            return True, candidate
    return False, secrets.token_urlsafe(16)


def client_address(headers, peer: str | None) -> str:
    """The visitor's address. Behind the host's proxy (Cloudflare in front of Render) it is the
    header the proxy sets and overwrites; otherwise the last address a proxy added."""
    for name in ("cf-connecting-ip", "true-client-ip"):
        if headers.get(name):
            return headers[name].strip()
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[-1].strip() or (peer or "?")
    return peer or "?"


class _Window:
    """Counts events per key over a sliding window."""

    def __init__(self, limit: int, seconds: float) -> None:
        self.limit, self.seconds = limit, seconds
        self._times: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str, now: float) -> bool:
        times = self._times[key]
        while times and now - times[0] > self.seconds:
            times.popleft()
        if len(times) >= self.limit:
            return False
        times.append(now)
        return True

    def forget_old(self, now: float) -> None:
        for key in [k for k, times in self._times.items() if not times or now - times[-1] > self.seconds]:
            del self._times[key]


class Visitors:
    def __init__(self, make_engine: Callable[[], object], clock: Callable[[], float] = time.monotonic,
                 idle_seconds: float = IDLE_SECONDS, max_visitors: int = MAX_VISITORS) -> None:
        self._make = make_engine
        self._clock = clock
        self.idle_seconds = idle_seconds
        self.max_visitors = max_visitors
        self._lock = threading.Lock()
        self._days: dict[str, list] = {}  # id -> [engine, last seen]
        self._new_days = _Window(*NEW_DAYS_PER_ADDRESS)
        self._changes_address = _Window(*CHANGES_PER_ADDRESS)
        self._changes_visitor = _Window(*CHANGES_PER_VISITOR)
        self._reads_visitor = _Window(*READS_PER_VISITOR)
        self._reads_address = _Window(*READS_PER_ADDRESS)

    def __len__(self) -> int:
        return len(self._days)

    def check(self, method: str, path: str, address: str, visitor: str) -> tuple[int, str] | None:
        """(status, message) when this request must be refused, otherwise None."""
        now = self._clock()
        with self._lock:
            if not (self._reads_visitor.allow(visitor, now) and self._reads_address.allow(address, now)):
                return 429, TOO_FAST
            if path in LIMITED_PATHS:
                if not (self._changes_address.allow(address, now)
                        and self._changes_visitor.allow(visitor, now)):
                    return 429, TOO_FAST
            if visitor not in self._days and not self._new_days.allow(address, now):
                return 429, TOO_MANY_NEW
        return None

    def engine(self, visitor: str, address: str = "?"):
        """The visitor's day, started if new. None when the demo is full."""
        now = self._clock()
        with self._lock:
            entry = self._days.get(visitor)
            if entry is None:
                self._drop_idle(now)
                if len(self._days) >= self.max_visitors and not self._make_room(now):
                    return None
                entry = self._days[visitor] = [self._make(), now]
            entry[1] = now
            return entry[0]

    def tick(self, real_seconds: float) -> None:
        """Move every visitor's day forward. One day failing never stops the others."""
        now = self._clock()
        with self._lock:
            self._drop_idle(now)
            days = [(visitor, entry[0]) for visitor, entry in self._days.items()]
            for window in (self._new_days, self._changes_address, self._changes_visitor,
                           self._reads_visitor, self._reads_address):
                window.forget_old(now)
        for visitor, day in days:
            try:
                day.tick(real_seconds)
            except Exception:  # a broken day is dropped; the visitor gets a fresh one next request
                with self._lock:
                    self._days.pop(visitor, None)

    def _drop_idle(self, now: float) -> None:
        for visitor in [v for v, (_, seen) in self._days.items() if now - seen > self.idle_seconds]:
            del self._days[visitor]

    def _make_room(self, now: float) -> bool:
        oldest = min(self._days, key=lambda v: self._days[v][1], default=None)
        if oldest is None or now - self._days[oldest][1] < EVICT_AFTER_SECONDS:
            return False
        del self._days[oldest]
        return True
