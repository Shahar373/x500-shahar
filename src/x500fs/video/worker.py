"""Video receiver: an FFmpeg subprocess (SRT caller, or RTSP) decoding in the background.

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
import re
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


_SECRET_PARAM = re.compile(r"(?i)\b(passphrase|streamid|pbkeylen|password|pass|token)=([^&\s]*)")


def redact_url(url: str) -> str:
    """Remove credentials from URLs: user:pass@ and SRT passphrase/streamid parameters."""
    if "://" in url and "@" in url.split("://", 1)[1].split("/", 1)[0]:
        scheme, rest = url.split("://", 1)
        host = rest.split("@", 1)[1]
        url = f"{scheme}://***@{host}"
    return _SECRET_PARAM.sub(lambda m: f"{m.group(1)}=***", url)


@dataclass(frozen=True)
class Source:
    """What FFmpeg should open. ``input_args`` go before ``-i``."""
    url: str
    transport: str                 # srt | rtsp | file
    input_args: tuple = ()
    label: str = ""

    @property
    def redacted(self) -> str:
        return redact_url(self.url)


def srt_source(host: str, port: int, latency_us: int, *, connect_timeout_ms: int = 5000,
               io_timeout_us: int = 5_000_000, extra: str = "") -> Source:
    """SRT caller URL for the Pi's existing listener (x500-video-run).

    ``latency`` is the receiver buffer in microseconds (SRT uses the larger of
    the two peers' values); it is a configured buffer, not a measured delay.
    """
    if not host:
        raise ValueError("no SRT host (Pi Tailscale IPv4 unknown and none configured)")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    q = (f"mode=caller&transtype=live&latency={int(latency_us)}&connect_timeout={int(connect_timeout_ms)}"
         f"&timeout={int(io_timeout_us)}&pkt_size=1316")
    extra = extra.strip().lstrip("&?")
    if extra:
        q += "&" + extra
    return Source(f"srt://{host}:{int(port)}?{q}", "srt")


def rtsp_source(url: str, rtsp_transport: str = "tcp", io_timeout_us: int = 5_000_000) -> Source:
    return Source(url, "rtsp", ("-rtsp_transport", rtsp_transport, "-timeout", str(int(io_timeout_us))))


def file_source(path: str, realtime: bool = True) -> Source:
    return Source(str(path), "file", ("-re",) if realtime else ())


_STREAM_RE = re.compile(r"Stream #\d+:\d+.*?: Video: (\w+).*?(\d{2,5})x(\d{2,5})")
_FPS_RE = re.compile(r"([\d.]+) fps")


class FfmpegWorker(_BaseWorker):
    """Decode a live stream with an FFmpeg subprocess into RGB frames.

    FFmpeg writes raw rgb24 frames of a fixed size (the requested profile size;
    FFmpeg scales if the source differs, and the source size is reported) to a
    pipe. Only the newest frame is kept - nothing queues up behind the display.
    A watchdog kills FFmpeg when no frame arrives within the open/read timeout,
    which leads to a bounded reconnect.
    """

    backend = "FFmpeg"

    def __init__(self, source_provider: Callable[[], Source], *, width: int, height: int,
                 ffmpeg_path: str = "", open_timeout_s: float = 12.0, read_timeout_s: float = 3.0,
                 max_reconnects: int = 5, on_status: StatusCb | None = None, on_frame: FrameCb | None = None,
                 on_link: Callable[[bool, str], None] | None = None, stale_after_s: float = 2.0,
                 reconnect_delay_max_s: float = 8.0):
        super().__init__(on_status, on_frame, stale_after_s)
        self.source_provider = source_provider
        self.out_w, self.out_h = int(width), int(height)
        self.ffmpeg_path = ffmpeg_path
        self.open_timeout_s = open_timeout_s
        self.read_timeout_s = read_timeout_s
        self.max_reconnects = max_reconnects
        self.on_link = on_link
        self.reconnect_delay_max_s = reconnect_delay_max_s
        self.transport: str | None = None
        self.source_size: str | None = None
        self.source_fps: float | None = None
        self.stderr_tail: deque[str] = deque(maxlen=30)
        self.attempt_started_mono: float | None = None
        self._proc = None

    def status(self) -> dict:
        st = super().status()
        st.update(transport=self.transport, source_size=self.source_size, source_fps=self.source_fps,
                  output_size=f"{self.out_w}x{self.out_h}", stderr_tail=list(self.stderr_tail)[-6:],
                  attempt_started_mono=self.attempt_started_mono)
        return st

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._kill()
        super().stop(timeout)

    def _link(self, up: bool, reason: str = "") -> None:
        if self.on_link:
            try:
                self.on_link(up, reason)
            except Exception:  # noqa: BLE001
                pass

    def _kill(self) -> None:
        p = self._proc
        if p is not None and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass

    def _read_stderr(self, pipe, raw_url: str, red_url: str) -> None:
        try:
            self._read_stderr_lines(pipe, raw_url, red_url)
        except (ValueError, OSError):
            pass  # pipe closed while the process was being torn down

    def _read_stderr_lines(self, pipe, raw_url: str, red_url: str) -> None:
        section = None
        for raw in iter(pipe.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if not line:
                continue
            if raw_url:
                line = line.replace(raw_url, red_url)
            line = redact_url(line)
            if line.startswith("Input #"):
                section = "in"
            elif line.startswith("Output #"):
                section = "out"
            if section == "in" and "Video:" in line:
                m = _STREAM_RE.search(line)
                if m:
                    self.codec = m.group(1)
                    self.source_size = f"{m.group(2)}x{m.group(3)}"
                f = _FPS_RE.search(line)
                if f:
                    try:
                        self.source_fps = float(f.group(1))
                    except ValueError:
                        pass
            self.stderr_tail.append(line[:300])

    def _watchdog(self, proc, attempt_stop: threading.Event) -> None:
        while not attempt_stop.wait(0.2):
            if self._stop.is_set():
                self._kill()
                return
            now = time.monotonic()
            if self.last_frame_mono is not None and self.last_frame_mono >= (self.attempt_started_mono or 0):
                if now - self.last_frame_mono > self.read_timeout_s:
                    self._timeout_reason = f"no frame for {self.read_timeout_s:.0f}s"
                    self._kill()
                    return
            elif now - (self.attempt_started_mono or now) > self.open_timeout_s:
                self._timeout_reason = f"no picture within {self.open_timeout_s:.0f}s"
                self._kill()
                return

    def _attempt(self) -> bool:
        """One connection attempt. Returns True if at least one frame was decoded."""
        import subprocess

        from .ffmpeg_tools import NO_WINDOW, find_ffmpeg

        src = self.source_provider()
        self.transport = src.transport
        self.url_redacted = src.redacted
        exe = find_ffmpeg(self.ffmpeg_path)
        w, h = self.out_w, self.out_h
        live = ["-fflags", "nobuffer", "-flags", "low_delay", "-probesize", "65536", "-analyzeduration", "1000000"]
        cmd = [exe, "-hide_banner", "-nostdin", "-nostats", "-loglevel", "info",
               *(live if src.transport != "file" else []),  # nobuffer breaks the MP4 demuxer
               *src.input_args, "-i", src.url,
               "-map", "0:v:0", "-an", "-sn", "-dn",
               "-vf", f"scale={w}:{h}:flags=fast_bilinear", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
        self._timeout_reason = None
        self.attempt_started_mono = time.monotonic()
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                bufsize=0, creationflags=NO_WINDOW)
        self._proc = proc
        attempt_stop = threading.Event()
        t_err = threading.Thread(target=self._read_stderr, args=(proc.stderr, src.url, src.redacted),
                                 name="ffmpeg-stderr", daemon=True)
        t_dog = threading.Thread(target=self._watchdog, args=(proc, attempt_stop), name="ffmpeg-watchdog",
                                 daemon=True)
        t_err.start()
        t_dog.start()
        got = False
        size = w * h * 3
        buf = bytearray(size)
        mv = memoryview(buf)
        try:
            while not self._stop.is_set():
                n = 0
                while n < size:
                    r = proc.stdout.readinto(mv[n:])
                    if not r:
                        break
                    n += r
                if n < size:
                    break
                self._publish_frame(w, h, w * 3, bytes(buf))
                if not got:
                    got = True
                    self.error = None
                    self._set_state("playing")
                    self._link(True)
        finally:
            attempt_stop.set()
            self._kill()
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
            t_err.join(2.0)  # the process has exited, so stderr reaches EOF; join before closing
            t_dog.join(1.0)
            for pipe in (proc.stdout, proc.stderr):
                try:
                    pipe.close()
                except Exception:  # noqa: BLE001
                    pass
            self._proc = None
        if self._stop.is_set():
            return got
        last = next((ln.strip() for ln in reversed(self.stderr_tail)
                     if any(k in ln.lower() for k in ("error", "fail", "refused", "timed out", "timeout"))), None)
        if last is None and not self._timeout_reason and self.stderr_tail:
            last = self.stderr_tail[-1].strip()
        reason = self._timeout_reason or f"FFmpeg exited (code {proc.returncode})"
        raise EOFError(f"{reason}: {last}" if last else reason)

    def _run(self) -> None:
        attempts = 0
        while not self._stop.is_set():
            self._set_state("connecting" if attempts == 0 else "reconnecting")
            try:
                if self._attempt():
                    attempts = 0  # it played; a later drop starts a fresh bounded series
                if self._stop.is_set():
                    break
            except Exception as e:  # noqa: BLE001 - every failure leads to a bounded reconnect
                if self._stop.is_set():
                    break
                self.error = f"{type(e).__name__}: {e}" if not isinstance(e, EOFError) else str(e)
                self._link(False, self.error)
            attempts += 1
            if attempts > self.max_reconnects or self.reconnects >= self.max_reconnects * 10:
                self._set_state("failed", f"{self.error} (אחרי {self.max_reconnects} ניסיונות התחברות מחדש)")
                return
            self.reconnects += 1
            self._set_state("reconnecting", self.error)
            self._stop.wait(min(2.0 ** attempts, self.reconnect_delay_max_s))


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
