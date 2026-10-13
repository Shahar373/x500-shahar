#!/usr/bin/env python3
"""x500fs collector - read-only observations of the Raspberry Pi as JSON.

* Standard library only (Python 3.7+).
* Never changes anything: no writes, no service control, no package installs,
  no sudo. Every external command has a timeout.
* Reports raw observations; pass/fail policy is evaluated on the laptop.

Usage (normally streamed by the desktop app over SSH, nothing is installed):
    python3 - --config-b64 <base64-json>  < x500fs_collector.py
or, if installed explicitly:
    x500fs-collector --pretty
"""

from __future__ import annotations

import argparse
import base64
import getpass
import glob
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

SCHEMA = "x500fs.collector/1"
VERSION = "0.1.0"
ROOT = "/"
ALLOW_COMMANDS = True

DEFAULT_CONFIG = {
    "services": {"mavlink_router": "mavlink-router"},
    "blackbox_globs": [],
    "lte_usb_vids": ["19d2"],
    "lte_interface": "",
    "ping_target": "",
    "tools": [],
    "mediamtx": {"api": "http://127.0.0.1:9997", "path": "x500cam"},
    "camera_probe": True,
    "mavlink_router_conf": ["/etc/mavlink-router/main.conf", "/etc/mavlink-router/config.d/*.conf"],
}

DISCOVER_RE = re.compile(r"x500|mavlink|watchdog|black|box|logger|camera|mediamtx|tailscale|modem|rpicam", re.I)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def rp(path):
    """Resolve an absolute path under ROOT (ROOT is only changed by tests)."""
    if ROOT == "/":
        return path
    norm, root = os.path.normpath(path), os.path.normpath(ROOT)
    if norm == root or norm.startswith(root + os.sep):
        return path  # already resolved (e.g. a glob result)
    return os.path.join(ROOT, path.lstrip("/"))


def read_text(path, limit=65536):
    try:
        with open(rp(path), "r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return None


def run(cmd, timeout=5.0):
    """Run a command without a shell. Returns (exit_code, stdout, stderr)."""
    if not ALLOW_COMMANDS:
        return None, "", "commands disabled"
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout, universal_newlines=True, errors="replace")
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return None, "", "not found: %s" % cmd[0]
    except subprocess.TimeoutExpired:
        return None, "", "timeout after %ss" % timeout
    except OSError as e:
        return None, "", str(e)


def which(name):
    if not ALLOW_COMMANDS:
        return None
    return shutil.which(name)


# --------------------------------------------------------------------------
# parsers (pure; unit-tested on the laptop)
# --------------------------------------------------------------------------

def parse_os_release(text):
    out = {}
    for line in (text or "").splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"')
    keep = ("PRETTY_NAME", "NAME", "VERSION_ID", "VERSION_CODENAME", "ID", "ID_LIKE")
    return {k: out[k] for k in keep if k in out}


def parse_meminfo(text):
    vals = {}
    for line in (text or "").splitlines():
        m = re.match(r"(\w+):\s+(\d+)", line)
        if m:
            vals[m.group(1)] = int(m.group(2))
    total = vals.get("MemTotal")
    avail = vals.get("MemAvailable")
    return {
        "total_kb": total,
        "available_kb": avail,
        "available_pct": round(100.0 * avail / total, 1) if total and avail is not None else None,
        "swap_total_kb": vals.get("SwapTotal"),
        "swap_free_kb": vals.get("SwapFree"),
    }


def parse_proc_stat_cpu(text):
    for line in (text or "").splitlines():
        if line.startswith("cpu "):
            nums = [int(x) for x in line.split()[1:]]
            idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
            return sum(nums), idle
    return None


def parse_throttled(text):
    m = re.search(r"throttled=(0x[0-9a-fA-F]+)", text or "")
    return int(m.group(1), 16) if m else None


def parse_pmic_ext5v(text):
    m = re.search(r"EXT5V_V\s+volt\(\d+\)=([0-9.]+)V", text or "")
    return float(m.group(1)) if m else None


