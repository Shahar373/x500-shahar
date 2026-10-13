"""Pi collector parsers and a run against a fake filesystem root."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from x500fs.pi_side import x500fs_collector as col

COLLECTOR = Path(col.__file__)


def test_parsers():
    assert col.parse_throttled("throttled=0x50005\n") == 0x50005
    assert col.parse_throttled("error") is None
    assert col.parse_pmic_ext5v("     EXT5V_V volt(24)=5.09180000V\n  BATT_V volt(25)=0.0V") == 5.0918
    m = col.parse_meminfo("MemTotal:  8000000 kB\nMemAvailable: 2000000 kB\n")
    assert m["available_pct"] == 25.0
    osr = col.parse_os_release('PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nVERSION_CODENAME=trixie\nX=1\n')
    assert osr == {"PRETTY_NAME": "Debian GNU/Linux 13 (trixie)", "VERSION_CODENAME": "trixie"}
    cams = col.parse_list_cameras(
        "Available cameras\n-----------------\n0 : imx219 [3280x2464 10-bit RGGB] "
        "(/base/axi/pcie@1000120000/rp1/i2c@88000/imx219@10)\n    Modes: 'SRGGB10_CSI2P' : 640x480 [103.33 fps]\n")
    assert cams[0]["sensor"] == "imx219" and cams[0]["index"] == 0
    assert col.parse_list_cameras("No cameras available!") == []
    sh = col.parse_systemctl_show("ActiveState=active\nSubState=running\nNRestarts=0\n")
    assert sh["ActiveState"] == "active"
    ini = col.parse_ini_sections("[General]\nTcpServerPort = 5760\n# c\n[UartEndpoint pixhawk]\nDevice=/dev/ttyAMA0\nBaud = 921600\n")
    assert ini[0]["keys"]["TcpServerPort"] == "5760" and ini[1]["name"] == "pixhawk"
    ss = col.parse_ss_listening("tcp   LISTEN 0 5 0.0.0.0:5760 0.0.0.0:*\nudp UNCONN 0 0 [::]:14550 [::]:*\n")
    assert {"proto": "tcp", "addr": "0.0.0.0", "port": 5760} in ss
    assert ss[1]["port"] == 14550
    p = col.parse_ping("3 packets transmitted, 3 received, 0% packet loss, time 2003ms\n"
                       "rtt min/avg/max/mdev = 40.1/45.5/50.2/4.1 ms\n")
    assert p == {"loss_pct": 0.0, "rtt_ms": 45.5}
    iw = col.parse_iw_link("Connected to aa:bb\n\tSSID: Home\n\tfreq: 5180\n\tsignal: -61 dBm\n\ttx bitrate: 433.3 MBit/s\n")
    assert iw["ssid"] == "Home" and iw["signal_dbm"] == -61
    assert col.parse_iw_link("Not connected.") == {"connected": False}


def _fake_root(tmp: Path) -> Path:
    r = tmp / "root"
    (r / "proc/device-tree").mkdir(parents=True)
    (r / "proc/device-tree/model").write_text("Raspberry Pi 5 Model B Rev 1.0\x00")
    (r / "proc/sys/kernel/random").mkdir(parents=True)
    (r / "proc/sys/kernel/random/boot_id").write_text("abc-123\n")
    (r / "proc/uptime").write_text("1234.5 4000.0\n")
    (r / "proc/meminfo").write_text("MemTotal: 8000000 kB\nMemAvailable: 6000000 kB\n")
    (r / "proc/stat").write_text("cpu  100 0 100 800 0 0 0 0 0 0\n")
    (r / "proc/loadavg").write_text("0.10 0.20 0.30 1/100 1234\n")
    (r / "etc").mkdir()
    (r / "etc/os-release").write_text('PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nVERSION_CODENAME=trixie\n')
    z = r / "sys/class/thermal/thermal_zone0"
    z.mkdir(parents=True)
    (z / "type").write_text("cpu-thermal\n")
    (z / "temp").write_text("51234\n")
    hw = r / "sys/class/hwmon/hwmon1"
    hw.mkdir(parents=True)
    (hw / "name").write_text("rpi_volt\n")
    (hw / "in0_lcrit_alarm").write_text("0\n")
    logs = r / "var/log/x500"
    logs.mkdir(parents=True)
    (logs / "bb-1.log").write_text("x" * 100)
    (r / "etc/mavlink-router").mkdir(parents=True)
    (r / "etc/mavlink-router/main.conf").write_text("[General]\nTcpServerPort=5760\n[UartEndpoint fc]\nDevice=/dev/ttyAMA0\n")
    usb = r / "sys/bus/usb/devices/1-1"
    usb.mkdir(parents=True)
    (usb / "idVendor").write_text("19d2\n")
    (usb / "idProduct").write_text("1405\n")
    (usb / "product").write_text("ZTE Mobile Broadband\n")
    return r


def test_collector_run_on_fake_root(tmp_path):
    root = _fake_root(tmp_path)
    cfg = {"blackbox_globs": ["/var/log/x500/*.log"], "services": {}, "camera_probe": True}
    out = subprocess.run(
        [sys.executable, "-I", str(COLLECTOR), "--root", str(root), "--no-commands",
         "--config-b64", __import__("base64").b64encode(json.dumps(cfg).encode()).decode()],
        capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    doc = json.loads(out.stdout)
    assert doc["schema"] == "x500fs.collector/1"
    s = doc["sections"]
    assert s["identity"]["data"]["model"] == "Raspberry Pi 5 Model B Rev 1.0"
    assert s["identity"]["data"]["boot_id"] == "abc-123"
    assert s["uptime"]["data"]["uptime_s"] == 1234.5
    assert s["thermal"]["data"]["cpu_temp_c"] == 51.234
    assert s["memory"]["data"]["available_pct"] == 75.0
    assert s["power"]["data"]["hwmon_undervoltage_alarm"] == 0
    assert s["power"]["data"]["throttled"] is None  # vcgencmd unavailable -> reported, not invented
    assert s["blackbox"]["data"]["file_count"] == 1
    assert s["mavlink_router"]["data"]["general"]["TcpServerPort"] == "5760"
    assert s["usb"]["data"]["devices"][0]["vid"] == "19d2"
    assert s["camera"]["data"]["tool"] is None
    # network needs `ip`; with commands disabled the section must fail gracefully, not crash
    assert s["network"]["ok"] is False and "ip" in s["network"]["error"]


def test_collector_never_writes(tmp_path):
    root = _fake_root(tmp_path)
    before = sorted(str(p.relative_to(root)) + str(p.stat().st_mtime) for p in root.rglob("*"))
    subprocess.run([sys.executable, "-I", str(COLLECTOR), "--root", str(root), "--no-commands"],
                   capture_output=True, timeout=60, check=True)
    after = sorted(str(p.relative_to(root)) + str(p.stat().st_mtime) for p in root.rglob("*"))
    assert before == after


def test_collector_source_is_stdlib_only():
    import ast

    tree = ast.parse(COLLECTOR.read_text())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    allowed = set(sys.stdlib_module_names) | {"__future__"}
    assert mods <= allowed, mods - allowed
    # No command the script can run escalates privileges, manages packages or
    # controls services/power: inspect every literal argv passed to run().
    banned_first = {"sudo", "su", "apt", "apt-get", "dpkg", "pip", "pip3", "reboot", "shutdown", "poweroff"}
    banned_systemctl = {"start", "stop", "restart", "enable", "disable", "kill", "reboot", "mask"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "run" and node.args:
            argv = node.args[0]
            if isinstance(argv, ast.List) and argv.elts and isinstance(argv.elts[0], ast.Constant):
                words = [e.value for e in argv.elts if isinstance(e, ast.Constant)]
                assert words[0] not in banned_first, words
                if words[0] == "systemctl":
                    assert not banned_systemctl & set(words), words
    # Files are only ever opened for reading.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open":
            mode = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "mode"), None)
            if mode is not None:
                assert isinstance(mode, ast.Constant) and not set(mode.value) & set("wax+"), ast.dump(node)
