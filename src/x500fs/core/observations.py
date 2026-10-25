"""Thread-safe store of the latest raw observations.

Workers (MAVLink, Pi collector, video, simulator) ``put`` raw values with the
laptop's monotonic receive time. Check evaluation works on an immutable
:class:`Snapshot` so ages are computed against a single ``now``.

The store is created for exactly one data origin - ``live`` or ``simulation``
- and refuses observations from the other origin. This is what keeps
simulated data from ever being shown or reported as coming from the drone.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Iterator


class OriginMismatch(RuntimeError):
    """Raised when simulated data is offered to a live store or vice versa."""


@dataclass(frozen=True)
class Observation:
    key: str
    value: Any
    t_mono: float
    t_wall: float
    source: str
    simulated: bool

    def age(self, now_mono: float) -> float:
        return max(0.0, now_mono - self.t_mono)


class ObservationStore:
    def __init__(self, simulated: bool):
        self.simulated = simulated
        self._lock = threading.Lock()
        self._data: dict[str, Observation] = {}
        self._counts: dict[str, int] = {}

    def put(
        self,
        key: str,
        value: Any,
        source: str,
        *,
        simulated: bool,
        t_mono: float | None = None,
        t_wall: float | None = None,
    ) -> Observation:
        if simulated != self.simulated:
            raise OriginMismatch(
                f"refusing {'simulated' if simulated else 'live'} observation "
                f"{key!r} in a {'simulation' if self.simulated else 'live'} store"
            )
        obs = Observation(
            key=key,
            value=value,
            t_mono=time.monotonic() if t_mono is None else t_mono,
            t_wall=time.time() if t_wall is None else t_wall,
            source=source,
            simulated=simulated,
        )
        with self._lock:
            self._data[key] = obs
            self._counts[key] = self._counts.get(key, 0) + 1
        return obs

    def get(self, key: str) -> Observation | None:
        with self._lock:
            return self._data.get(key)

    def remove(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def remove_prefix(self, prefix: str) -> None:
        with self._lock:
            for k in [k for k in self._data if k.startswith(prefix)]:
                del self._data[k]

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._counts.clear()

    def snapshot(self, now_mono: float | None = None) -> "Snapshot":
        with self._lock:
            data = dict(self._data)
            counts = dict(self._counts)
        return Snapshot(
            data=data,
            counts=counts,
            now_mono=time.monotonic() if now_mono is None else now_mono,
            now_wall=time.time(),
            simulated=self.simulated,
        )


@dataclass(frozen=True)
class Snapshot:
    data: dict[str, Observation]
    counts: dict[str, int]
    now_mono: float
    now_wall: float
    simulated: bool

    def get(self, key: str) -> Observation | None:
        return self.data.get(key)

    def age(self, key: str) -> float | None:
        obs = self.data.get(key)
        return None if obs is None else obs.age(self.now_mono)

    def with_prefix(self, prefix: str) -> Iterator[Observation]:
        for k, v in self.data.items():
            if k.startswith(prefix):
                yield v
