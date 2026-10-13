"""Per-preflight-session event log: interruptions, warnings, RTT samples."""

from __future__ import annotations

import statistics
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Interruption:
    link: str          # "ssh" | "mavlink" | "video" | "collector"
    reason: str
    start_wall: float
    start_mono: float
    end_wall: float | None = None
    end_mono: float | None = None

    @property
    def open(self) -> bool:
        return self.end_mono is None

    def duration(self, now_mono: float | None = None) -> float:
        end = self.end_mono if self.end_mono is not None else (now_mono or time.monotonic())
        return max(0.0, end - self.start_mono)

    def to_dict(self) -> dict[str, Any]:
        return {
            "link": self.link,
            "reason": self.reason,
            "start": _iso(self.start_wall),
            "end": None if self.end_wall is None else _iso(self.end_wall),
            "duration_s": round(self.duration(), 2),
            "ongoing": self.open,
        }


@dataclass
class Event:
    t_wall: float
    level: str        # "info" | "warning" | "error"
    source: str
    text: str
    seq: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"time": _iso(self.t_wall), "level": self.level, "source": self.source, "text": self.text}


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t)) + f".{int((t % 1) * 1000):03d}"


@dataclass
class RttStats:
    samples_ms: deque = field(default_factory=lambda: deque(maxlen=120))
    timeouts: int = 0

    def add(self, ms: float) -> None:
        self.samples_ms.append(ms)

    def summary(self) -> dict[str, Any]:
        s = list(self.samples_ms)
        if not s:
            return {"count": 0, "timeouts": self.timeouts}
        return {
            "count": len(s),
            "last_ms": round(s[-1], 1),
            "median_ms": round(statistics.median(s), 1),
            "max_ms": round(max(s), 1),
            "min_ms": round(min(s), 1),
            "timeouts": self.timeouts,
        }


class SessionLog:
    """Thread-safe log for one monitoring/preflight session."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.session_id = uuid.uuid4().hex[:12]
        self.started_wall = time.time()
        self.started_mono = time.monotonic()
        self.preflight_started_wall: float | None = None
        self._interruptions: list[Interruption] = []
        self._events: deque[Event] = deque(maxlen=500)
        self._seq = 0
        self.rtt = RttStats()

    def new_preflight(self) -> None:
        with self._lock:
            self.session_id = uuid.uuid4().hex[:12]
            self.preflight_started_wall = time.time()

    # -- interruptions -------------------------------------------------------
    def link_down(self, link: str, reason: str) -> None:
        with self._lock:
            for i in self._interruptions:
                if i.link == link and i.open:
                    return
            self._interruptions.append(
                Interruption(link, reason, time.time(), time.monotonic())
            )
        self.event("warning", link, f"קישור {link} נותק: {reason}")

    def link_up(self, link: str) -> None:
        closed = False
        with self._lock:
            for i in self._interruptions:
                if i.link == link and i.open:
                    i.end_wall = time.time()
                    i.end_mono = time.monotonic()
                    closed = True
        if closed:
            self.event("info", link, f"קישור {link} חזר")

    def interruptions(self) -> list[Interruption]:
        with self._lock:
            return list(self._interruptions)

    # -- events ---------------------------------------------------------------
    def event(self, level: str, source: str, text: str) -> Event:
        with self._lock:
            self._seq += 1
            ev = Event(time.time(), level, source, text, self._seq)
            self._events.append(ev)
        return ev

    def events_after(self, seq: int) -> list[Event]:
        with self._lock:
            return [e for e in self._events if e.seq > seq]

    def events(self) -> list[Event]:
        with self._lock:
            return list(self._events)
