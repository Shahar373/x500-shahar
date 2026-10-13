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


@dataclass
class VideoSettings:
    transport: str = "ssh_tunnel"   # ssh_tunnel | direct
    rtsp_port: int = 8554
    path: str = "x500cam"
    direct_url: str = ""            # optional full override, e.g. rtsp://100.x.y.z:8554/x500cam
    rtsp_transport: str = "tcp"     # tcp | udp
    width: int = 640
    height: int = 480
    fps: int = 15
    bitrate_kbps: int = 1000
    apply_camera_settings: bool = True
    max_reconnects: int = 5
    stale_after_s: float = 2.0
    open_timeout_s: float = 8.0
    mediamtx_api: str = "http://127.0.0.1:9997"


@dataclass
class PiSettings:
    services: dict = field(default_factory=lambda: {
        "mavlink_router": "mavlink-router", "watchdog": "", "blackbox": "", "mediamtx": "",
    })
    blackbox_globs: list = field(default_factory=list)
    collector_mode: str = "stream"  # stream (nothing installed) | installed
    installed_collector_path: str = "/usr/local/bin/x500fs-collector"
    python: str = "python3"
    poll_interval_s: float = 10.0
    collector_timeout_s: float = 45.0
    ping_target: str = ""
    lte_usb_vids: list = field(default_factory=lambda: ["19d2"])
    lte_interface: str = ""
    camera_probe: bool = True
    # Existing scripts on the Pi, e.g. {"name": "x500-status", "command": "x500-status",
    # "timeout_s": 20, "enabled": false}. Disabled until you enable them.
    tools: list = field(default_factory=list)


@dataclass
class Settings:
    schema_version: int = 1
    drone_name: str = "X500"
    simulation: bool = False
    sim_scenario: str = "healthy"
    profile_id: str = "bench"
    reports_dir: str = ""
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
                merged.update({str(k): v for k, v in val.items() if isinstance(v, str)})
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
