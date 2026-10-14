"""A local stand-in for the Pi's SRT video pipeline (tests, self-test, development).

Runs FFmpeg's ``testsrc2`` through the same encoder settings as
``/opt/x500/bin/x500-video-run`` (H.264 baseline, zerolatency, MPEG-TS) into an
SRT *listener*, so the app's caller code path is exercised end to end::

    python -m x500fs.video.testsource --port 5600 --profile degraded
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import time

from ..settings import SRT_PROFILES
from .ffmpeg_tools import NO_WINDOW, find_ffmpeg


def free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class SrtTestSource:
    def __init__(self, port: int | None = None, profile: str = "degraded", host: str = "127.0.0.1",
                 ffmpeg_path: str = "", seconds: float | None = None):
        self.host = host
        self.port = port or free_udp_port()
        self.profile = dict(SRT_PROFILES[profile])
        self.ffmpeg = find_ffmpeg(ffmpeg_path)
        self.seconds = seconds
        self.proc: subprocess.Popen | None = None

    def command(self) -> list[str]:
        p = self.profile
        w, h, fps, kbps, lat = p["width"], p["height"], p["fps"], p["bitrate_kbps"], p["latency_us"]
        src = f"testsrc2=size={w}x{h}:rate={fps}"
        if self.seconds:
            src += f":duration={self.seconds}"
        return [self.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-re", "-f", "lavfi", "-i", src,
                "-an", "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-profile:v", "baseline",
                "-bf", "0", "-g", str(fps), "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps // 3}k",
                "-x264-params", "repeat-headers=1", "-pix_fmt", "yuv420p", "-f", "mpegts",
                f"srt://{self.host}:{self.port}?mode=listener&transtype=live&latency={lat}&pkt_size=1316"]

    def start(self) -> "SrtTestSource":
        self.proc = subprocess.Popen(self.command(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        time.sleep(0.5)  # let the listener bind
        if self.proc.poll() is not None:
            err = self.proc.stderr.read().decode("utf-8", "replace") if self.proc.stderr else ""
            raise RuntimeError(f"SRT test source failed to start: {err.strip()[:300]}")
        return self

    def running(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                pass
        if self.proc and self.proc.stderr:
            self.proc.stderr.close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Local SRT listener that mimics x500-video-run")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5600)
    ap.add_argument("--profile", default="degraded", choices=sorted(SRT_PROFILES))
    args = ap.parse_args(argv)
    print(f"SRT test source on {args.host}:{args.port} ({args.profile}); restarts after each caller. Ctrl+C to stop")
    try:
        while True:  # like systemd Restart=always: the listener exits when the caller leaves
            src = SrtTestSource(args.port, args.profile, args.host).start()
            while src.running():
                time.sleep(0.5)
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
