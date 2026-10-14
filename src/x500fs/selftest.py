"""Self-test for the packaged application (no hardware, no network beyond localhost).

    X500FlightStation.exe --self-test result.json [--offscreen]

Used by the Windows CI job on the *installed* app (with Python removed from
PATH) to prove the bundle works without a separately installed interpreter.
It uses a temporary config directory and never touches the user's settings.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
import time
import traceback
from pathlib import Path


_crash_file = None


def run_selftest(out_path: str | None, offscreen: bool = False) -> int:
    from . import __version__

    tmp = Path(tempfile.mkdtemp(prefix="x500fs-selftest-"))
    if out_path:
        import faulthandler

        # A native crash leaves the Python stacks of every thread next to the result file.
        global _crash_file
        _crash_file = open(str(out_path) + ".crash.log", "w", encoding="utf-8")  # noqa: SIM115
        faulthandler.enable(file=_crash_file, all_threads=True)
    os.environ["X500FS_CONFIG_DIR"] = str(tmp / "config")
    if offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    report = {
        "app_version": __version__,
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "python": sys.version,
        "platform": platform.platform(),
        "steps": [],
    }

    def step(name, fn):
        t0 = time.monotonic()
        try:
            detail = fn()
            report["steps"].append({"name": name, "ok": True, "detail": detail,
                                    "seconds": round(time.monotonic() - t0, 2)})
        except Exception as e:  # noqa: BLE001
            report["steps"].append({"name": name, "ok": False, "error": f"{type(e).__name__}: {e}",
                                    "trace": traceback.format_exc()[-2000:],
                                    "seconds": round(time.monotonic() - t0, 2)})

    step("imports", _imports)
    step("profiles", _profiles)
    step("pi_scripts", _pi_scripts)
    step("mavlink_mock_tcp", _mavlink)
    step("ffmpeg_srt_support", _ffmpeg_info)
    step("video_decode_file", lambda: _video(tmp))
    step("video_srt_end_to_end", _video_srt)
    step("gui_simulation_report", lambda: _gui(tmp))
    step("live_connect_ui", lambda: _live_connect(tmp))
    step("credential_backend", _credentials)
    report["ok"] = all(s["ok"] for s in report["steps"])
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if out_path:
        Path(out_path).write_text(text, "utf-8")
    else:
        try:
            print(text)
        except Exception:  # noqa: BLE001 - windowed exe has no console
            pass
    return 0 if report["ok"] else 1


def _imports():
    import imageio_ffmpeg
    import keyring  # noqa: F401
    import paramiko
    import PySide6
    from pymavlink.dialects.v20 import ardupilotmega  # noqa: F401
    from PySide6 import QtWidgets  # noqa: F401

    return {"PySide6": PySide6.__version__, "paramiko": paramiko.__version__,
            "imageio_ffmpeg": imageio_ffmpeg.__version__}


def _ffmpeg_info():
    from .video.ffmpeg_tools import find_ffmpeg, probe

    exe = find_ffmpeg()
    info = dict(probe(exe))
    if not info["srt"]:
        raise RuntimeError(f"bundled FFmpeg has no SRT input protocol: {info}")
    info["path"] = exe
    info["input_protocols"] = [p for p in info["input_protocols"] if p in ("srt", "rtsp", "tcp", "udp", "file")]
    return info


def _profiles():
    from .core import profile

    out = {}
    for pid in profile.BUILTIN_IDS:
        p = profile.load_builtin(pid)
        out[pid] = {"checks": len(p.checks), "sha256": p.sha256[:12]}
    return out


def _pi_scripts():
    from .pi_side import script_source

    out = {}
    for name in ("x500fs_collector.py", "x500fs_camera.py", "x500fs_diag.py"):
        src = script_source(name)
        compile(src, name, "exec")
        out[name] = len(src)
    return out


def _mavlink():
    from .core.observations import ObservationStore
    from .core.session import SessionLog
    from .sim.mock_mavlink import MockAutopilot
    from .transport.mav_ingest import MavIngest
    from .transport.mavlink_client import MavConfig, MavlinkClient
    from .transport.streams import TcpStream

    m = MockAutopilot().start()
    try:
        store = ObservationStore(simulated=False)
        sess = SessionLog()
        ing = MavIngest(store, sess, simulated=False, endpoint="selftest")
        c = MavlinkClient(lambda: TcpStream("127.0.0.1", m.port), ing, sess, MavConfig())
        c.start()
        c.refresh(["ARMING_CHECK"])
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not store.get("mav.param.ARMING_CHECK"):
            time.sleep(0.2)
        c.stop()
        types = {t for _, _, t, _ in m.received}
        if store.get("mav.msg.HEARTBEAT") is None:
            raise RuntimeError("no heartbeat decoded")
        if "HEARTBEAT" in types:
            raise RuntimeError("client sent HEARTBEAT")
        return {"messages": sorted(k for k in store.snapshot().data), "client_sent": sorted(types)}
    finally:
        m.stop()


def _video(tmp: Path):
    import subprocess

    from .video.ffmpeg_tools import NO_WINDOW, find_ffmpeg
    from .video.worker import FfmpegWorker, file_source

    ff = find_ffmpeg()
    path = tmp / "selftest.mp4"
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=160x120:rate=15", "-frames:v", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(path)], check=True, timeout=60, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    w = FfmpegWorker(lambda: file_source(path, realtime=False), width=160, height=120, max_reconnects=0)
    w.start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and w.running():
        time.sleep(0.1)
    w.stop()
    if w.frames != 30:
        raise RuntimeError(f"decoded {w.frames}/30 frames: {w.error}")
    return {"codec": w.codec, "frames": w.frames, "size": f"{w.width}x{w.height}"}


def _video_srt():
    """The app as SRT caller to a local listener that mimics x500-video-run (bundled FFmpeg on both sides)."""
    if sys.platform.startswith("linux"):
        # imageio-ffmpeg's Linux build is fully static and its resolver crashes on network
        # inputs; the product targets Windows, where this step runs in CI.
        return {"skipped": "linux static FFmpeg build cannot open network inputs"}
    from .video.testsource import SrtTestSource
    from .video.worker import FfmpegWorker, srt_source

    with SrtTestSource(profile="degraded") as src:
        w = FfmpegWorker(lambda: srt_source("127.0.0.1", src.port, 300000), width=480, height=360,
                         max_reconnects=2)
        w.start()
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline and w.frames < 30:
            time.sleep(0.1)
        st = w.status()
        w.stop()
    if st["frames"] < 30:
        raise RuntimeError(f"SRT: decoded only {st['frames']} frames; state={st['state']} error={st['error']} "
                           f"stderr={st.get('stderr_tail')}")
    return {"frames": st["frames"], "fps": st["fps"], "codec": st["codec"], "source_size": st["source_size"],
            "transport": st["transport"]}


def _gui(tmp: Path):
    from .settings import SettingsStore
    from .ui.app import create_application
    from .ui.main_window import MainWindow

    store = SettingsStore(tmp / "config")
    s = store.load()
    s.simulation = True
    s.reports_dir = str(tmp / "reports")
    store.save(s)
    app = create_application()
    w = MainWindow(store)
    w.show()
    w.ctl.connect()
    _pump(app, 2.5)
    w.ctl.run_preflight()
    _pump(app, 2.0)
    w._start_video()
    gate = w.ctl.camera_gate()
    _pump(app, 1.5)
    frames = w.ctl.video_worker().frames if w.ctl.video_worker() else 0
    results, overall, snap = w.ctl.evaluate()
    if not snap.simulated or not all(r.source.startswith("SIM") or r.source == "profile" for r in results
                                     if r.status.value != "NOT_APPLICABLE" and r.source not in ("", "evaluator")):
        raise RuntimeError("simulation results not marked as simulated")
    jp, hp = w.ctl.export_report()
    doc = json.loads(Path(jp).read_text("utf-8"))
    if doc.get("simulation") is not True:
        raise RuntimeError("report not marked as simulation")
    shot = tmp / "selftest-window.png"
    w.grab().save(str(shot))
    w.close()
    _pump(app, 0.5)
    return {"overall": overall.status.value, "checks": len(results), "video_gate": gate.allowed,
            "sim_video_frames": frames, "report": Path(hp).name, "screenshot": str(shot)}


def _live_connect(tmp: Path):
    """The real 'Connect' path of the UI against an in-process SSH server and mock autopilot:
    host-key prompt, password auth, collector over exec, MAVLink over the SSH tunnel, preflight, report."""
    import threading
    import traceback

    from . import credentials
    from .settings import SettingsStore
    from .sim.mock_mavlink import MockAutopilot
    from .sim.mock_sshd import MockSshd
    from .ui.app import create_application
    from .ui.dialogs import HostKeyDialog
    from .ui.main_window import MainWindow

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    ap = MockAutopilot().start()
    sshd = MockSshd(forward_map={("127.0.0.1", 5760): ("127.0.0.1", ap.port)}).start()
    errors: list[str] = []
    saved = (credentials.get, sys.excepthook, threading.excepthook)
    prompts = []
    cred_ident = credentials.password_id(sshd.user, "127.0.0.1", sshd.port)
    real_store = sys.platform == "win32"
    if real_store:
        # The real Windows Credential Manager, read later from the SSH worker thread (as in normal use).
        credentials.set(cred_ident, sshd.password)
    else:
        credentials.get = lambda ident: sshd.password if ident.startswith("ssh-password") else None

    def accept_hostkey_dialog():
        # The real modal HostKeyDialog is shown; accept it like a user who compared the fingerprint.
        dlg = QApplication.activeModalWidget()
        if isinstance(dlg, HostKeyDialog):
            prompts.append(dlg.windowTitle())
            dlg.accept()
    app = create_application()  # before any QTimer: timers need the application's event loop
    clicker = QTimer()
    clicker.timeout.connect(accept_hostkey_dialog)
    clicker.start(300)
    sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))
    threading.excepthook = lambda a: errors.append(
        f"thread {a.thread.name if a.thread else '?'}: " +
        "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))
    w = None
    try:
        store = SettingsStore(tmp / "live-config")
        s = store.load()
        s.simulation = False
        s.connection.host, s.connection.ssh_port = "127.0.0.1", sshd.port
        s.connection.username, s.connection.auth_method = sshd.user, "password"
        s.reports_dir = str(tmp / "live-reports")
        s.first_run_done = True
        store.save(s)
        w = MainWindow(store)
        w.show()
        w._toggle_connect()
        ctl = w.ctl

        def ready():
            ssh = ctl.store.get("pi.ssh")
            return (ssh is not None and ssh.value.get("state") == "connected"
                    and ctl.store.get("mav.msg.HEARTBEAT") is not None and ctl.store.get("pi.collector") is not None)
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline and not ready():
            _pump(app, 0.2)
        if not ready():
            ssh = ctl.store.get("pi.ssh")
            raise RuntimeError(f"not connected: ssh={ssh.value if ssh else None} prompts={len(prompts)} "
                               f"heartbeat={ctl.store.get('mav.msg.HEARTBEAT') is not None} "
                               f"collector_error={ctl.store.get('pi.collector_error')}")
        w._run_preflight()
        _pump(app, 4.0)
        results, overall, snap = ctl.evaluate()
        res = {r.check_id: r.status.value for r in results}
        jp, hp = ctl.export_report()
        ctl.disconnect()
        _pump(app, 1.0)
        if errors:
            raise RuntimeError("uncaught exception: " + errors[0][-1500:])
        sent = sorted({(t, d.get("command")) for _, _, t, d in ap.received}, key=str)
        if any(t == "HEARTBEAT" for t, _ in sent):
            raise RuntimeError("client sent HEARTBEAT")
        return {"hostkey_prompts": len(prompts), "credential_store": "windows" if real_store else "patched",
                "overall": overall.status.value,
                "pi.reachable": res.get("pi.reachable"), "link.mavlink_heartbeat": res.get("link.mavlink_heartbeat"),
                "fc.prearm": res.get("fc.prearm"), "ssh_commands": len(sshd.commands),
                "forwards": [list(f) for f in sshd.forward_requests][:3], "report": Path(hp).name,
                "client_sent": [list(x) for x in sent]}
    finally:
        clicker.stop()
        credentials.get, sys.excepthook, threading.excepthook = saved
        if real_store:
            credentials.delete(cred_ident)
        if w is not None:
            try:
                w.ctl.disconnect()
                w.close()
            except Exception:  # noqa: BLE001
                pass
        sshd.stop()
        ap.stop()


def _pump(app, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def _credentials():
    from . import credentials

    return {"backend": credentials.backend_name()}


def cli() -> int:
    args = sys.argv[1:]
    return run_selftest(args[0] if args and not args[0].startswith("--") else None, offscreen="--offscreen" in args)
