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


def run_selftest(out_path: str | None, offscreen: bool = False) -> int:
    from . import __version__

    tmp = Path(tempfile.mkdtemp(prefix="x500fs-selftest-"))
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
    step("video_decode", lambda: _video(tmp))
    step("gui_simulation_report", lambda: _gui(tmp))
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
    import av
    import keyring  # noqa: F401
    import paramiko
    import PySide6
    from pymavlink.dialects.v20 import ardupilotmega  # noqa: F401
    from PySide6 import QtWidgets  # noqa: F401

    return {"PySide6": PySide6.__version__, "paramiko": paramiko.__version__, "av": av.__version__,
            "ffmpeg": str(getattr(av, "ffmpeg_version_info", "?"))}


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
    for name in ("x500fs_collector.py", "x500fs_camera.py"):
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
    import av

    from .video.worker import PyAvWorker

    path = tmp / "selftest.mp4"
    out = av.open(str(path), "w")
    codec = "libx264" if "libx264" in av.codecs_available else "mpeg4"
    s = out.add_stream(codec, rate=15)
    s.width, s.height, s.pix_fmt = 160, 120, "yuv420p"
    for i in range(30):
        f = av.VideoFrame(160, 120, "rgb24")
        f.planes[0].update(bytes([(i * 8) % 256, 90, 160]) * (160 * 120))
        for pkt in s.encode(f):
            out.mux(pkt)
    for pkt in s.encode():
        out.mux(pkt)
    out.close()
    w = PyAvWorker(lambda: str(path), max_reconnects=0)
    w.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and w.running():
        time.sleep(0.1)
    w.stop()
    if w.frames < 20:
        raise RuntimeError(f"decoded only {w.frames} frames")
    return {"codec": codec, "frames": w.frames, "size": f"{w.width}x{w.height}"}


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
