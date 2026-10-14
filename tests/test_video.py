"""Video: FFmpeg decoding (file + SRT caller), bounded reconnects, stale display, Pi service allowlist."""

from __future__ import annotations

import re
import subprocess
import time

import pytest
from videotools import file_ffmpeg, network_ffmpeg

from x500fs.core.observations import ObservationStore
from x500fs.transport.pi_video import (
    NotAllowed, PiVideoControl, control_command, password_help, unit_for,
)
from x500fs.transport.ssh import ExecResult
from x500fs.video.ffmpeg_tools import probe
from x500fs.video.pipeline import BAD, OFF, OK, UNKNOWN, WARN, pipeline_summary
from x500fs.video.testsource import SrtTestSource, free_udp_port
from x500fs.video.worker import (
    FfmpegWorker, FpsMeter, SimVideoWorker, file_source, redact_url, rtsp_source, srt_source,
)


def wait(pred, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def _clip(path, ffmpeg, frames=45, fps=15):
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size=320x240:rate={fps}", "-frames:v", str(frames), "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(path)], check=True, timeout=60)


# ---------------------------------------------------------------- local file (bundled FFmpeg)


@pytest.mark.video
def test_decode_file_and_fps(tmp_path):
    ff = file_ffmpeg()
    path = tmp_path / "clip.mp4"
    _clip(path, ff)
    states = []
    w = FfmpegWorker(lambda: file_source(path), width=320, height=240, ffmpeg_path=ff, max_reconnects=0,
                     on_status=lambda st: states.append(st["state"]))
    w.start()
    assert wait(lambda: not w.running(), 20)
    assert w.frames == 45
    assert w.width == 320 and w.height == 240 and w.codec == "h264"
    assert w.source_size == "320x240"
    f = w.latest()
    assert f is not None and len(f.data) == 320 * 240 * 3 and f.stride == 320 * 3
    assert "playing" in states
    assert states[-1] == "failed"  # EOF with 0 reconnects allowed -> bounded stop


@pytest.mark.video
def test_bounded_reconnects_on_missing_source(tmp_path):
    ff = file_ffmpeg()
    w = FfmpegWorker(lambda: file_source(tmp_path / "missing.mp4"), width=160, height=120, ffmpeg_path=ff,
                     max_reconnects=1)
    t0 = time.monotonic()
    w.start()
    assert wait(lambda: not w.running(), 20)
    assert w.state == "failed" and w.frames == 0
    assert w.reconnects == 1
    assert "ניסיונות" in (w.error or "")
    assert time.monotonic() - t0 < 15


def test_bundled_ffmpeg_reports_srt():
    info = probe(file_ffmpeg())
    assert info["version"]
    assert info["srt"], "bundled FFmpeg must include the SRT protocol"


# ---------------------------------------------------------------- SRT (caller to a local listener)


@pytest.mark.video
def test_srt_end_to_end_like_the_pi():
    ff = network_ffmpeg()
    with SrtTestSource(profile="degraded", ffmpeg_path=ff) as src:
        w = FfmpegWorker(lambda: srt_source("127.0.0.1", src.port, 300000), width=480, height=360,
                         ffmpeg_path=ff, max_reconnects=1)
        w.start()
        try:
            assert wait(lambda: w.frames >= 20, 20), w.status()
            st = w.status()
            assert st["state"] == "playing" and st["transport"] == "srt"
            assert st["codec"] == "h264" and st["source_size"] == "480x360"
            assert "mode=caller" in st["url_redacted"]
            assert wait(lambda: (w.status()["fps"] or 0) > 10, 5)
        finally:
            w.stop()
    assert w.state == "stopped"


@pytest.mark.video
def test_srt_unreachable_is_bounded():
    ff = network_ffmpeg()
    port = free_udp_port()
    w = FfmpegWorker(lambda: srt_source("127.0.0.1", port, 120000, connect_timeout_ms=1000), width=160,
                     height=120, ffmpeg_path=ff, max_reconnects=1, open_timeout_s=5, reconnect_delay_max_s=1)
    t0 = time.monotonic()
    w.start()
    assert wait(lambda: not w.running(), 25)
    assert w.state == "failed" and w.frames == 0 and w.reconnects == 1
    assert time.monotonic() - t0 < 20


@pytest.mark.video
def test_srt_source_restart_reconnects_and_old_frame_goes_stale():
    ff = network_ffmpeg()
    port = free_udp_port()
    src = SrtTestSource(port, "degraded", ffmpeg_path=ff).start()
    w = FfmpegWorker(lambda: srt_source("127.0.0.1", port, 300000, connect_timeout_ms=1500), width=480,
                     height=360, ffmpeg_path=ff, max_reconnects=4, read_timeout_s=2, reconnect_delay_max_s=2)
    w.start()
    try:
        assert wait(lambda: w.frames >= 5, 20)
        src.stop()  # like systemd restarting x500-video@ after the caller drops
        assert wait(lambda: w.state == "reconnecting", 10)
        st = w.status()
        assert time.monotonic() - st["last_frame_mono"] > st["stale_after_s"]
        frames_before = w.frames
        src = SrtTestSource(port, "degraded", ffmpeg_path=ff).start()
        assert wait(lambda: w.frames > frames_before + 5, 25), w.status()
        assert w.state == "playing" and w.reconnects >= 1
    finally:
        w.stop()
        src.stop()


