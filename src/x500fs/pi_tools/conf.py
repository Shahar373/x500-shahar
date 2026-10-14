"""Read /etc/x500/x500.conf (shell-style KEY=value) without executing it."""

from __future__ import annotations

import os
import re
import shlex

DEFAULT_PATH = os.environ.get("X500_CONF", "/etc/x500/x500.conf")
MARKER_RE = re.compile(r"NOT_CONFIGURED|PI_USER|DEVICE_SERIAL_NOT_CONFIGURED")

DEFAULTS = {
    "GROUND_IP": "",
    "ZTE_GATEWAY": "",
    "WIFI_IF": "wlan0",
    "LTE_IF": "eth1",
    "TAILSCALE_IF": "tailscale0",
    "PING_TARGET": "1.1.1.1",
    "X500_VENV_PYTHON": "",
    "MAVLINK_ENDPOINT": "tcp:127.0.0.1:5760",
    "MAVLINK_SYSID": "246",
    "MAVLINK_COMPID": "26",
    "TARGET_SYSID": "0",
    "FC_BYID_GLOB": "usb-Holybro_Pixhawk6C_*-if00",
    "EXPECTED_FC_BYID": "",
    "BLACKBOX_DIR": "/var/log/x500/blackbox",
    "BLACKBOX_INTERVAL": "10",
    "BLACKBOX_RETENTION_DAYS": "",
    "WATCHDOG_EXPECTED": "15s",
    "LINK_PATH": "wifi",
    "EXPECTED_HOSTNAME": "x500pi",
}


def parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if not m:
            continue
        try:
            parts = shlex.split(m.group(2), comments=True)
        except ValueError:
            continue
        out[m.group(1)] = parts[0] if parts else ""
    return out


def load(path: str = DEFAULT_PATH) -> dict[str, str]:
    conf = dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as f:
            conf.update(parse(f.read()))
    except OSError:
        pass
    # Reference markers from the sanitized snapshot are never real values.
    for k, v in list(conf.items()):
        if MARKER_RE.search(v or ""):
            conf[k] = ""
    return conf


def markers_in(text: str) -> list[str]:
    return sorted({k for k, v in parse(text).items() if MARKER_RE.search(v or "")})


def collector_config(conf: dict[str, str]) -> dict:
    """Collector settings equivalent to what the desktop app sends."""
    return {
        "wifi_interface": conf["WIFI_IF"],
        "lte_interface": conf["LTE_IF"],
        "tailscale_interface": conf["TAILSCALE_IF"],
        "zte_gateway": conf["ZTE_GATEWAY"],
        "ground_ip": conf["GROUND_IP"],
        "ping_target": conf["PING_TARGET"],
        "fc_byid_glob": conf["FC_BYID_GLOB"],
        "blackbox_globs": [conf["BLACKBOX_DIR"].rstrip("/") + "/x500-blackbox-*.csv"],
    }
