"""SSH connection management and periodic Pi collection.

Running the collector streams a read-only script to ``python3 -`` over SSH;
nothing is installed or changed on the Pi.
"""

from __future__ import annotations

import base64
import copy
import json
import logging
import shlex
import threading
import time
from typing import Callable

from ..core.observations import ObservationStore
from ..core.session import SessionLog
from ..pi_side import script_source
from ..pi_side import x500fs_collector as collector_mod
from .ssh import AuthFailed, HostKeyMismatch, HostKeyUnknown, KnownHosts, SshAuth, SshError, SshSession

log = logging.getLogger(__name__)

COLLECTOR_SCHEMA = "x500fs.collector/1"


class SshManager:
    def __init__(self, store: ObservationStore, session: SessionLog, *, host: str, port: int, user: str,
                 auth_provider: Callable[[], SshAuth], known_hosts: KnownHosts, connect_timeout: float = 10.0,
                 on_hostkey_unknown: Callable[[HostKeyUnknown], None] | None = None,
                 ping_interval_s: float = 2.0):
        self.store = store
        self.session = session
        self.host, self.port, self.user = host, port, user
        self.auth_provider = auth_provider
        self.known_hosts = known_hosts
        self.connect_timeout = connect_timeout
        self.on_hostkey_unknown = on_hostkey_unknown
        self.ping_interval_s = ping_interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ssh: SshSession | None = None
        self._connected = threading.Event()
        self.state = "disconnected"

    def _publish(self, state: str, error: str | None = None, fingerprint: str | None = None) -> None:
        self.state = state
        self.store.put("pi.ssh", {"state": state, "host": self.host, "port": self.port, "user": self.user,
                                  "error": error, "fingerprint": fingerprint},
                       f"SSH {self.user}@{self.host}:{self.port}", simulated=False)

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ssh-manager", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        self._connected.clear()
        ssh = self._ssh
        if ssh:
            ssh.close()
        if self._thread:
            self._thread.join(timeout)
        self._publish("disconnected")

    def current(self) -> SshSession | None:
        ssh = self._ssh
        return ssh if ssh and ssh.is_active() else None

    def wait_session(self, timeout: float) -> SshSession:
        if not self._connected.wait(timeout):
            raise SshError(f"SSH not connected ({self.state})")
        ssh = self.current()
        if ssh is None:
            raise SshError("SSH session lost")
        return ssh

    def _run(self) -> None:
        backoff = 2.0
        while not self._stop.is_set():
            self._publish("connecting")
            log.info("ssh: connecting to %s:%s", self.host, self.port)
            try:
                ssh = SshSession(self.host, self.port, self.user, self.auth_provider(), self.known_hosts,
                                 self.connect_timeout)
                ssh.connect()
            except HostKeyUnknown as e:
                self._publish("hostkey_unknown", str(e), e.fingerprint)
                self.session.event("warning", "ssh", f"מפתח שרת לא מוכר: {e.key_type} {e.fingerprint}")
                if self.on_hostkey_unknown:
                    self.on_hostkey_unknown(e)
                return
            except HostKeyMismatch as e:
                self._publish("hostkey_mismatch", str(e), e.fingerprint)
                self.session.event("error", "ssh", "מפתח השרת של ה-Pi השתנה! החיבור נחסם.")
                return
            except AuthFailed as e:
                self._publish("failed", f"authentication: {e}")
                self.session.event("error", "ssh", f"הזדהות נכשלה: {e}")
                return
            except (SshError, OSError) as e:
                log.info("ssh: connection failed: %s", e)
                self._publish("failed", str(e))
                self.session.link_down("ssh", str(e))
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 15.0)
                continue
            except Exception as e:  # noqa: BLE001 - anything else must be reported, never kill the worker
                log.exception("ssh: unexpected error while connecting")
                self._publish("failed", f"{type(e).__name__}: {e}")
                self.session.link_down("ssh", f"{type(e).__name__}: {e}")
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 15.0)
                continue
            backoff = 2.0
            log.info("ssh: connected (%s)", ssh.fingerprint)
            self._ssh = ssh
            self._connected.set()
            self._publish("connected", fingerprint=ssh.fingerprint)
            self.session.link_up("ssh")
            self.session.event("info", "ssh", f"מחובר ל-{self.host} ({ssh.fingerprint})")
            silent_since = None
            while not self._stop.is_set():
                if not ssh.is_active():
                    self.session.link_down("ssh", "connection lost")
                    break
                ms = ssh.ping(timeout=3.0)
                now = time.monotonic()
                if ms is None:
                    self.session.rtt.timeouts += 1
                    silent_since = silent_since or now
                    if now - silent_since > 4.0:
                        self.session.link_down("ssh", "no reply to keepalive")
                    if now - silent_since > 15.0:
                        break  # reconnect
                else:
                    if silent_since is not None:
                        self.session.link_up("ssh")
                    silent_since = None
                    self.session.rtt.add(ms)
                self._stop.wait(self.ping_interval_s)
            self._connected.clear()
            self._ssh = None
            ssh.close()
            if not self._stop.is_set():
                self._publish("lost", "connection lost")
                self._stop.wait(1.0)


