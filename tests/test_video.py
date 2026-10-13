"""Video worker: real decode with PyAV, FPS measurement, bounded reconnects, stale display."""

from __future__ import annotations

import time

import pytest

av = pytest.importorskip("av")

from x500fs.video.worker import FpsMeter, PyAvWorker, SimVideoWorker, redact_url  # noqa: E402


def _clip(path, frames=45, fps=15):
    out = av.open(str(path), "w")
    codec = "libx264" if "libx264" in av.codecs_available else "mpeg4"
    s = out.add_stream(codec, rate=fps)
    s.width, s.height, s.pix_fmt = 320, 240, "yuv420p"
    for i in range(frames):
        f = av.VideoFrame(320, 240, "rgb24")
        f.planes[0].update(bytes([(i * 5) % 256, 100, 150]) * (320 * 240))
        for p in s.encode(f):
            out.mux(p)
    for p in s.encode():
        out.mux(p)
    out.close()


def wait(pred, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


@pytest.mark.video
def test_decode_and_fps(tmp_path):
    path = tmp_path / "clip.mp4"
    _clip(path)
    states = []
    w = PyAvWorker(lambda: str(path), max_reconnects=0, realtime_files=True,
                   on_status=lambda st: states.append(st["state"]))
    w.start()
    assert wait(lambda: not w.running(), 20)
    assert w.frames == 45
    assert w.width == 320 and w.height == 240 and w.codec
    f = w.latest()
    assert f is not None and len(f.data) >= f.stride * f.height and f.stride >= 320 * 3
    assert "playing" in states
    assert states[-1] == "failed"  # EOF with 0 reconnects allowed -> bounded stop
    st = w.status()
    assert st["fps"] is None or abs(st["fps"] - 15) < 3 or st["fps"] == 0.0


@pytest.mark.video
def test_bounded_reconnects_on_unreachable_source(tmp_path):
    w = PyAvWorker(lambda: str(tmp_path / "missing.mp4"), max_reconnects=1)
    t0 = time.monotonic()
    w.start()
    assert wait(lambda: not w.running(), 20)
    assert w.state == "failed" and w.frames == 0
    assert w.reconnects == 1
    assert "ניסיונות" in (w.error or "")
    assert time.monotonic() - t0 < 15


def test_stop_is_prompt_and_state_stopped():
    w = SimVideoWorker(160, 120, 15)
    w.start()
    assert wait(lambda: w.frames > 3)
    t0 = time.monotonic()
    w.stop()
    assert time.monotonic() - t0 < 2
    assert w.state == "stopped"
    assert w.latest().simulated is True


def test_fps_meter():
    m = FpsMeter(window_s=3)
    for i in range(31):
        m.add(100 + i / 10)
    assert abs(m.fps() - 10) < 0.01
    assert m.fps(now=110) == 0.0  # long silence -> 0, never a stale number


def test_redact_url():
    assert redact_url("rtsp://user:pw@host:8554/x") == "rtsp://***@host:8554/x"
    assert redact_url("rtsp://host:8554/x") == "rtsp://host:8554/x"