# ---------------------------------------------------------------- URL building / redaction


def test_srt_url_matches_pi_listener_options():
    s = srt_source("100.64.1.2", 5600, 300000, extra="passphrase=topsecret1234")
    assert s.url.startswith("srt://100.64.1.2:5600?mode=caller&transtype=live&latency=300000")
    assert "pkt_size=1316" in s.url and "connect_timeout=5000" in s.url
    assert "topsecret" not in s.redacted and "passphrase=***" in s.redacted
    with pytest.raises(ValueError):
        srt_source("", 5600, 1000)
    assert srt_source("fd7a::1", 5600, 1).url.startswith("srt://[fd7a::1]:5600?")


def test_redact_url():
    assert redact_url("rtsp://user:pw@host:8554/x") == "rtsp://***@host:8554/x"
    assert redact_url("rtsp://host:8554/x") == "rtsp://host:8554/x"
    assert rtsp_source("rtsp://u:p@h/x").redacted == "rtsp://***@h/x"


# ---------------------------------------------------------------- simple workers


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


# ---------------------------------------------------------------- Pi service allowlist


class FakeSession:
    def __init__(self, answers):
        self.answers = answers
        self.cmds = []

    def exec(self, cmd, stdin=None, timeout=30.0):
        self.cmds.append(cmd)
        for rx, res in self.answers:
            if re.fullmatch(rx, cmd):
                return ExecResult(*res, 0.01)
        return ExecResult(1, b"", b"unexpected command", 0.01)


def test_only_allowlisted_units_and_actions():
    assert unit_for("degraded") == "x500-video@degraded.service"
    assert control_command("start", "high") == "sudo -n /usr/bin/systemctl start x500-video@high.service"
    for bad in ("evil", "high;reboot", "../x", "", "normal.service"):
        with pytest.raises(NotAllowed):
            unit_for(bad)
    for action in ("restart", "enable", "disable", "kill", "mask"):
        with pytest.raises(NotAllowed):
            control_command(action, "normal")
    with pytest.raises(NotAllowed):
        unit_for("normal", template="mavlink-router.service")
    with pytest.raises(NotAllowed):
        unit_for("normal", template="x500-video@{profile}.service extra")


def test_password_required_is_detected_and_explained():
    sess = FakeSession([(r"sudo -n .*", (1, b"", b"sudo: a password is required\n"))])
    res = PiVideoControl(sess).start("degraded")
    assert not res.ok and res.needs_password
    assert sess.cmds == ["sudo -n /usr/bin/systemctl start x500-video@degraded.service"]
    assert "x500-video degraded" in password_help("degraded")


def test_state_read_needs_no_sudo():
    sess = FakeSession([(r"/usr/bin/systemctl show x500-video@normal\.service .*",
                         (0, b"ActiveState=active\nSubState=running\nMainPID=42\nNRestarts=0\n", b""))])
    ctl = PiVideoControl(sess)
    st = ctl.state("normal")
    assert ctl.is_running(st) and st["MainPID"] == "42"
    assert not any("sudo" in c for c in sess.cmds)


# ---------------------------------------------------------------- four separate indicators


def _snap_with_video(video_data, age=0.0):
    store = ObservationStore(False)
    now = time.monotonic()
    doc = {"sections": {"video": {"ok": True, "data": video_data, "_received_mono": now - age}}}
    store.put("pi.collector", doc, "test", simulated=False, t_mono=now - age)
    return store.snapshot()


VIDEO_ACTIVE = {"active_profile": "degraded",
                "units": {"degraded": {"ActiveState": "active", "SubState": "running", "NRestarts": "0"}},
                "progress": {"exists": True, "mtime_age_s": 0.8, "parsed": {"frame": "1200"},
                             "frame_rate_between_polls": 19.8}}


def test_encoder_progress_is_not_a_live_picture():
    out = pipeline_summary(_snap_with_video(VIDEO_ACTIVE), None)
    assert out["service"][1] == OK and out["encoder"][1] == OK
    assert out["transport"][1] == OFF and out["decoded"][1] == OFF


def test_stale_frame_and_stale_pi_data():
    snap = _snap_with_video(VIDEO_ACTIVE)
    st = {"state": "playing", "transport": "srt", "last_frame_mono": snap.now_mono - 5, "stale_after_s": 2,
          "fps": 20.0, "reconnects": 0}
    out = pipeline_summary(snap, st)
    assert out["transport"][1] == OK
    assert out["decoded"][1] == BAD and "לא חי" in out["decoded"][0]
    old = pipeline_summary(_snap_with_video(VIDEO_ACTIVE, age=120), None)
    assert old["service"][1] == UNKNOWN and old["encoder"][1] == UNKNOWN


def test_service_without_progress_waits_for_caller():
    data = dict(VIDEO_ACTIVE, progress={"exists": False})
    out = pipeline_summary(_snap_with_video(data), {"state": "connecting", "transport": "srt"})
    assert out["encoder"][1] == WARN and out["transport"][1] == WARN and out["decoded"][1] == WARN
