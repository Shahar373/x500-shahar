"""Application controller (Qt-free).

Owns the observation store, session log, workers and settings. UI calls are
non-blocking: anything that touches the network runs in a worker thread.
Callbacks on :class:`Listener` may arrive from worker threads; the UI bridge
re-emits them as queued Qt signals.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import credentials
from .core import profile as profile_mod
from .core.checks import Ctx, DroneConfig, evaluate
from .core.manual import ManualChecklist
from .core.mav import parse as P
from .core.observations import ObservationStore
from .core.params import all_params
from .core.report import build_report, save_report
from .core.session import SessionLog
from .core.status import aggregate
from .settings import Settings, SettingsStore
from .sim.simulator import SimDrone
from .transport.mav_ingest import MavIngest
from .transport.mavlink_client import MavConfig, MavlinkClient
from .transport.pi_link import PiPoller, SshManager
from .transport.ssh import HostKeyUnknown, KnownHosts, LocalForwarder, SshAuth, SshError
from .transport.streams import SshChannelStream, TcpStream, UdpListenStream
from .transport.pi_video import PiVideoControl, password_help
from .video.worker import FfmpegWorker, SimVideoWorker, rtsp_source, srt_source

log = logging.getLogger(__name__)


class Listener:
    def on_connection_changed(self) -> None: ...
    def on_statustext(self, item: dict) -> None: ...
    def on_hostkey_unknown(self, info: dict) -> None: ...
    def on_video_frame(self) -> None: ...
    def on_video_status(self, status: dict) -> None: ...
    def on_notice(self, level: str, text: str) -> None: ...


@dataclass
class CameraGate:
    allowed: bool
    reason: str


class Controller:
    def __init__(self, settings_store: SettingsStore, listener: Listener | None = None):
        self.settings_store = settings_store
        self.listener = listener or Listener()
        self.settings: Settings = settings_store.load()
        self.profile_error: str | None = None
        self.profile = None
        self.load_profile()
        self.session = SessionLog()
        self.store = ObservationStore(simulated=self.settings.simulation)
        self.manual = ManualChecklist()
        self.statustext: deque[dict] = deque(maxlen=300)
        self.connected = False
        self.connecting_mode: str | None = None  # "live" | "simulation"
        self._sim: SimDrone | None = None
        self._ssh: SshManager | None = None
        self._poller: PiPoller | None = None
        self._mav: MavlinkClient | None = None
        self._video = None
        self._forwarder: LocalForwarder | None = None
        self._pending_hostkey: HostKeyUnknown | None = None
        self._lock = threading.Lock()
        self.preflight_started: float | None = None
        self.camera_info: dict | None = None
        self._video_service_started: str | None = None  # profile this app started on the Pi
        self.video_service_log: list[dict] = []
        self.video_perf: list[dict] = []
        self._perf_last_t: float | None = None

    # ------------------------------------------------------------------ settings
    @property
    def simulation(self) -> bool:
        return self.store.simulated

    def load_profile(self) -> None:
        pdir = self.settings_store.profiles_dir
        try:
            profile_mod.ensure_user_profiles(pdir)
        except OSError as e:
            log.warning("cannot create user profiles: %s", e)
        self.profile, self.profile_error = profile_mod.load(self.settings.profile_id, pdir)

    def available_profiles(self) -> list[str]:
        return profile_mod.list_profiles(self.settings_store.profiles_dir)

    def set_profile(self, profile_id: str) -> None:
        self.settings.profile_id = profile_id
        self.load_profile()
        self.save_settings()

    def save_settings(self) -> None:
        self.settings_store.save(self.settings)

    def apply_settings(self, new: Settings) -> None:
        """Replace settings (from the settings dialog). Requires reconnect to take effect."""
        self.settings = new
        self.save_settings()
        self.load_profile()
        if not self.connected:
            self.store = ObservationStore(simulated=new.simulation)

    def drone_config(self) -> DroneConfig:
        s = self.settings
        ssh = self.store.get("pi.ssh")
        return DroneConfig(
            name=s.drone_name,
            expected_hostname=s.connection.expected_hostname,
            link_path=s.connection.link_path,
            services=dict(s.pi.services),
            blackbox_globs=list(s.pi.blackbox_globs),
            mav_source_sysid=s.mavlink.source_sysid,
            mav_source_compid=s.mavlink.source_compid,
            mav_target_sysid=s.mavlink.target_sysid,
            video_active=self._video is not None,
            pinned_hostkey=(ssh.value.get("fingerprint") if ssh else None),
            watchdog_expected_runtime=s.pi.watchdog_expected_runtime,
            blackbox_sleep_s=s.pi.blackbox_sleep_s,
            expected_fc_byid=s.pi.expected_fc_byid,
            video_backend=s.video.backend,
            srt_profile=s.video.srt_profile,
            ping_target_configured=bool(s.pi.ping_target),
            zte_gateway_configured=bool(s.pi.zte_gateway),
            ground_ip_configured=bool(s.pi.ground_ip),
        )

    # ------------------------------------------------------------------ connect
    def connect(self) -> None:
        if self.connected:
            return
        s = self.settings
        self.session = SessionLog()
        self.store = ObservationStore(simulated=s.simulation)
        self.statustext.clear()
        self.video_perf = []
        self._perf_last_t = None
        self.connected = True
        if s.simulation:
            self.connecting_mode = "simulation"
            self._sim = SimDrone(self.store, self.session, s.sim_scenario, s.connection.link_path,
                                 on_text=self._on_text)
            self._sim.start()
        else:
            self.connecting_mode = "live"
            if not s.is_configured():
                self.connected = False
                self.listener.on_notice("error", "יש להגדיר כתובת ושם משתמש של ה-Pi בהגדרות לפני החיבור.")
                self.listener.on_connection_changed()
                return
            self._start_live()
        self.listener.on_connection_changed()

    def _auth(self) -> SshAuth:
        c = self.settings.connection
        auth = SshAuth(method=c.auth_method, key_path=c.key_path)
        if c.auth_method == "password":
            auth.password = credentials.get(credentials.password_id(c.username, c.host, c.ssh_port))
        elif c.key_path:
            auth.passphrase = credentials.get(credentials.passphrase_id(c.key_path))
        return auth

    def known_hosts(self) -> KnownHosts:
        return KnownHosts(self.settings_store.known_hosts)

    def _start_live(self) -> None:
        s = self.settings
        c = s.connection
        self._ssh = SshManager(
            self.store, self.session, host=c.host, port=c.ssh_port, user=c.username,
            auth_provider=self._auth, known_hosts=self.known_hosts(), connect_timeout=c.connect_timeout_s,
            on_hostkey_unknown=self._on_hostkey_unknown)
        self._ssh.start()
        self._poller = PiPoller(self._ssh, self.store, self.session,
                                settings_provider=lambda: (self.settings.pi, self.settings.video),
                                previous_boot_id=s.last_boot_id, on_boot_id=self._on_boot_id)
        self._poller.start()
        m = s.mavlink
        ingest = MavIngest(self.store, self.session, simulated=False, endpoint=self._mav_endpoint_text(),
                           target_sysid=m.target_sysid, my_sysid=m.source_sysid, my_compid=m.source_compid,
                           on_text=self._on_text)
        cfg = MavConfig(source_sysid=m.source_sysid, source_compid=m.source_compid,
                        request_messages=m.request_messages, request_interval_s=m.request_interval_s)
        self._mav = MavlinkClient(self._mav_stream_factory(), ingest, self.session, cfg,
                                  describe=self._mav_endpoint_text())
        self._mav.start()

    def _mav_endpoint_text(self) -> str:
        m = self.settings.mavlink
        if m.transport == "tcp":
            return f"tcp:{m.tcp_host}:{m.tcp_port}"
        if m.transport == "udp_listen":
            return f"udp-listen:{m.udp_port}"
        return f"ssh:{self.settings.connection.host} → {m.tunnel_host}:{m.tunnel_port}"

    def _mav_stream_factory(self):
        m = self.settings.mavlink
        if m.transport == "tcp":
            return lambda: TcpStream(m.tcp_host or self.settings.connection.host, m.tcp_port)
        if m.transport == "udp_listen":
            return lambda: UdpListenStream(m.udp_port)

        def factory():
            ssh = self._ssh.wait_session(timeout=5.0)
            return SshChannelStream(ssh, m.tunnel_host, m.tunnel_port)
        return factory

    def _on_boot_id(self, boot_id: str) -> None:
        self.settings.last_boot_id = boot_id
        try:
            self.save_settings()
        except OSError:
            pass

    def _on_hostkey_unknown(self, e: HostKeyUnknown) -> None:
        self._pending_hostkey = e
        self.listener.on_hostkey_unknown({"host": e.host, "port": e.port, "key_type": e.key_type,
                                          "fingerprint": e.fingerprint})

    def trust_pending_host_key(self) -> None:
        e = self._pending_hostkey
        if e is None:
            return
        self.known_hosts().add(e.host, e.port, e.key)
        self._pending_hostkey = None
        self.session.event("info", "ssh", f"מפתח השרת אושר ונשמר: {e.key_type} {e.fingerprint}")
        if self._ssh:
            self._ssh.stop()
            self._ssh.start()

    def reject_pending_host_key(self) -> None:
        self._pending_hostkey = None
        self.disconnect()

    def forget_host_key(self) -> bool:
        c = self.settings.connection
        return self.known_hosts().forget(c.host, c.ssh_port)

    def disconnect(self) -> None:
        if not self.connected:
            return
        self.connected = False
        self.stop_video()
        workers = [w for w in (self._sim, self._mav, self._poller, self._ssh) if w is not None]
        self._sim = self._mav = self._poller = self._ssh = None
        if self._forwarder:
            self._forwarder.stop()
            self._forwarder = None

        def _stop_all():
            for w in workers:
                try:
                    w.stop()
                except Exception:  # noqa: BLE001
                    log.exception("stop failed")
        threading.Thread(target=_stop_all, name="disconnect", daemon=True).start()
        self.session.event("info", "app", "נותק לבקשת המשתמש")
        self.listener.on_connection_changed()

    def shutdown(self) -> None:
        self.disconnect()

    # ------------------------------------------------------------------ preflight
    def run_preflight(self) -> None:
        self.session.new_preflight()
        self.manual.reset()
        self.preflight_started = time.time()
        self.session.event("info", "app", "התחיל סשן בדיקה חדש - הרשימה הידנית אופסה")
        if self._sim:
            self._sim.request_refresh(prearm_diagnostic=self.settings.mavlink.prearm_diagnostic)
        if self._poller:
            self._poller.refresh()
        if self._mav:
            extra = []
            for group in (self.profile.expected_params or {}).values():
                extra.extend(group.keys())
            self._mav.refresh(all_params(extra))
            if self.settings.mavlink.prearm_diagnostic:
                self._mav.request_prearm_diagnostic()

    def preflight_progress(self) -> dict:
        """What has been refreshed since 'run preflight' was pressed."""
        if self.preflight_started is None:
            return {}
        out = {}
        col = self.store.get("pi.collector")
        out["pi"] = bool(col and col.t_wall >= self.preflight_started)
        if self._mav:
            out["params_pending"] = self._mav.pending_params()
        elif self._sim:
            out["params_pending"] = 0
        return out

    def _sample_video_performance(self) -> None:
        """While video plays, pair each new Pi sample (CPU/temp/processes) with the measured FPS."""
        w = self._video
        col = self.store.get("pi.collector")
        if w is None or col is None or col.t_mono == self._perf_last_t or w.state != "playing":
            return
        self._perf_last_t = col.t_mono
        try:
            sec = col.value["sections"]
            cpu = sec["cpu"]["data"] or {}
            temp = (sec["thermal"]["data"] or {}).get("cpu_temp_c")
        except (KeyError, TypeError):
            return
        st = w.status()
        v = self.settings.video
        self.video_perf.append({
            "t": time.strftime("%H:%M:%S"),
            "requested": self._video_profile_text(),
            "decoded": f"{st.get('width')}x{st.get('height')}",
            "fps_measured": st.get("fps"),
            "pi_cpu_total_pct": cpu.get("usage_pct"),
            "pi_temp_c": temp,
            "camera_processes": [p for p in cpu.get("processes") or []
                                 if not str(p.get("name", "")).startswith("mavlink")],
        })
        if len(self.video_perf) > 720:
            del self.video_perf[:360]

    def video_perf_summary(self) -> dict | None:
        rows = self.video_perf
        if not rows:
            return None

        def stats(key):
            vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
            return None if not vals else {"min": min(vals), "avg": round(sum(vals) / len(vals), 1), "max": max(vals)}
        cam = [sum(p.get("cpu_pct_of_one_core") or 0 for p in r["camera_processes"]) for r in rows]
        return {
            "samples": len(rows),
            "requested": rows[-1]["requested"],
            "fps_measured": stats("fps_measured"),
            "pi_cpu_total_pct": stats("pi_cpu_total_pct"),
            "pi_temp_c": stats("pi_temp_c"),
            "camera_processes_cpu_pct_of_one_core": None if not cam else
            {"min": min(cam), "avg": round(sum(cam) / len(cam), 1), "max": max(cam)},
            "note": "דגימות CPU של חצי שנייה כל מחזור איסוף; אינן מודדות השהיה (latency).",
            "rows": rows[-60:],
        }

    def evaluate(self):
        self._sample_video_performance()
        ctx = Ctx(self.store.snapshot(), self.profile, self.drone_config(), self.session)
        results = evaluate(ctx)
        return results, aggregate(results), ctx.snap

    # ------------------------------------------------------------------ telemetry helpers
    def _on_text(self, item: dict) -> None:
        self.statustext.append(item)
        self.listener.on_statustext(item)

    def vehicle_armed(self) -> tuple[bool | None, str]:
        o = self.store.get("mav.msg.HEARTBEAT")
        if o is None:
            return None, "לא התקבל HEARTBEAT מבקר הטיסה"
        age = time.monotonic() - o.t_mono
        if age > 3.0:
            return None, f"ה-HEARTBEAT האחרון ישן ({age:.0f}s)"
        armed = P.is_armed(o.value)
        if armed is None:
            return None, "שדה base_mode לא תקין"
        return armed, ""

    def camera_gate(self) -> CameraGate:
        if not self.connected:
            return CameraGate(False, "יש להתחבר קודם")
        armed, why = self.vehicle_armed()
        if armed is None:
            return CameraGate(False, f"לא ניתן לאשר שהרחפן אינו חמוש ({why}). הפעלת מצלמה מותרת רק כשהוא לא חמוש.")
        if armed:
            return CameraGate(False, "הרחפן חמוש. הפעלה או שינוי הגדרות מצלמה מותרים רק כשהוא לא חמוש.")
        return CameraGate(True, "")

    # ------------------------------------------------------------------ video
    def video_running(self) -> bool:
        return self._video is not None

    def start_video(self) -> CameraGate:
        """Explicit user action. Requires a fresh, verified disarmed state."""
        gate = self.camera_gate()
        if not gate.allowed or self._video is not None:
            return gate
        v = self.settings.video
        common = dict(on_status=self._on_video_status, on_frame=self.listener.on_video_frame,
                      stale_after_s=v.stale_after_s)
        if self.simulation:
            if v.backend == "srt":
                prof = v.profile()
                self._video = SimVideoWorker(prof["width"], prof["height"], prof["fps"], **common)
                if self._sim:
                    self._sim.video_profile = v.srt_profile
            else:
                self._video = SimVideoWorker(v.width, v.height, v.fps, **common)
            self._video.start()
            return gate
        if v.backend == "srt":
            prof = v.profile()
            worker = FfmpegWorker(self._srt_source, width=prof["width"], height=prof["height"],
                                  ffmpeg_path=v.ffmpeg_path, open_timeout_s=v.open_timeout_s,
                                  max_reconnects=v.max_reconnects, on_link=self._on_video_link, **common)
            self._video = worker

            def _prepare_and_start():
                if v.manage_pi_service and not self._ensure_pi_video_service(v.srt_profile):
                    if self._video is worker:
                        self._video = None
                        self._on_video_status({**worker.status(), "state": "failed",
                                               "error": "שירות הווידאו ב-Pi לא הופעל"})
                    return
                if self._video is worker:
                    worker.start()
            threading.Thread(target=_prepare_and_start, name="video-start", daemon=True).start()
            return gate
        worker = FfmpegWorker(self._rtsp_source, width=v.width, height=v.height, ffmpeg_path=v.ffmpeg_path,
                              open_timeout_s=v.open_timeout_s, max_reconnects=v.max_reconnects,
                              on_link=self._on_video_link, **common)
        self._video = worker

        def _prepare_and_start_rtsp():
            if v.apply_camera_settings:
                self._configure_camera()
            if self._video is worker:
                worker.start()
        threading.Thread(target=_prepare_and_start_rtsp, name="video-start", daemon=True).start()
        return gate

    # -- SRT (existing Pi pipeline) -------------------------------------
    def pi_tailscale_ipv4(self) -> str | None:
        col = self.store.get("pi.collector")
        if col is None or not isinstance(col.value, dict):
            return None
        secs = col.value.get("sections") or {}
        ts = (((secs.get("net_probes") or {}).get("data") or {}).get("tailscale") or {})
        if ts.get("ipv4"):
            return ts["ipv4"]
        ips = ((((secs.get("network") or {}).get("data") or {}).get("tailscale") or {}).get("self_ips") or [])
        return next((ip for ip in ips if isinstance(ip, str) and ip.count(".") == 3), None)

    def _srt_source(self):
        v = self.settings.video
        host = v.srt_host.strip() or self.pi_tailscale_ipv4()
        if not host:
            raise RuntimeError("כתובת ה-Tailscale של ה-Pi לא ידועה עדיין (ממתין לנתוני ה-Pi) ולא הוגדרה כתובת SRT")
        return srt_source(host, v.srt_port, v.profile()["latency_us"], extra=v.srt_extra_params)

    def _video_log(self, action: str, profile: str, ok: bool, message: str = "") -> None:
        self.video_service_log.append({"t_wall": time.time(), "action": action, "profile": profile, "ok": ok,
                                       "message": message[:300]})
        del self.video_service_log[:-50]

    def _ensure_pi_video_service(self, profile: str) -> bool:
        """Start x500-video@<profile> if needed (allowlisted sudo -n systemctl). True when it runs."""
        try:
            ssh = self._ssh.wait_session(5.0) if self._ssh else None
            if ssh is None:
                raise SshError("SSH not connected")
            ctl = PiVideoControl(ssh, self.settings.video.unit_template)
            states = ctl.states()
        except Exception as e:  # noqa: BLE001
            msg = f"לא ניתן לקרוא את מצב שירות הווידאו ב-Pi: {type(e).__name__}: {e}"
            self.listener.on_notice("warning", msg)
            self._video_log("state", profile, False, msg)
            return False
        if ctl.is_running(states.get(profile, {})):
            self._video_log("state", profile, True, "already running")
            return True
        gate = self.camera_gate()  # re-check right before changing anything on the Pi
        if not gate.allowed:
            self.listener.on_notice("warning", gate.reason)
            return False
        for other, st in states.items():
            if other != profile and ctl.is_running(st):
                res = ctl.stop(other)
                self._video_log("stop", other, res.ok, res.message)
                if not res.ok:
                    self.listener.on_notice("warning", password_help(profile) if res.needs_password else
                                            f"פרופיל וידאו אחר ({other}) פועל ב-Pi ולא נעצר: {res.message}")
                    return False
                self.session.event("info", "video", f"שירות הווידאו {other} נעצר ב-Pi (החלפת פרופיל)")
        res = ctl.start(profile)
        self._video_log("start", profile, res.ok, res.message)
        if not res.ok:
            self.listener.on_notice("warning", password_help(profile) if res.needs_password else
                                    f"הפעלת שירות הווידאו ב-Pi נכשלה: {res.message}")
            self.session.event("warning", "video", f"הפעלת x500-video@{profile} נכשלה: {res.message}")
            return False
        self._video_service_started = profile
        self.session.event("info", "video", f"שירות הווידאו x500-video@{profile} הופעל ב-Pi (פעולה מפורשת)")
        deadline = time.monotonic() + max(1.0, self.settings.video.service_start_wait_s)
        while time.monotonic() < deadline:
            try:
                if ctl.is_running(ctl.state(profile)):
                    time.sleep(0.8)  # the listener binds right after the unit becomes active
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.4)
        self.listener.on_notice("warning", f"x500-video@{profile} לא הגיע למצב פעיל. בדוק את יומן השירות ב-Pi.")
        return False

    def _stop_pi_video_service(self, profile: str) -> None:
        gate = self.camera_gate()
        if not gate.allowed:
            self.listener.on_notice("warning", "הצפייה נעצרה במחשב, אבל שירות הווידאו ב-Pi נשאר פועל: " + gate.reason)
            self._video_log("stop", profile, False, "not stopped: " + gate.reason)
            return
        try:
            ssh = self._ssh.wait_session(5.0) if self._ssh else None
            if ssh is None:
                raise SshError("SSH not connected")
            res = PiVideoControl(ssh, self.settings.video.unit_template).stop(profile)
        except Exception as e:  # noqa: BLE001
            self.listener.on_notice("warning", f"עצירת שירות הווידאו ב-Pi נכשלה: {type(e).__name__}: {e}")
            return
        self._video_log("stop", profile, res.ok, res.message)
        if res.ok:
            self.session.event("info", "video", f"שירות הווידאו x500-video@{profile} נעצר ב-Pi")
        else:
            self.listener.on_notice("warning", password_help(profile) if res.needs_password else
                                    f"עצירת שירות הווידאו ב-Pi נכשלה: {res.message}")

    # -- RTSP / MediaMTX alternative -------------------------------------
    def _rtsp_source(self):
        v = self.settings.video
        return rtsp_source(self._video_url(), "tcp" if v.transport == "ssh_tunnel" else v.rtsp_transport)

    def _video_url(self) -> str:
        v = self.settings.video
        if v.direct_url:
            return v.direct_url
        if v.transport == "ssh_tunnel":
            if self._forwarder is None:
                if self._ssh is None:
                    raise SshError("SSH not running")
                fw = LocalForwarder(lambda: self._ssh.wait_session(5.0), "127.0.0.1", v.rtsp_port)
                fw.start()
                self._forwarder = fw
            return f"rtsp://127.0.0.1:{self._forwarder.port}/{v.path}"
        return f"rtsp://{self.settings.connection.host}:{v.rtsp_port}/{v.path}"

    def _configure_camera(self) -> None:
        """Explicit camera reconfiguration (runtime MediaMTX path config) - disarmed only."""
        import json
        import shlex

        from .pi_side import script_source

        gate = self.camera_gate()
        if not gate.allowed:
            self.listener.on_notice("warning", gate.reason)
            return
        v = self.settings.video
        try:
            ssh = self._ssh.wait_session(5.0)
            cmd = (f"{shlex.quote(self.settings.pi.python)} -I - configure --api {shlex.quote(v.mediamtx_api)} "
                   f"--path {shlex.quote(v.path)} --width {int(v.width)} --height {int(v.height)} "
                   f"--fps {float(v.fps)} --bitrate {int(v.bitrate_kbps) * 1000}")
            res = ssh.exec(cmd, stdin=script_source("x500fs_camera.py"), timeout=15)
            info = json.loads(res.stdout.decode("utf-8", "replace") or "{}")
        except Exception as e:  # noqa: BLE001
            info = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        self.camera_info = info
        if info.get("ok"):
            changed = info.get("changed") or {}
            self.session.event("info", "camera", "הגדרות מצלמה: " + (str(changed) if changed else "ללא שינוי"))
        else:
            self.listener.on_notice("warning", f"לא ניתן להחיל הגדרות מצלמה: {info.get('error')}")
            self.session.event("warning", "camera", f"הגדרת מצלמה נכשלה: {info.get('error')}")

    def _on_video_link(self, up: bool, reason: str) -> None:
        if up:
            self.session.link_up("video")
        else:
            self.session.link_down("video", reason)

    def _on_video_status(self, st: dict) -> None:
        try:
            self.store.put("video.state", st, "video", simulated=self.store.simulated)
        except Exception:  # noqa: BLE001
            pass
        self.listener.on_video_status(st)

    def stop_video(self) -> None:
        w = self._video
        self._video = None
        if w is None:
            return
        try:
            self.store.put("video.state", {"state": "stopped"}, "video", simulated=self.store.simulated)
        except Exception:  # noqa: BLE001
            pass
        self.session.link_up("video")  # an explicit stop is not an interruption
        started, self._video_service_started = self._video_service_started, None
        v = self.settings.video
        if self._sim:
            self._sim.video_profile = None

        def _stop():
            w.stop()
            if started and not self.simulation and v.manage_pi_service and v.stop_pi_service_on_stop:
                self._stop_pi_video_service(started)
        threading.Thread(target=_stop, name="video-stop", daemon=True).start()
        fw = self._forwarder
        self._forwarder = None
        if fw:
            fw.stop()

    def video_worker(self):
        return self._video

    # ------------------------------------------------------------------ report
    def settings_summary(self) -> dict[str, Any]:
        s = self.settings
        return {
            "mode": "simulation" if self.simulation else "live",
            "drone": s.drone_name,
            "host": s.connection.host if not self.simulation else "SIM",
            "ssh_user": s.connection.username if not self.simulation else "SIM",
            "auth_method": s.connection.auth_method,
            "link_path": s.connection.link_path,
            "mavlink_transport": s.mavlink.transport,
            "mavlink_endpoint": self._mav_endpoint_text() if not self.simulation else "SIM",
            "mavlink_requests_enabled": s.mavlink.request_messages,
            "app_sysid": s.mavlink.source_sysid,
            "video_backend": s.video.backend,
            "video_transport": "srt (caller)" if s.video.backend == "srt" else f"rtsp ({s.video.transport})",
            "video_profile": self._video_profile_text(),
            "video_manage_pi_service": s.video.manage_pi_service,
        }

    def _video_profile_text(self) -> str:
        v = self.settings.video
        if v.backend == "srt":
            p = v.profile()
            return (f"{v.srt_profile}: {p['width']}x{p['height']}@{p['fps']} {p['bitrate_kbps']}kbps, "
                    f"SRT latency {p['latency_us'] // 1000}ms (requested, not measured)")
        return f"{v.width}x{v.height}@{v.fps}"

    def export_report(self, directory: Path | None = None) -> tuple[Path, Path]:
        results, overall, snap = self.evaluate()
        secrets = []
        c = self.settings.connection
        for ident in (credentials.password_id(c.username, c.host, c.ssh_port),
                      credentials.passphrase_id(c.key_path) if c.key_path else None):
            if ident and not self.simulation:
                val = credentials.get(ident)
                if val:
                    secrets.append(val)
        from .video.ffmpeg_tools import describe as ffmpeg_describe
        video = {"current": self._video.status() if self._video else None,
                 "camera_settings_applied": self.camera_info,
                 "pi_service_actions": list(self.video_service_log),
                 "decoder": ffmpeg_describe(self.settings.video.ffmpeg_path) if not self.simulation else "SIM",
                 "performance": self.video_perf_summary()}
        doc = build_report(
            snap=snap, results=results, overall=overall, profile_summary=self.profile.summary(),
            profile_error=self.profile_error, settings_summary=self.settings_summary(),
            manual=self.manual.to_list(), manual_done=self.manual.done_count(), manual_total=self.manual.total(),
            interruptions=[i.to_dict() for i in self.session.interruptions()],
            events=[e.to_dict() for e in self.session.events()], statustext=list(self.statustext),
            video=video, session_id=self.session.session_id, preflight_started=self.preflight_started,
            extra_secrets=secrets,
        )
        return save_report(doc, directory or self.settings.reports_path())