def parse_systemctl_show(text):
    out = {}
    for line in (text or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def parse_list_cameras(text):
    cams = []
    for line in (text or "").splitlines():
        m = re.match(r"^\s*(\d+)\s*:\s*(\S+)\s*\[([^\]]*)\]\s*(?:\((.*)\))?", line)
        if m:
            cams.append({"index": int(m.group(1)), "sensor": m.group(2), "desc": m.group(3),
                         "path": m.group(4) or ""})
    return cams


def parse_ini_sections(text):
    """mavlink-router style config: [Type name] sections with key = value."""
    sections = []
    cur = None
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"^\[(\w+)(?:\s+([^\]]+))?\]$", line)
        if m:
            cur = {"type": m.group(1), "name": (m.group(2) or "").strip(), "keys": {}}
            sections.append(cur)
            continue
        if cur is not None and "=" in line:
            k, v = line.split("=", 1)
            cur["keys"][k.strip()] = v.strip()
    return sections


def parse_ss_listening(text):
    out = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        proto = parts[0]
        local = parts[4]
        m = re.match(r"^(.*):(\d+)$", local)
        if not m:
            continue
        out.append({"proto": proto, "addr": m.group(1).strip("[]"), "port": int(m.group(2))})
    return out


def parse_ping(text):
    loss = None
    m = re.search(r"([\d.]+)% packet loss", text or "")
    if m:
        loss = float(m.group(1))
    rtt = None
    m = re.search(r"=\s*[\d.]+/([\d.]+)/[\d.]+", text or "")
    if m:
        rtt = float(m.group(1))
    return {"loss_pct": loss, "rtt_ms": rtt}


def parse_iw_link(text):
    out = {}
    if not text or "Not connected" in text:
        return {"connected": False}
    out["connected"] = True
    m = re.search(r"SSID:\s*(.+)", text)
    if m:
        out["ssid"] = m.group(1).strip()
    m = re.search(r"signal:\s*(-?\d+)\s*dBm", text)
    if m:
        out["signal_dbm"] = int(m.group(1))
    m = re.search(r"tx bitrate:\s*([\d.]+\s*\S+)", text)
    if m:
        out["tx_bitrate"] = m.group(1)
    m = re.search(r"freq:\s*([\d.]+)", text)
    if m:
        out["freq_mhz"] = float(m.group(1))
    return out


def ip_in_cgnat(ip):
    try:
        a, b = [int(x) for x in ip.split(".")[:2]]
        return a == 100 and 64 <= b <= 127
    except (ValueError, AttributeError):
        return False


# --------------------------------------------------------------------------
# sections
# --------------------------------------------------------------------------

def sec_identity(cfg):
    model = read_text("/proc/device-tree/model")
    ssh = os.environ.get("SSH_CONNECTION", "").split()
    return {
        "hostname": socket.gethostname(),
        "os_release": parse_os_release(read_text("/etc/os-release")),
        "debian_version": (read_text("/etc/debian_version") or "").strip() or None,
        "rpi_issue": ((read_text("/etc/rpi-issue") or "").splitlines() or [None])[0],
        "kernel": platform.release(),
        "machine": platform.machine(),
        "model": model.strip("\x00").strip() if model else None,
        "python": platform.python_version(),
        "boot_id": (read_text("/proc/sys/kernel/random/boot_id") or "").strip() or None,
        "user": getpass.getuser(),
        "ssh_connection": {"client_ip": ssh[0], "client_port": ssh[1], "server_ip": ssh[2],
                           "server_port": ssh[3]} if len(ssh) == 4 else None,
    }


def sec_uptime(cfg):
    text = read_text("/proc/uptime")
    if not text:
        raise RuntimeError("/proc/uptime unreadable")
    up = float(text.split()[0])
    return {"uptime_s": up, "boot_time_epoch": round(time.time() - up, 1)}


def sec_thermal(cfg):
    zones = []
    for d in sorted(glob.glob(rp("/sys/class/thermal/thermal_zone*"))):
        try:
            with open(os.path.join(d, "type")) as f:
                ztype = f.read().strip()
            with open(os.path.join(d, "temp")) as f:
                temp = int(f.read().strip()) / 1000.0
            zones.append({"zone": os.path.basename(d), "type": ztype, "temp_c": temp})
        except (OSError, ValueError):
            continue
    cpu = next((z["temp_c"] for z in zones if "cpu" in z["type"].lower()), None)
    if cpu is None and zones:
        cpu = zones[0]["temp_c"]
    return {"zones": zones, "cpu_temp_c": cpu}


PROC_RE = re.compile(r"^(mediamtx|mtxrpicam.*|rpicam-.*|libcamera.*|ffmpeg|mavlink-route.*)$")