def collector_config(pi_settings, video_settings) -> dict:
    tools = []
    for t in pi_settings.tools or []:
        if not isinstance(t, dict) or not t.get("enabled") or not t.get("command"):
            continue
        tools.append({"name": t.get("name") or t["command"].split()[0],
                      "argv": shlex.split(t["command"]),
                      "timeout_s": float(t.get("timeout_s") or 20)})
    services = {k: v for k, v in (pi_settings.services or {}).items() if v}
    return {
        "services": services,
        "blackbox_globs": list(pi_settings.blackbox_globs or []),
        "lte_usb_vids": list(pi_settings.lte_usb_vids or []),
        "wifi_interface": pi_settings.wifi_interface or "",
        "lte_interface": pi_settings.lte_interface or "",
        "tailscale_interface": pi_settings.tailscale_interface or "",
        "zte_gateway": pi_settings.zte_gateway or "",
        "ground_ip": pi_settings.ground_ip or "",
        "ping_target": pi_settings.ping_target or "",
        "fc_byid_glob": pi_settings.fc_byid_glob or "",
        "tools": tools,
        "mediamtx": {"api": video_settings.mediamtx_api, "path": video_settings.path},
        "camera_probe": bool(pi_settings.camera_probe),
        "video": {
            "profiles": list(video_settings.srt_profiles.keys()) or ["high", "normal", "degraded"],
            "unit_template": video_settings.unit_template,
            "progress_template": video_settings.progress_template,
            "config_file": video_settings.pi_config_file,
        },
    }


def sections_for(pi_settings, video_settings, slow: bool) -> list[str]:
    """Bounded cadence: cheap sections every cycle, slow probes only when due."""
    names = list(collector_mod.FAST_SECTIONS)
    if slow:
        # "tools" is always requested: with nothing configured it returns {} at once, and the
        # check can then say "not configured" instead of "not received".
        names += [n for n in collector_mod.SLOW_SECTIONS if n != "mediamtx"]
        if video_settings.backend == "rtsp":
            names.append("mediamtx")
    return names


def collector_command(pi_settings, cfg: dict, only: list[str] | None = None) -> tuple[str, bytes | None]:
    b64 = base64.b64encode(json.dumps(cfg, separators=(",", ":")).encode("utf-8")).decode("ascii")
    only_arg = f" --only {shlex.quote(','.join(only))}" if only else ""
    if pi_settings.collector_mode == "installed":
        return f"{shlex.quote(pi_settings.installed_collector_path)} --config-b64 {b64}{only_arg}", None
    return (f"{shlex.quote(pi_settings.python)} -I - --config-b64 {b64}{only_arg}",
            script_source("x500fs_collector.py"))


def parse_collector_output(stdout: bytes) -> dict:
    try:
        doc = json.loads(stdout.decode("utf-8", "replace"))
    except ValueError as e:
        raise ValueError(f"collector output is not JSON: {e}") from None
    if not isinstance(doc, dict):
        raise ValueError("collector output is not a JSON object")
    if doc.get("schema") != COLLECTOR_SCHEMA:
        raise ValueError(f"unexpected collector schema {doc.get('schema')!r}")
    if not isinstance(doc.get("sections"), dict):
        raise ValueError("collector output has no sections")
    return doc


