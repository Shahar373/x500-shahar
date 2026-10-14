"""Local settings (JSON in the user's config dir - never in the install dir).

Secrets are NOT stored here; see :mod:`x500fs.secrets`.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from . import APP_ID

log = logging.getLogger(__name__)


def config_dir() -> Path:
    override = os.environ.get("X500FS_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_ID


def default_reports_dir() -> Path:
    docs = Path.home() / "Documents"
    return (docs if docs.is_dir() else Path.home()) / APP_ID / "reports"


@dataclass
class ConnectionSettings:
    host: str = ""
    ssh_port: int = 22
    username: str = ""
    auth_method: str = "key"        # key | agent | password
    key_path: str = ""
    link_path: str = "wifi"         # wifi | lte | tailscale
    expected_hostname: str = "x500pi"
    connect_timeout_s: float = 10.0


@dataclass
class MavlinkSettings:
    transport: str = "ssh_tunnel"   # ssh_tunnel | tcp | udp_listen
    tunnel_host: str = "127.0.0.1"  # as seen from the Pi
    tunnel_port: int = 5760
    tcp_host: str = ""
    tcp_port: int = 5760
    udp_port: int = 14550
    source_sysid: int = 247
    source_compid: int = 25
    target_sysid: int = 0           # 0 = first autopilot heartbeat seen
    request_messages: bool = True
    request_interval_s: float = 2.0
    # On "run preflight" only, while verified disarmed: send MAV_CMD_RUN_PREARM_CHECKS
    # (read-only; ArduPilot reports failures as PreArm STATUSTEXT). Never an arm attempt.
    prearm_diagnostic: bool = True


# Requested encoder settings of the existing Pi pipeline (copied from
# /opt/x500/bin/x500-video-run in the 2026-10-14 source snapshot). These are what
# the Pi is ASKED to produce - not measured performance, and the SRT latency is a
# configured buffer, not an end-to-end delay.
SRT_PROFILES = {
    "high": {"width": 640, "height": 480, "fps": 60, "bitrate_kbps": 1200, "latency_us": 120000},
    "normal": {"width": 640, "height": 480, "fps": 30, "bitrate_kbps": 650, "latency_us": 180000},
    "degraded": {"width": 480, "height": 360, "fps": 20, "bitrate_kbps": 350, "latency_us": 300000},
}


@dataclass
class VideoSettings:
    backend: str = "srt"            # srt (existing Pi pipeline, default) | rtsp (MediaMTX alternative)
    # --- SRT: x500-video@<profile>.service on the Pi, listener on its Tailscale IPv4 ---
    srt_profile: str = "degraded"   # start with the lowest-load existing profile
    srt_profiles: dict = field(default_factory=lambda: {k: dict(v) for k, v in SRT_PROFILES.items()})
    srt_host: str = ""              # empty = the Pi's Tailscale IPv4 as reported by the collector
    srt_port: int = 5600
    srt_extra_params: str = ""      # appended to the SRT URL (advanced)
    manage_pi_service: bool = True  # explicit start/stop of x500-video@<profile> via `sudo -n systemctl`
    stop_pi_service_on_stop: bool = True
    unit_template: str = "x500-video@{profile}.service"
    progress_template: str = "/run/x500-video-{profile}.progress"
    pi_config_file: str = "/etc/x500/video.conf"
    service_start_wait_s: float = 4.0
    ffmpeg_path: str = ""           # empty = FFmpeg bundled with the app (imageio-ffmpeg)
    # --- RTSP / MediaMTX alternative (not the default) ---
    transport: str = "ssh_tunnel"   # ssh_tunnel | direct
    rtsp_port: int = 8554
    path: str = "x500cam"
    direct_url: str = ""
    rtsp_transport: str = "tcp"
    width: int = 640
    height: int = 480
    fps: int = 15
    bitrate_kbps: int = 1000
    apply_camera_settings: bool = True
    mediamtx_api: str = "http://127.0.0.1:9997"
    # --- common ---
    max_reconnects: int = 5
    stale_after_s: float = 2.0
    open_timeout_s: float = 12.0

    def profile(self) -> dict:
        p = self.srt_profiles.get(self.srt_profile) or SRT_PROFILES.get(self.srt_profile) or SRT_PROFILES["degraded"]
        return dict(p)


@dataclass
class PiSettings:
    # Unit names from the X500 source snapshot. The hardware watchdog is systemd's
    # own (RuntimeWatchdogSec), so there is no separate watchdog unit.
    services: dict = field(default_factory=lambda: {
        "mavlink_router": "mavlink-router",
        "blackbox": "x500-blackbox",
        "health_monitor": "x500-health-monitor",
        "net_monitor": "x500-net-monitor",
        "tailscale": "tailscaled",
        "mediamtx": "",
    })
    blackbox_globs: list = field(default_factory=lambda: ["/var/log/x500/blackbox/x500-blackbox-*.csv"])
    blackbox_sleep_s: float = 10.0          # sleep in x500-blackbox; real cadence = probe time + sleep
    watchdog_expected_runtime: str = "15s"  # from 60-x500-watchdog.conf; empty = any non-zero value
    collector_mode: str = "stream"          # stream (nothing installed) | installed
    installed_collector_path: str = "/usr/local/bin/x500fs-collector"
    python: str = "python3"                 # interpreter used to run the collector on the Pi
    legacy_python: str = ""                 # Pi venv with pymavlink (account-specific; for pi/x500 tools)
    poll_interval_s: float = 10.0           # fast cycle
    slow_poll_interval_s: float = 60.0      # pings, camera enumeration, Tailscale, USB
    collector_timeout_s: float = 45.0
    wifi_interface: str = "wlan0"
    lte_interface: str = "eth1"
    tailscale_interface: str = "tailscale0"
    zte_gateway: str = ""                   # modem gateway address (was hardcoded in the old scripts)
    ground_ip: str = ""                     # ground station address for the Pi-side reachability probe
    ping_target: str = "1.1.1.1"            # Internet probe target used by the old scripts
    lte_usb_vids: list = field(default_factory=lambda: ["19d2"])
    fc_byid_glob: str = "usb-Holybro_Pixhawk6C_*-if00"
    expected_fc_byid: str = ""              # exact /dev/serial/by-id name to verify (optional)
    camera_probe: bool = True
    # Existing scripts on the Pi, e.g. {"name": "x500-health", "command": "x500-health --json",
    # "timeout_s": 20, "enabled": false}. Disabled until you enable them.
    tools: list = field(default_factory=list)


@dataclass
class Settings:
    schema_version: int = 2
    drone_name: str = "X500"
    simulation: bool = False
    sim_scenario: str = "healthy"
    profile_id: str = "bench"
    reports_dir: str = ""
    report_hide_private: bool = True   # mask IPs, MACs, USB serials, home user, SSIDs in reports/diagnostics
    connection: ConnectionSettings = field(default_factory=ConnectionSettings)
    mavlink: MavlinkSettings = field(default_factory=MavlinkSettings)
    video: VideoSettings = field(default_factory=VideoSettings)
    pi: PiSettings = field(default_factory=PiSettings)
    last_boot_id: str = ""
    first_run_done: bool = False

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        return _fill(cls, d)

    def is_configured(self) -> bool:
        c = self.connection
        return bool(c.host and c.username)

    def reports_path(self) -> Path:
        return Path(self.reports_dir) if self.reports_dir else default_reports_dir()


def _fill(cls, d: Any):
    obj = cls()
    if not isinstance(d, dict):
        return obj
    for f in fields(cls):
        if f.name not in d:
            continue
        cur = getattr(obj, f.name)
        val = d[f.name]
        if is_dataclass(cur):
            setattr(obj, f.name, _fill(type(cur), val))
        elif isinstance(cur, bool):
            if isinstance(val, bool):
                setattr(obj, f.name, val)
        elif isinstance(cur, int) and not isinstance(cur, bool):
            if isinstance(val, int) and not isinstance(val, bool):
                setattr(obj, f.name, val)
        elif isinstance(cur, float):
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                setattr(obj, f.name, float(val))
        elif isinstance(cur, str):
            if isinstance(val, str):
                setattr(obj, f.name, val)
        elif isinstance(cur, dict):
            if isinstance(val, dict):
                merged = dict(cur)
                for k, v in val.items():
                    default = cur.get(str(k))
                    if default is None and isinstance(v, (str, dict)):
                        merged[str(k)] = v
                    elif isinstance(v, type(default)) and not isinstance(v, bool):
                        merged[str(k)] = v
                setattr(obj, f.name, merged)
        elif isinstance(cur, list):
            if isinstance(val, list):
                setattr(obj, f.name, val)
    return obj


class SettingsStore:
    def __init__(self, directory: Path | None = None):
        self.dir = Path(directory) if directory else config_dir()
        self.path = self.dir / "settings.json"
        self.load_error: str | None = None

    @property
    def profiles_dir(self) -> Path:
        return self.dir / "profiles"

    @property
    def known_hosts(self) -> Path:
        return self.dir / "known_hosts"

    @property
    def logs_dir(self) -> Path:
        return self.dir / "logs"

    def load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        try:
            return Settings.from_dict(json.loads(self.path.read_text("utf-8")))
        except (OSError, ValueError) as e:
            self.load_error = f"{self.path}: {e}"
            log.error("settings unreadable, using defaults: %s", e)
            backup = self.path.with_suffix(".broken.json")
            try:
                self.path.replace(backup)
            except OSError:
                pass
            return Settings()

    def save(self, s: Settings) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(s.to_dict(), ensure_ascii=False, indent=2) + "\n", "utf-8")
        os.replace(tmp, self.path)
