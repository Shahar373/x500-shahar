#!/usr/bin/env python3
"""x500fs diagnostics - explicit, read-only, stdout only (standard library, Python 3.7+).

Replaces the automatic use of the legacy ``x500-diag`` (which writes files under
/var/log/x500/diag). Nothing is written on the Pi: every command is read-only,
bounded by a timeout, and the result is printed as one JSON document. The
desktop app redacts private details before saving the bundle on the laptop.

    python3 - [--journal-lines N] < x500fs_diag.py
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time

UNITS = ["mavlink-router", "x500-blackbox", "x500-health-monitor", "x500-net-monitor", "tailscaled",
         "x500-video@high", "x500-video@normal", "x500-video@degraded"]


def run(cmd, timeout=8):
    exe = shutil.which(cmd[0])
    if not exe:
        return {"cmd": " ".join(cmd), "error": "not found: %s" % cmd[0]}
    try:
        p = subprocess.run([exe] + cmd[1:], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout)
        return {"cmd": " ".join(cmd), "rc": p.returncode,
                "stdout": p.stdout.decode("utf-8", "replace")[-20000:],
                "stderr": p.stderr.decode("utf-8", "replace")[-2000:]}
    except subprocess.TimeoutExpired:
        return {"cmd": " ".join(cmd), "error": "timeout after %ss" % timeout}
    except OSError as e:
        return {"cmd": " ".join(cmd), "error": str(e)}


def read(path, limit=20000):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError as e:
        return "unreadable: %s" % e


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal-lines", type=int, default=40)
    args = ap.parse_args(argv)
    t0 = time.time()
    out = {"schema": "x500fs.pi_diag/1", "generated_at_epoch": round(t0, 3), "python": sys.version.split()[0],
           "platform": platform.platform(), "read_only": True, "items": {}}
    items = out["items"]
    items["os_release"] = read("/etc/os-release")
    items["uname"] = run(["uname", "-a"])
    items["uptime"] = read("/proc/uptime")
    items["model"] = read("/proc/device-tree/model").strip("\x00")
    items["failed_units"] = run(["systemctl", "--failed", "--no-legend", "--no-pager"])
    items["units"] = {u: run(["systemctl", "show", u, "--no-pager", "--property=LoadState,ActiveState,SubState,"
                              "NRestarts,Result,ExecMainStatus,ActiveEnterTimestamp,UnitFileState"]) for u in UNITS}
    if args.journal_lines > 0:
        n = str(min(max(args.journal_lines, 1), 500))
        items["journal"] = {u: run(["journalctl", "-u", u, "-n", n, "--no-pager", "-o", "short-iso"], 10)
                            for u in UNITS[:5]}
        items["kernel_usb_power"] = run(["journalctl", "-k", "-n", "300", "--no-pager", "-o", "short-iso",
                                         "--grep", "usb|ttyACM|cdc_acm|voltage|throttl|watchdog"], 10)
    items["ip_addr"] = run(["ip", "-br", "addr"])
    items["ip_route"] = run(["ip", "route"])
    items["tailscale_ip"] = run(["tailscale", "ip", "-4"])
    items["usb"] = run(["lsusb"])
    try:
        items["serial_by_id"] = sorted(os.listdir("/dev/serial/by-id"))
    except OSError as e:
        items["serial_by_id"] = "unavailable: %s" % e
    items["cameras"] = run(["rpicam-hello", "--list-cameras"], 15)
    items["vcgencmd"] = {k: run(["vcgencmd"] + k.split()) for k in ("measure_temp", "get_throttled", "pmic_read_adc")}
    items["mavlink_router_conf"] = read("/etc/mavlink-router/main.conf")
    items["x500_conf"] = read("/etc/x500/x500.conf")
    items["video_conf"] = read("/etc/x500/video.conf")
    items["watchdog_conf"] = read("/etc/systemd/system.conf.d/60-x500-watchdog.conf")
    items["disk"] = run(["df", "-hP"])
    out["duration_s"] = round(time.time() - t0, 2)
    json.dump(out, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