def _proc_ticks():
    """utime+stime ticks of camera/telemetry processes, keyed by pid."""
    out = {}
    for d in glob.glob(rp("/proc/[0-9]*")):
        comm = (read_text(os.path.join(d, "comm"), 64) or "").strip()
        if not PROC_RE.match(comm):
            continue
        stat = read_text(os.path.join(d, "stat"), 4096) or ""
        try:
            fields = stat.rsplit(")", 1)[1].split()
            out[os.path.basename(d)] = (comm, int(fields[11]) + int(fields[12]))
        except (IndexError, ValueError):
            continue
    return out


def sec_cpu(cfg):
    a = parse_proc_stat_cpu(read_text("/proc/stat"))
    pa = _proc_ticks()
    t0 = time.time()
    time.sleep(0.5)
    b = parse_proc_stat_cpu(read_text("/proc/stat"))
    pb = _proc_ticks()
    dt = time.time() - t0
    usage = None
    if a and b and b[0] > a[0]:
        usage = round(100.0 * (1.0 - (b[1] - a[1]) / float(b[0] - a[0])), 1)
    try:
        hz = os.sysconf("SC_CLK_TCK")
    except (ValueError, OSError, AttributeError):
        hz = 100
    procs = []
    for pid, (comm, ticks) in pb.items():
        if pid in pa and dt > 0:
            procs.append({"pid": int(pid), "name": comm,
                          "cpu_pct_of_one_core": round(100.0 * (ticks - pa[pid][1]) / hz / dt, 1)})
    load = (read_text("/proc/loadavg") or "").split()[:3]
    freq = read_text("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
    return {
        "usage_pct": usage,
        "sample_s": 0.5,
        "processes": sorted(procs, key=lambda p: -p["cpu_pct_of_one_core"]),
        "loadavg": [float(x) for x in load] if load else None,
        "cores": os.cpu_count(),
        "freq_mhz": int(freq) / 1000.0 if freq and freq.strip().isdigit() else None,
    }


def sec_memory(cfg):
    return parse_meminfo(read_text("/proc/meminfo"))


def _mount_flags():
    flags = {}
    for line in (read_text("/proc/mounts") or "").splitlines():
        parts = line.split()
        if len(parts) >= 4:
            flags[parts[1]] = "ro" in parts[3].split(",")
    return flags


def sec_storage(cfg):
    paths = ["/", "/boot/firmware"]
    for g in cfg.get("blackbox_globs") or []:
        base = g.split("*", 1)[0]
        base = base if os.path.isdir(rp(base)) else os.path.dirname(base)
        if base and base not in paths:
            paths.append(base)
    ro = _mount_flags()
    mounts, seen = [], set()
    for path in paths:
        if not os.path.exists(rp(path)):
            continue
        st = os.statvfs(rp(path))
        key = (st.f_fsid if hasattr(st, "f_fsid") else path, st.f_blocks)
        if key in seen:
            continue
        seen.add(key)
        mounts.append({
            "path": path,
            "total_mb": round(st.f_blocks * st.f_frsize / 1048576.0, 1),
            "free_mb": round(st.f_bavail * st.f_frsize / 1048576.0, 1),
            "read_only": ro.get(path),
        })
    return {"mounts": mounts}


def sec_power(cfg):
    out = {"vcgencmd": None, "throttled": None, "throttled_raw": None, "ext5v_v": None,
           "hwmon_undervoltage_alarm": None}
    vc = which("vcgencmd")
    if vc:
        out["vcgencmd"] = vc
        rc, so, se = run([vc, "get_throttled"], 3)
        out["throttled_raw"] = so.strip() or None
        out["throttled"] = parse_throttled(so)
        if out["throttled"] is None:
            out["vcgencmd_error"] = (se or so).strip()[:200]
        # Documented form lists every PMIC ADC channel (Pi 5); we pick EXT5V_V.
        rc, so, se = run([vc, "pmic_read_adc"], 3)
        out["ext5v_v"] = parse_pmic_ext5v(so)
    for d in glob.glob(rp("/sys/class/hwmon/hwmon*")):
        name = (read_text(os.path.join(d, "name")) or "").strip()
        if name == "rpi_volt":
            alarm = read_text(os.path.join(d, "in0_lcrit_alarm"))
            if alarm is not None and alarm.strip().isdigit():
                out["hwmon_undervoltage_alarm"] = int(alarm.strip())
    return out


SHOW_PROPS = "LoadState,ActiveState,SubState,NRestarts,ActiveEnterTimestamp,ExecMainStartTimestamp,UnitFileState,Result,MainPID"


def sec_services(cfg):
    units = {}
    for role, unit in (cfg.get("services") or {}).items():
        if not unit:
            continue
        rc, so, se = run(["systemctl", "show", unit, "--property=" + SHOW_PROPS, "--no-pager"], 4)
        if rc is None or (rc != 0 and not so.strip()):
            units[unit] = {"error": (se.strip() or "exit %s" % rc)[:200], "role": role}
        else:
            units[unit] = parse_systemctl_show(so)
            units[unit]["role"] = role
    discovered = set()
    for cmd in (["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--no-pager", "--plain"],
                ["systemctl", "list-unit-files", "--type=service", "--no-legend", "--no-pager"]):
        rc, so, se = run(cmd, 4)
        for line in so.splitlines():
            parts = line.split()
            if parts and parts[0].endswith(".service") and DISCOVER_RE.search(parts[0]):
                discovered.add(parts[0])
    return {"units": units, "discovered": sorted(discovered)}


def sec_watchdog(cfg):
    out = {"dev_watchdog_exists": os.path.exists(rp("/dev/watchdog")),
           "devices": []}
    for d in sorted(glob.glob(rp("/sys/class/watchdog/watchdog*"))):
        info = {"name": os.path.basename(d)}
        for k in ("identity", "timeout", "state", "status"):
            v = read_text(os.path.join(d, k))
            if v is not None:
                info[k] = v.strip()
        out["devices"].append(info)
    rc, so, se = run(["systemctl", "show", "--property=RuntimeWatchdogUSec,RebootWatchdogUSec"], 3)
    out["systemd"] = parse_systemctl_show(so) if rc == 0 else None
    return out


def sec_blackbox(cfg):
    globs = cfg.get("blackbox_globs") or []
    files = []
    for g in globs:
        for path in glob.glob(rp(g), recursive=True):
            try:
                st = os.stat(path)
            except OSError:
                continue
            if os.path.isfile(path):
                files.append((st.st_mtime, st.st_size, path))
    now = time.time()
    newest = max(files) if files else None
    return {
        "globs": globs,
        "file_count": len(files),
        "total_mb": round(sum(f[1] for f in files) / 1048576.0, 2),
        "newest": {"path": newest[2] if ROOT == "/" else "/" + os.path.relpath(newest[2], ROOT),
                   "size": newest[1], "mtime_age_s": round(now - newest[0], 1)} if newest else None,
    }


def sec_mavlink_router(cfg):
    files = []
    for pattern in cfg.get("mavlink_router_conf") or []:
        files.extend(sorted(glob.glob(rp(pattern))))
    endpoints, general, used = [], {}, []
    for path in files:
        text = read_text(path)
        if text is None:
            continue
        used.append(path)
        for s in parse_ini_sections(text):
            if s["type"].lower() == "general":
                general.update(s["keys"])
            else:
                endpoints.append({"file": os.path.basename(path), "type": s["type"], "name": s["name"], **s["keys"]})
    ports = {5760, 8554, 9997, 22}
    for k in ("TcpServerPort",):
        if general.get(k, "").isdigit():
            ports.add(int(general[k]))
    for ep in endpoints:
        if str(ep.get("Port", "")).isdigit():
            ports.add(int(ep["Port"]))
    rc, so, se = run(["ss", "-H", "-l", "-n", "-t", "-u"], 3)
    listening = [x for x in parse_ss_listening(so) if x["port"] in ports] if rc == 0 else None
    return {"config_files": used, "general": general, "endpoints": endpoints, "listening": listening,
            "ss_error": None if rc == 0 else se.strip()[:200]}


def sec_camera(cfg):
    if not cfg.get("camera_probe", True):
        return {"tool": None, "cameras": None, "note": "camera probe disabled"}
    tool = which("rpicam-hello") or which("libcamera-hello")
    if not tool:
        return {"tool": None, "cameras": None}
    rc, so, se = run([tool, "--list-cameras"], 12)
    text = so + "\n" + se
    cams = parse_list_cameras(text)
    note = None
    if not cams:
        if "No cameras available" in text:
            note = "No cameras available"
        elif rc is None:
            note = se.strip()[:200]
    return {"tool": os.path.basename(tool), "cameras": cams, "exit_code": rc, "note": note}


def _http_json(url, timeout=2.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 (localhost only)
        return json.loads(r.read(262144).decode("utf-8"))


def sec_mediamtx(cfg):
    m = cfg.get("mediamtx") or {}
    api = (m.get("api") or "").rstrip("/")
    path = m.get("path") or "x500cam"
    out = {"binary": which("mediamtx") or (rp("/usr/local/bin/mediamtx") if os.path.exists(rp("/usr/local/bin/mediamtx")) else None),
           "api": api, "path_name": path, "api_reachable": False}
    if not api.startswith("http://127.0.0.1") and not api.startswith("http://localhost"):
        out["error"] = "API URL must be on localhost"
        return out
    if not ALLOW_COMMANDS:
        return out
    try:
        st = _http_json("%s/v3/paths/get/%s" % (api, path))
        out["api_reachable"] = True
        out["path"] = {
            "ready": st.get("ready"),
            "ready_time": st.get("readyTime"),
            "source_type": (st.get("source") or {}).get("type"),
            "tracks": st.get("tracks"),
            "readers": len(st.get("readers") or []),
            "bytes_received": st.get("bytesReceived"),
        }
    except Exception as e:  # noqa: BLE001
        out["path_error"] = str(e)[:200]
    try:
        conf = _http_json("%s/v3/config/paths/get/%s" % (api, path))
        out["api_reachable"] = True
        keep = ("source", "sourceOnDemand", "rpiCameraWidth", "rpiCameraHeight", "rpiCameraFPS",
                "rpiCameraBitrate", "rpiCameraCodec", "rpiCameraIDRPeriod", "runOnDemand")
        out["path_config"] = {k: conf.get(k) for k in keep if k in conf}
    except Exception as e:  # noqa: BLE001
        out["config_error"] = str(e)[:200]
    return out


def _iface_kind(name, cfg):
    base = rp("/sys/class/net/%s" % name)
    if name == "lo":
        return "loopback", None, None
    if name.startswith("tailscale"):
        return "tailscale", None, None
    dev = os.path.realpath(os.path.join(base, "device")) if os.path.exists(os.path.join(base, "device")) else ""
    driver = None
    drv_link = os.path.join(base, "device", "driver")
    if os.path.exists(drv_link):
        driver = os.path.basename(os.path.realpath(drv_link))
    usb = None
    if "/usb" in dev:
        d = dev
        while d and d != "/" and not os.path.exists(os.path.join(d, "idVendor")):
            d = os.path.dirname(d)
        if d and os.path.exists(os.path.join(d, "idVendor")):
            usb = {
                "vid": (read_text(os.path.join(d, "idVendor")) or "").strip(),
                "pid": (read_text(os.path.join(d, "idProduct")) or "").strip(),
                "product": (read_text(os.path.join(d, "product")) or "").strip() or None,
            }
    if os.path.exists(os.path.join(base, "wireless")) or os.path.exists(os.path.join(base, "phy80211")):
        return "wifi", driver, usb
    lte_if = cfg.get("lte_interface")
    lte_vids = [v.lower() for v in cfg.get("lte_usb_vids") or []]
    if (lte_if and name == lte_if) or name.startswith("wwan") or driver in ("qmi_wwan", "cdc_mbim") \
            or (usb and usb.get("vid", "").lower() in lte_vids):
        return "lte", driver, usb
    if usb:
        return "usb_net", driver, usb
    if name.startswith(("eth", "end", "enx")):
        return "ethernet", driver, usb
    return "other", driver, usb


def _ip_json(args):
    rc, so, se = run(["ip", "-j"] + args, 3)
    if rc != 0:
        raise RuntimeError("ip %s failed: %s" % (" ".join(args), se.strip()[:200]))
    return json.loads(so or "[]")


_CACHE = {}


def sec_network(cfg):
    if "network" in _CACHE:
        return _CACHE["network"]
    _CACHE["network"] = result = _sec_network(cfg)
    return result


def _sec_network(cfg):
    addrs = _ip_json(["addr"])
    interfaces = []
    by_ip = {}
    for a in addrs:
        name = a.get("ifname")
        kind, driver, usb = _iface_kind(name, cfg)
        v4 = ["%s/%s" % (x["local"], x.get("prefixlen")) for x in a.get("addr_info", []) if x.get("family") == "inet"]
        v6 = ["%s/%s" % (x["local"], x.get("prefixlen")) for x in a.get("addr_info", [])
              if x.get("family") == "inet6" and x.get("scope") == "global"]
        for x in a.get("addr_info", []):
            by_ip[x.get("local")] = name
        interfaces.append({"name": name, "operstate": (a.get("operstate") or "").lower(),
                           "flags": a.get("flags"), "kind": kind, "driver": driver, "usb": usb,
                           "ipv4": v4, "ipv6": v6})
    kinds = {i["name"]: i["kind"] for i in interfaces}
    routes = []
    for r in _ip_json(["route", "show", "default"]):
        routes.append({"dev": r.get("dev"), "gateway": r.get("gateway"), "metric": r.get("metric", 0),
                       "kind": kinds.get(r.get("dev"))})
    routes.sort(key=lambda r: r.get("metric") or 0)

    arrival = None
    ssh = os.environ.get("SSH_CONNECTION", "").split()
    if len(ssh) == 4:
        server_ip = ssh[2]
        if server_ip.startswith("::ffff:"):
            server_ip = server_ip[7:]
        dev = by_ip.get(server_ip)
        arrival = {"server_ip": server_ip, "client_ip": ssh[0], "dev": dev, "kind": kinds.get(dev)}

    wifi = {}
    for i in interfaces:
        if i["kind"] == "wifi":
            rc, so, se = run(["iw", "dev", i["name"], "link"], 3)
            wifi[i["name"]] = parse_iw_link(so) if rc == 0 else {"error": (se or "iw unavailable").strip()[:120]}

    ts = {"available": False}
    tsbin = which("tailscale")
    if tsbin:
        rc, so, se = run([tsbin, "status", "--json"], 4)
        if rc == 0:
            try:
                st = json.loads(so)
                me = st.get("Self") or {}
                ts = {"available": True, "backend_state": st.get("BackendState"),
                      "self_online": me.get("Online"), "self_ips": me.get("TailscaleIPs")}
                client_ip = arrival["client_ip"] if arrival else None
                for peer in (st.get("Peer") or {}).values():
                    if client_ip and client_ip in (peer.get("TailscaleIPs") or []):
                        cur = peer.get("CurAddr") or ""
                        ts["peer"] = {"online": peer.get("Online"), "cur_addr": cur or None,
                                      "relay": peer.get("Relay") or None, "direct": bool(cur)}
                        underlay = None
                        if cur:
                            host = cur.rsplit(":", 1)[0].strip("[]")
                            try:
                                rg = _ip_json(["route", "get", host])
                                underlay = rg[0].get("dev") if rg else None
                            except Exception:  # noqa: BLE001
                                underlay = None
                        elif routes:
                            underlay = routes[0]["dev"]  # DERP relay goes via the default route
                        ts["underlay_dev"] = underlay
                        ts["underlay_kind"] = kinds.get(underlay)
                        break
            except ValueError as e:
                ts = {"available": True, "error": "bad json: %s" % e}
        else:
            ts = {"available": True, "error": (se or so).strip()[:200]}
    return {"interfaces": interfaces, "default_routes": routes, "ssh_arrival": arrival,
            "wifi": wifi, "tailscale": ts}


def _usb_devices():
    devs = []
    for d in sorted(glob.glob(rp("/sys/bus/usb/devices/*"))):
        vid = read_text(os.path.join(d, "idVendor"))
        if not vid:
            continue
        vid = vid.strip()
        if vid == "1d6b":  # Linux Foundation root hubs
            continue
        devs.append({
            "bus_path": os.path.basename(d),
            "vid": vid,
            "pid": (read_text(os.path.join(d, "idProduct")) or "").strip(),
            "manufacturer": (read_text(os.path.join(d, "manufacturer")) or "").strip() or None,
            "product": (read_text(os.path.join(d, "product")) or "").strip() or None,
        })
    return devs


def sec_usb(cfg):
    return {"devices": _usb_devices()}


def sec_lte(cfg):
    vids = [v.lower() for v in cfg.get("lte_usb_vids") or []]
    modems = [d for d in _usb_devices() if d["vid"].lower() in vids]
    net = sec_network(cfg)
    ifaces = [i for i in net["interfaces"] if i["kind"] == "lte"]
    routes = net["default_routes"]
    out = {"usb_modems": modems, "interfaces": ifaces,
           "default_route_via_lte": bool(routes and routes[0].get("kind") == "lte") if routes else False,
           "mmcli": None}
    if which("mmcli"):
        rc, so, se = run(["mmcli", "-L"], 4)
        out["mmcli"] = {"exit_code": rc, "output": (so or se).strip()[:500]}
    target = cfg.get("ping_target")
    if target and ifaces:
        dev = ifaces[0]["name"]
        rc, so, se = run(["ping", "-n", "-c", "3", "-W", "2", "-I", dev, target], 10)
        res = parse_ping(so)
        out["ping"] = {"target": target, "iface": dev, "ok": rc == 0, **res}
    return out


def sec_internet(cfg):
    target = cfg.get("ping_target")
    if not target:
        return {"target": None}
    rc, so, se = run(["ping", "-n", "-c", "3", "-W", "2", target], 10)
    return {"target": target, "ok": rc == 0, **parse_ping(so)}


def sec_tools(cfg):
    out = {}
    for t in cfg.get("tools") or []:
        name = t.get("name") or "tool"
        argv = t.get("argv") or []
        if not argv or not isinstance(argv, list):
            out[name] = {"error": "no command configured"}
            continue
        t0 = time.time()
        rc, so, se = run([str(a) for a in argv], float(t.get("timeout_s") or 20))
        res = {"argv": argv, "exit_code": rc, "duration_ms": int((time.time() - t0) * 1000),
               "stdout_tail": so[-4000:], "stderr_tail": se[-2000:]}
        if rc is None:
            res["error"] = se.strip()[:200]
        try:
            res["json"] = json.loads(so) if so.strip().startswith(("{", "[")) else None
        except ValueError:
            res["json"] = None
        out[name] = res
    return out


SECTIONS = [
    ("identity", sec_identity), ("uptime", sec_uptime), ("thermal", sec_thermal),
    ("cpu", sec_cpu), ("memory", sec_memory), ("storage", sec_storage), ("power", sec_power),
    ("services", sec_services), ("watchdog", sec_watchdog), ("blackbox", sec_blackbox),
    ("mavlink_router", sec_mavlink_router), ("camera", sec_camera), ("mediamtx", sec_mediamtx),
    ("network", sec_network), ("usb", sec_usb), ("lte", sec_lte), ("internet", sec_internet),
    ("tools", sec_tools),
]


def collect(cfg, only=None):
    _CACHE.clear()
    t0 = time.time()
    sections = {}
    for name, fn in SECTIONS:
        if only and name not in only:
            continue
        s0 = time.time()
        try:
            sections[name] = {"ok": True, "data": fn(cfg), "error": None}
        except Exception as e:  # noqa: BLE001 - every failure is reported, never raised
            sections[name] = {"ok": False, "data": None, "error": "%s: %s" % (type(e).__name__, e)}
        sections[name]["duration_ms"] = int((time.time() - s0) * 1000)
    return {
        "schema": SCHEMA,
        "collector_version": VERSION,
        "generated_at_epoch": round(time.time(), 3),
        "duration_ms": int((time.time() - t0) * 1000),
        "sections": sections,
    }


def main(argv=None):
    global ROOT, ALLOW_COMMANDS
    ap = argparse.ArgumentParser(description="x500fs read-only collector")
    ap.add_argument("--config-b64", default="")
    ap.add_argument("--config-file", default="")
    ap.add_argument("--only", default="", help="comma separated section names")
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--root", default="/", help=argparse.SUPPRESS)
    ap.add_argument("--no-commands", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    ROOT = args.root
    ALLOW_COMMANDS = not args.no_commands
    cfg = dict(DEFAULT_CONFIG)
    if args.config_file:
        with open(args.config_file, encoding="utf-8") as f:
            cfg.update(json.load(f))
    if args.config_b64:
        cfg.update(json.loads(base64.b64decode(args.config_b64).decode("utf-8")))
    only = [s for s in args.only.split(",") if s] or None
    doc = collect(cfg, only)
    json.dump(doc, sys.stdout, indent=2 if args.pretty else None, sort_keys=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
