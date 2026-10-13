"""Video receiver: PyAV (bundled FFmpeg) decoding RTSP in a background thread.

* Independent of the telemetry workers - a stuck stream never blocks the UI or
  MAVLink.
* Measures the frame rate actually decoded; does NOT claim a latency figure
  (there is no shared clock with the Pi).
* Bounded reconnects with backoff; after ``max_reconnects`` failed attempts the
  worker stops in state "failed".
* The UI decides "stale" from ``last_frame_mono`` and never presents an old
  frame as live.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Frame:
    width: int
    height: int
    stride: int
    data: bytes          # packed RGB888 rows of ``stride`` bytes
    t_mono: float
    index: int
    simulated: bool = False


class FpsMeter:
    def __init__(self, window_s: float = 3.0):
        self.window_s = window_s
        self._t: deque[float] = deque()

    def add(self, t: float) -> None:
        self._t.append(t)
        while self._t and t - self._t[0] > self.window_s:
            self._t.popleft()

    def fps(self, now: float | None = None) -> float | None:
        if len(self._t) < 2:
            return None
        if now is not None and now - self._t[-1] > self.window_s:
            return 0.0
        span = self._t[-1] - self._t[0]
        return (len(self._t) - 1) / span if span > 0 else None


StatusCb = Callable[[dict], None]
FrameCb = Callable[[], None]


class _BaseWorker:
    backend = "base"

    def __init__(self, on_status: StatusCb | None = None, on_frame: FrameCb | None = None,
                 stale_after_s: float = 2.0):
        self.on_status = on_status
        self.on_frame = on_frame
        self.stale_after_s = stale_after_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._latest: Frame | None = None
        self._fps = FpsMeter()
        self.frames = 0
        self.reconnects = 0
        self.state = "stopped"
        self.error: str | None = None
        self.width = self.height = 0
        self.codec: str | None = None
        self.url_redacted = ""
        self.last_frame_mono: float | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_guarded, name=f"video-{self.backend}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
        self._set_state("stopped")

    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def latest(self) -> Frame | None:
        with self._lock:
            return self._latest

    def status(self) -> dict:
        now = time.monotonic()
        return {
            "state": self.state,
            "backend": self.backend,
            "url_redacted": self.url_redacted,
            "fps": None if self._fps.fps(now) is None else round(self._fps.fps(now), 2),
            "frames": self.frames,
            "width": self.width,
            "height": self.height,
            "codec": self.codec,
            "reconnects": self.reconnects,
            "error": self.error,
            "last_frame_mono": self.last_frame_mono,
            "stale_after_s": self.stale_after_s,
        }

    def _set_state(self, state: str, error: str | None = None) -> None:
        self.state = state
        if error is not None:
            self.error = error
        if self.on_status:
            try:
                self.on_status(self.status())
            except Exception:  # noqa: BLE001
                pass

    def _publish_frame(self, w: int, h: int, stride: int, data: bytes, simulated: bool = False) -> None:
        now = time.monotonic()
        self.frames += 1
        self.width, self.height = w, h
        self._fps.add(now)
        self.last_frame_mono = now
        with self._lock:
            self._latest = Frame(w, h, stride, data, now, self.frames, simulated)
        if self.on_frame:
            try:
                self.on_frame()
            except Exception:  # noqa: BLE001
                pass

    def _run_guarded(self) -> None:
        try:
            self._run()
        except Exception as e:  # noqa: BLE001
            log.exception("video worker crashed")
            self._set_state("failed", f"{type(e).__name__}: {e}")

    def _run(self) -> None:
        raise NotImplementedError


def redact_url(url: str) -> str:
    """Remove credentials from rtsp://user:pass@host/... URLs."""
    if "://" in url and "@" in url.split("://", 1)[1].split("/", 1)[0]:
        scheme, rest = url.split("://", 1)
        host = rest.split("@", 1)[1]
        return f"{scheme}://***@{host}"
    return url