class PiPoller:
    def __init__(self, ssh: SshManager, store: ObservationStore, session: SessionLog, *,
                 settings_provider: Callable[[], tuple], previous_boot_id: str = "",
                 on_boot_id: Callable[[str], None] | None = None):
        self.ssh = ssh
        self.store = store
        self.session = session
        self.settings_provider = settings_provider  # -> (pi_settings, video_settings)
        self.previous_boot_id = previous_boot_id or None
        self.on_boot_id = on_boot_id
        self._boots: list[str] = []
        self._stop = threading.Event()
        self._refresh = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_ok_mono: float | None = None
        self.busy = False
        self._force_slow = False
        self._last_video = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="pi-poller", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        self._refresh.set()
        if self._thread:
            self._thread.join(timeout)

    def refresh(self) -> None:
        """Run a full collection (fast + slow sections) as soon as possible."""
        self._force_slow = True
        self._refresh.set()

    def _put(self, key, value):
        self.store.put(key, value, "Pi collector", simulated=False)

    def _run(self) -> None:
        merged: dict = {"schema": COLLECTOR_SCHEMA, "sections": {}}
        next_slow = 0.0
        while not self._stop.is_set():
            pi_s, video_s = self.settings_provider()
            self._put("pi.poll_interval_s", pi_s.poll_interval_s)
            self._put("pi.slow_poll_interval_s", pi_s.slow_poll_interval_s)
            try:
                ssh = self.ssh.wait_session(timeout=1.0)
            except SshError:
                self._refresh.wait(1.0)
                self._refresh.clear()
                continue
            forced = self._force_slow
            self._force_slow = False
            slow = forced or time.monotonic() >= next_slow
            only = sections_for(pi_s, video_s, slow)
            self.busy = True
            try:
                cmd, stdin = collector_command(pi_s, collector_config(pi_s, video_s), only)
                res = ssh.exec(cmd, stdin=stdin, timeout=pi_s.collector_timeout_s, cancel=self._stop)
                if res.exit_code != 0:
                    err = res.stderr.decode("utf-8", "replace").strip()[-300:]
                    raise RuntimeError(f"collector exit {res.exit_code}: {err or 'no stderr'}")
                doc = parse_collector_output(res.stdout)
                received = time.monotonic()
                slow_set = set(collector_mod.SLOW_SECTIONS)
                for name, sec in doc["sections"].items():
                    if isinstance(sec, dict):
                        sec["_received_mono"] = received
                        sec["_cadence"] = "slow" if name in slow_set else "fast"
                        sec["_pi_epoch"] = doc.get("generated_at_epoch")
                        merged["sections"][name] = sec
                self._video_rate(doc, merged)
                for k in ("collector_version", "generated_at_epoch", "pi_monotonic_s", "duration_ms"):
                    merged[k] = doc.get(k)
                merged["_transport"] = {"command_duration_ms": int(res.duration_s * 1000),
                                        "laptop_epoch_at_receipt": time.time(), "sections": only}
                if slow:
                    next_slow = time.monotonic() + pi_s.slow_poll_interval_s
                self._put("pi.collector", copy.deepcopy(merged))
                self.store.remove_prefix("pi.collector_error")
                self.last_ok_mono = time.monotonic()
                self.session.link_up("collector")
                self._track_boot(merged)
            except Exception as e:  # noqa: BLE001
                msg = f"{type(e).__name__}: {e}"
                self._put("pi.collector_error", msg[:300])
                self.session.link_down("collector", msg[:200])
            finally:
                self.busy = False
            self._refresh.wait(pi_s.poll_interval_s)
            self._refresh.clear()

    def _video_rate(self, doc: dict, merged: dict) -> None:
        """Encoder frame rate between two collector samples (Pi clock on both ends)."""
        try:
            sec = doc["sections"]["video"]
            prog = sec["data"]["progress"]
            frame = prog["parsed"]["frame"]
            epoch = float(doc["generated_at_epoch"])
            profile = sec["data"]["active_profile"]
        except (KeyError, TypeError, ValueError):
            self._last_video = None
            return
        prev = self._last_video
        self._last_video = (profile, frame, epoch)
        if prev and prev[0] == profile and isinstance(frame, int) and isinstance(prev[1], int) and epoch > prev[2]:
            prog["frame_rate_between_polls"] = round((frame - prev[1]) / (epoch - prev[2]), 2)

    def _track_boot(self, doc: dict) -> None:
        try:
            boot_id = doc["sections"]["identity"]["data"]["boot_id"]
        except (KeyError, TypeError):
            return
        if not boot_id:
            return
        if self._boots and self._boots[-1] != boot_id:
            self.session.event("warning", "pi", "ה-Pi אותחל במהלך הסשן (boot_id השתנה)")
        if not self._boots or self._boots[-1] != boot_id:
            self._boots.append(boot_id)
            if self.on_boot_id:
                self.on_boot_id(boot_id)
        self._put("pi.boot_history", {"current_boot_id": boot_id, "previous_boot_id": self.previous_boot_id,
                                      "boot_ids_this_session": list(self._boots)})