class PyAvWorker(_BaseWorker):
    backend = "PyAV/FFmpeg"

    def __init__(self, url_provider: Callable[[], str], *, rtsp_transport: str = "tcp",
                 open_timeout_s: float = 8.0, read_timeout_s: float = 3.0, max_reconnects: int = 5,
                 on_status: StatusCb | None = None, on_frame: FrameCb | None = None,
                 on_link: Callable[[bool, str], None] | None = None, stale_after_s: float = 2.0,
                 realtime_files: bool = False):
        super().__init__(on_status, on_frame, stale_after_s)
        self.url_provider = url_provider
        self.rtsp_transport = rtsp_transport
        self.open_timeout_s = open_timeout_s
        self.read_timeout_s = read_timeout_s
        self.max_reconnects = max_reconnects
        self.on_link = on_link
        self.realtime_files = realtime_files

    def _link(self, up: bool, reason: str = "") -> None:
        if self.on_link:
            try:
                self.on_link(up, reason)
            except Exception:  # noqa: BLE001
                pass

    def _run(self) -> None:
        import av

        attempts = 0
        while not self._stop.is_set():
            self._set_state("connecting" if attempts == 0 else "reconnecting")
            container = None
            got_frame = False
            try:
                url = self.url_provider()
                self.url_redacted = redact_url(url)
                options = {}
                if url.startswith("rtsp"):
                    options = {
                        "rtsp_transport": self.rtsp_transport,
                        "fflags": "nobuffer",
                        "flags": "low_delay",
                        "max_delay": "500000",
                        "probesize": "65536",
                        "analyzeduration": "1000000",
                    }
                container = av.open(url, options=options, timeout=(self.open_timeout_s, self.read_timeout_s))
                if not container.streams.video:
                    raise RuntimeError("no video stream in source")
                vs = container.streams.video[0]
                self.codec = vs.codec_context.name
                start_wall = time.monotonic()
                for packet in container.demux(vs):
                    if self._stop.is_set():
                        break
                    for frame in packet.decode():
                        if self._stop.is_set():
                            break
                        rgb = frame.reformat(format="rgb24")
                        plane = rgb.planes[0]
                        self._publish_frame(rgb.width, rgb.height, plane.line_size, bytes(plane))
                        if not got_frame:
                            got_frame = True
                            attempts = 0
                            self.error = None
                            self._set_state("playing")
                            self._link(True)
                        if self.realtime_files and frame.time is not None:
                            # pace local test files like a live source
                            delay = frame.time - (time.monotonic() - start_wall)
                            if delay > 0:
                                self._stop.wait(delay)
                if self._stop.is_set():
                    break
                raise EOFError("stream ended")
            except Exception as e:  # noqa: BLE001 - every failure leads to a bounded reconnect
                err = f"{type(e).__name__}: {e}"
                if self._stop.is_set():
                    break
                self.error = err
                self._link(False, err)
            finally:
                if container is not None:
                    try:
                        container.close()
                    except Exception:  # noqa: BLE001
                        pass
            attempts += 1
            if attempts > self.max_reconnects or self.reconnects >= self.max_reconnects * 10:
                self._set_state("failed", f"{self.error} (אחרי {self.max_reconnects} ניסיונות התחברות מחדש)")
                return
            self.reconnects += 1
            self._set_state("reconnecting", self.error)
            self._stop.wait(min(2.0 ** attempts, 8.0))


class SimVideoWorker(_BaseWorker):
    """Synthetic frames for simulation mode (clearly marked by the UI)."""

    backend = "SIM"

    def __init__(self, width: int = 640, height: int = 480, fps: float = 15.0, **kw):
        super().__init__(**kw)
        self.req_w, self.req_h, self.req_fps = width, height, fps
        self.url_redacted = "SIM"
        self.codec = "synthetic"

    def _run(self) -> None:
        w, h = self.req_w, self.req_h
        stride = w * 3
        base = bytearray()
        for x in range(w):
            base += bytes((40 + (x * 60) // w, 70, 110 + (x * 80) // w))
        dark = bytes(base)
        self._set_state("playing")
        period = 1.0 / max(1.0, self.req_fps)
        i = 0
        while not self._stop.is_set():
            bar = (i * 8) % w
            row = bytearray(dark)
            row[bar * 3:(bar + 12) * 3] = b"\xff\xb3\x00" * min(12, w - bar)
            rows = bytes(row)
            band_top = (i * 4) % h
            band = bytes((230, 230, 230)) * w
            frame = bytearray(rows * h)
            for y in range(band_top, min(h, band_top + 6)):
                frame[y * stride:(y + 1) * stride] = band
            self._publish_frame(w, h, stride, bytes(frame), simulated=True)
            i += 1
            self._stop.wait(period)
