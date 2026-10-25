"""Corrected Pi scripts (pi/x500) and explicit deployment - run against fakes, never a real Pi."""

from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "pi" / "x500" / "bin"
DEPLOY = ROOT / "pi" / "deploy" / "deploy_x500.sh"
LIB = ROOT / "src"

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Pi scripts are bash/python for Linux")


def fake_bin(tmp: Path, scripts: dict[str, str]) -> str:
    d = tmp / "fakebin"
    d.mkdir(exist_ok=True)
    for name, body in scripts.items():
        p = d / name
        p.write_text("#!/bin/bash\n" + body + "\n")
        p.chmod(0o755)
    return str(d)


def run_script(name, *args, env_extra=None, path_prefix=None, timeout=60):
    env = dict(os.environ)
    env["X500FS_LIB"] = str(LIB)
    if path_prefix:
        env["PATH"] = path_prefix + os.pathsep + env["PATH"]
    env.update(env_extra or {})
    return subprocess.run([str(BIN / name), *args], capture_output=True, text=True, env=env, timeout=timeout)


def empty_root(tmp: Path) -> str:
    r = tmp / "root"
    (r / "proc").mkdir(parents=True, exist_ok=True)
    shutil.copy("/proc/meminfo", r / "proc" / "meminfo")
    shutil.copy("/proc/stat", r / "proc" / "stat")
    shutil.copy("/proc/loadavg", r / "proc" / "loadavg")
    return str(r)


# ---------------------------------------------------------------- x500-health (review item 1)


def test_health_failed_vcgencmd_is_unavailable_never_zero(tmp_path):
    fb = fake_bin(tmp_path, {"vcgencmd": "echo 'VCHI initialization failed' >&2; exit 1"})
    env = {"X500FS_ROOT": empty_root(tmp_path)}
    key = run_script("x500-health", "--key", env_extra=env, path_prefix=fb)
    assert key.returncode == 2, key
    assert "state=UNKNOWN" in key.stdout and "temp=unavailable" in key.stdout and "power=unavailable" in key.stdout
    machine = run_script("x500-health", "--machine", env_extra=env, path_prefix=fb)
    assert "temp=unavailable" in machine.stdout and "temp=0" not in machine.stdout
    assert "power=unavailable" in machine.stdout and "0x0" not in machine.stdout
    js = json.loads(run_script("x500-health", "--json", env_extra=env, path_prefix=fb).stdout)
    assert js["temp_c"] is None and js["power"] == "unavailable"


def test_health_invalid_throttle_text_is_unavailable(tmp_path):
    fb = fake_bin(tmp_path, {"vcgencmd": 'case "$1" in measure_temp) echo "temp=48.3\'C";; '
                                         'get_throttled) echo "throttled=garbage";; *) exit 1;; esac'})
    r = run_script("x500-health", "--key", env_extra={"X500FS_ROOT": empty_root(tmp_path)}, path_prefix=fb)
    assert "temp=normal" in r.stdout and "power=unavailable" in r.stdout and r.returncode == 2


@pytest.mark.parametrize("flags,power,hist,state,rc", [
    ("0x0", "normal", "none", "OK", 0),
    ("0x50000", "normal", "events", "WARN", 1),       # history is visible, not hidden under OK
    ("0x50005", "current_fault", "events", "CRITICAL", 2),
])
def test_health_current_and_historical_flags_separate(tmp_path, flags, power, hist, state, rc):
    fb = fake_bin(tmp_path, {"vcgencmd": f'case "$1" in measure_temp) echo "temp=48.3\'C";; '
                                         f'get_throttled) echo "throttled={flags}";; *) exit 1;; esac'})
    r = run_script("x500-health", "--key", env_extra={"X500FS_ROOT": empty_root(tmp_path)}, path_prefix=fb)
    assert f"power={power}" in r.stdout and f"power_hist={hist}" in r.stdout
    # disk usage of the test machine may add a WARN; never better than expected
    assert r.returncode >= rc
    if r.returncode == rc:
        assert f"state={state}" in r.stdout


# ---------------------------------------------------------------- black box (review item 10)


def _bb_csv(path: Path, n=6, period=10, age=0):
    now = time.time() - age
    rows = ["timestamp,temp_c,throttled,ram_pct,disk_pct,load1,uplink,wifi_internet,lte_internet,tailscale,"
            "ground,video_profile,video_state,mavlink"]
    for i in range(n):
        ts = datetime.datetime.fromtimestamp(now - (n - 1 - i) * period).astimezone().isoformat(timespec="seconds")
        rows.append(f"{ts},51.2,0x0,23,40,0.31,wifi,ok,fail,active,not_configured,none,stopped,active")
    path.write_text("\n".join(rows) + "\n")


def _bb_env(tmp_path, active="active"):
    logdir = tmp_path / "bb"
    logdir.mkdir()
    conf = tmp_path / "x500.conf"
    conf.write_text(f'BLACKBOX_DIR="{logdir}"\nBLACKBOX_INTERVAL=10\n')
    fb = fake_bin(tmp_path, {"systemctl": f'echo "LoadState=loaded"; echo "ActiveState={active}"; '
                                          'echo "SubState=running"; echo "NRestarts=0"'})
    return logdir, {"X500_CONF": str(conf)}, fb


def test_blackbox_status_needs_service_and_recent_samples(tmp_path):
    logdir, env, fb = _bb_env(tmp_path)
    _bb_csv(logdir / "x500-blackbox-20261014.csv")
    r = run_script("x500-blackbox-status", env_extra=env, path_prefix=fb)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "HEALTHY" in r.stdout and "median 10.0s" in r.stdout
    assert "14 days" not in r.stdout and "none configured" in r.stdout


def test_blackbox_status_stale_samples_and_inactive_service(tmp_path):
    logdir, env, fb = _bb_env(tmp_path)
    _bb_csv(logdir / "x500-blackbox-20261014.csv", age=600)
    assert run_script("x500-blackbox-status", env_extra=env, path_prefix=fb).returncode == 1
    (tmp_path / "b").mkdir()
    logdir2, env2, fb2 = _bb_env(tmp_path / "b", active="inactive")
    _bb_csv(logdir2 / "x500-blackbox-20261014.csv")
    r = run_script("x500-blackbox-status", env_extra=env2, path_prefix=fb2)
    assert r.returncode == 2 and "NOT HEALTHY" in r.stdout


def test_blackbox_logger_writes_na_not_empty_or_zero(tmp_path):
    logdir = tmp_path / "bb"
    conf = tmp_path / "x500.conf"
    conf.write_text(f'BLACKBOX_DIR="{logdir}"\nBLACKBOX_INTERVAL=1\nGROUND_IP="GROUND_STATION_IP_NOT_CONFIGURED"\n')
    fb = fake_bin(tmp_path, {
        "vcgencmd": "exit 1",
        "ping": "exit 1",
        "ip": 'if [ "$1" = route ]; then echo "1.1.1.1 via 10.0.0.1 dev wlan0 src 10.0.0.2"; '
              'elif [ "$1" = link ]; then exit 0; fi',
        "systemctl": 'if [ "$1" = is-active ]; then echo active; else echo inactive; fi',
    })
    env = dict(os.environ, X500_CONF=str(conf), PATH=fb + os.pathsep + os.environ["PATH"])
    p = subprocess.Popen(["bash", str(BIN / "x500-blackbox")], env=env)
    try:
        deadline = time.time() + 10
        files = []
        while time.time() < deadline:
            files = list(logdir.glob("x500-blackbox-*.csv")) if logdir.exists() else []
            if files and len(files[0].read_text().splitlines()) >= 2:
                break
            time.sleep(0.2)
    finally:
        p.kill()
        p.wait()
    lines = files[0].read_text().splitlines()
    cols = lines[1].split(",")
    assert len(cols) == 14
    assert cols[1] == "NA" and cols[2] == "NA"           # never empty, never 0 / 0x0
    assert cols[6] == "wifi" and cols[10] == "not_configured"
    sys.path.insert(0, str(LIB / "x500fs" / "pi_side"))
    import x500fs_collector as C
    parsed = C.parse_blackbox_rows(lines, time.time())
    assert parsed["header_ok"] and parsed["valid_rows"] >= 1


@pytest.mark.parametrize("value", ["10.5", "10s", "0", ""])
def test_blackbox_logger_invalid_interval_falls_back_and_keeps_logging(tmp_path, value):
    logdir = tmp_path / "bb"
    conf = tmp_path / "x500.conf"
    conf.write_text(f'BLACKBOX_DIR="{logdir}"\nBLACKBOX_INTERVAL="{value}"\n')
    fb = fake_bin(tmp_path, {"vcgencmd": "exit 1", "ping": "exit 1", "ip": "exit 1", "systemctl": "echo inactive",
                             # the loop sleeps once after the first sample; record the period and stop
                             "sleep": f'echo "$1" > "{tmp_path}/slept"; exit 0'})
    env = dict(os.environ, X500_CONF=str(conf), PATH=fb + os.pathsep + os.environ["PATH"])
    p = subprocess.Popen(["bash", str(BIN / "x500-blackbox")], env=env, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.time() + 10
        while time.time() < deadline and p.poll() is None and not (tmp_path / "slept").exists():
            time.sleep(0.1)
        alive = p.poll() is None
    finally:
        p.kill()
        _, err = p.communicate()
    assert alive, err                                   # the loop did not abort
    assert 1.0 <= float((tmp_path / "slept").read_text()) <= 10.0
    if value:
        assert "invalid BLACKBOX_INTERVAL" in err
    files = list(logdir.glob("x500-blackbox-*.csv"))
    assert files and len(files[0].read_text().splitlines()) >= 2


def test_blackbox_status_invalid_interval_uses_same_default(tmp_path):
    logdir, env, fb = _bb_env(tmp_path)
    Path(env["X500_CONF"]).write_text(f'BLACKBOX_DIR="{logdir}"\nBLACKBOX_INTERVAL=10s\n')
    _bb_csv(logdir / "x500-blackbox-20261014.csv")
    r = run_script("x500-blackbox-status", env_extra=env, path_prefix=fb)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Traceback" not in r.stderr


# ---------------------------------------------------------------- deployment (explicit, dry run by default)


def test_deploy_dry_run_allowed_without_root_apply_is_not(tmp_path):
    fb = fake_bin(tmp_path, {"id": 'if [ "$1" = -u ]; then echo 1000; else exec /usr/bin/id "$@"; fi'})
    env = dict(os.environ, PATH=fb + os.pathsep + os.environ["PATH"])
    env.pop("X500FS_DEPLOY_ROOT", None)
    r = subprocess.run(["bash", str(DEPLOY), "--apply"], env=env, capture_output=True, text=True)
    assert r.returncode == 1 and "Run with sudo" in r.stderr
    r = subprocess.run(["bash", str(DEPLOY)], env=env, capture_output=True, text=True)
    assert "Run with sudo" not in r.stderr and "DRY RUN" in r.stdout


def test_deploy_backup_path_is_relative_to_root_and_ships_profiles_init(tmp_path):
    root = tmp_path / "sysroot"
    env = dict(os.environ, X500FS_DEPLOY_ROOT=str(root))
    subprocess.run(["bash", str(DEPLOY), "--apply"], env=env, check=True, capture_output=True)
    old = root / "usr" / "local" / "bin" / "x500-health"
    old.parent.mkdir(parents=True)
    old.write_text("#!/bin/bash\necho legacy\n")
    r = subprocess.run(["bash", str(DEPLOY), "--apply"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    stamps = list((root / "var" / "backups" / "x500fs").iterdir())
    assert len(stamps) == 1
    assert (stamps[0] / "usr" / "local" / "bin" / "x500-health").read_text() == "#!/bin/bash\necho legacy\n"
    assert not any(str(root).lstrip("/") in str(p.relative_to(stamps[0])) for p in stamps[0].rglob("*"))
    if (LIB / "x500fs" / "profiles" / "__init__.py").exists():
        assert (root / "opt" / "x500fs" / "lib" / "x500fs" / "profiles" / "__init__.py").exists()



def test_deploy_dry_run_changes_nothing_and_refuses_markers(tmp_path):
    root = tmp_path / "sysroot"
    env = dict(os.environ, X500FS_DEPLOY_ROOT=str(root))
    r = subprocess.run(["bash", str(DEPLOY)], env=env, capture_output=True, text=True)
    assert r.returncode == 0 and not (root / "etc").exists()          # dry run: nothing written
    subprocess.run(["bash", str(DEPLOY), "--apply"], env=env, check=True, capture_output=True)
    conf = root / "etc" / "x500" / "x500.conf"
    assert conf.exists() and not (root / "usr").exists()             # first apply: only the example config
    conf.write_text(conf.read_text() + 'ZTE_GATEWAY="ZTE_GATEWAY_IP_NOT_CONFIGURED"\n')
    r = subprocess.run(["bash", str(DEPLOY), "--apply"], env=env, capture_output=True, text=True)
    assert r.returncode == 1 and "Refusing" in r.stderr
    assert not (root / "usr").exists()


def test_deploy_apply_backs_up_and_installs(tmp_path):
    root = tmp_path / "sysroot"
    env = dict(os.environ, X500FS_DEPLOY_ROOT=str(root))
    subprocess.run(["bash", str(DEPLOY), "--apply"], env=env, check=True, capture_output=True)
    old = root / "usr" / "local" / "bin" / "x500-health"
    old.parent.mkdir(parents=True)
    old.write_text("#!/bin/bash\necho legacy\n")
    r = subprocess.run(["bash", str(DEPLOY), "--apply"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert old.read_text() == (BIN / "x500-health").read_text()
    backups = list((root / "var" / "backups" / "x500fs").rglob("x500-health"))
    assert backups and backups[0].read_text() == "#!/bin/bash\necho legacy\n"
    assert (root / "opt" / "x500fs" / "lib" / "x500fs" / "pi_tools" / "preflight.py").exists()
    assert (root / "opt" / "x500" / "bin" / "x500-blackbox").exists()
    assert (root / "opt" / "x500" / "bin" / "x500-video-run").read_text() == (BIN / "x500-video-run").read_text()
    assert not (root / "etc" / "x500" / "video.conf").exists()          # the camera choice stays the user's file
    assert "Nothing was restarted" in r.stdout
    assert not (root / "etc" / "systemd").exists()          # services/router/watchdog untouched


def test_bundle_contains_lib_and_scripts(tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import make_pi_bundle

    out = make_pi_bundle.build(tmp_path)
    with tarfile.open(out) as tar:
        names = tar.getnames()
    top = names[0].split("/")[0]
    for want in ("deploy_x500.sh", "install_video_sudoers.sh", "x500/bin/x500-preflight", "lib/x500fs/pi_tools/preflight.py",
                 "lib/x500fs/core/checks/fc.py", "lib/x500fs/profiles/flight.json", "SHA256SUMS"):
        assert f"{top}/{want}" in names, want
    if (LIB / "x500fs" / "profiles" / "__init__.py").exists():
        assert f"{top}/lib/x500fs/profiles/__init__.py" in names
    assert not any("__pycache__" in n or "/ui/" in n or "controller" in n for n in names)


# ---------------------------------------------------------------- shared evaluation on the Pi


def test_pi_preflight_uses_shared_checks_and_legacy_exit_codes(tmp_path, capsys):
    from x500fs.pi_tools import preflight
    from x500fs.sim.mock_mavlink import MockAutopilot

    ap = MockAutopilot().start()
    conf = tmp_path / "x500.conf"
    conf.write_text(f'MAVLINK_ENDPOINT="tcp:127.0.0.1:{ap.port}"\n')
    try:
        rc = preflight.main(["flight", "--config", str(conf), "--profile-dir", str(tmp_path), "--timeout", "12",
                             "--json"])
    finally:
        ap.stop()
    doc = json.loads(capsys.readouterr().out)
    res = {r["id"]: r for r in doc["results"]}
    assert rc == 2 and doc["overall"]["status"] in ("INCOMPLETE", "FAIL")   # unconfigured flight items
    for cid in ("pi.reachable", "link.path", "link.rtt", "video.stream"):
        assert res[cid]["status"] == "NOT_APPLICABLE"
    assert res["fc.armed"]["status"] == "PASS"
    assert res["fc.prearm"]["evidence"]["diagnostic"]["state"] == "complete"
    sent = {d.get("command") for _, _, t, d in ap.received if t == "COMMAND_LONG"}
    assert sent <= {512, 401}
    assert all(t != "HEARTBEAT" for _, _, t, _ in ap.received)
    assert all((s, c) == (246, 26) for s, c, _, _ in ap.received)


def test_pi_preflight_armed_never_sends_prearm_command(tmp_path, capsys):
    from x500fs.pi_tools import preflight
    from x500fs.sim.mock_mavlink import MockAutopilot

    ap = MockAutopilot(armed=True).start()
    conf = tmp_path / "x500.conf"
    conf.write_text(f'MAVLINK_ENDPOINT="tcp:127.0.0.1:{ap.port}"\n')
    try:
        rc = preflight.main(["bench", "--config", str(conf), "--profile-dir", str(tmp_path), "--timeout", "8"])
    finally:
        ap.stop()
    out = capsys.readouterr().out
    assert rc == 2 and "PREFLIGHT: FAIL" in out
    assert not any(t == "COMMAND_LONG" and d.get("command") == 401 for _, _, t, d in ap.received)


def test_pi_preflight_bad_numeric_config_falls_back_to_defaults():
    from x500fs.pi_tools import conf as conf_mod
    from x500fs.pi_tools import preflight

    conf = dict(conf_mod.DEFAULTS, MAVLINK_SYSID="", MAVLINK_COMPID="abc", TARGET_SYSID="x",
                BLACKBOX_INTERVAL="10s")
    d = preflight.drone_from_conf(conf)
    assert (d.mav_source_sysid, d.mav_source_compid, d.mav_target_sysid) == (246, 26, 0)
    assert d.blackbox_sleep_s == 10.0
    d = preflight.drone_from_conf(dict(conf_mod.DEFAULTS, MAVLINK_SYSID="200", TARGET_SYSID="1"))
    assert (d.mav_source_sysid, d.mav_target_sysid) == (200, 1)


def test_conf_ignores_markers_and_comments(tmp_path):
    from x500fs.pi_tools import conf as conf_mod

    p = tmp_path / "x500.conf"
    p.write_text('GROUND_IP="GROUND_STATION_IP_NOT_CONFIGURED"   # marker\nZTE_GATEWAY=192.168.0.1 # gw\n'
                 'X500_VENV_PYTHON="/home/PI_USER/venv/bin/python"\n')
    c = conf_mod.load(str(p))
    assert c["GROUND_IP"] == "" and c["ZTE_GATEWAY"] == "192.168.0.1" and c["X500_VENV_PYTHON"] == ""
    assert conf_mod.markers_in(p.read_text()) == ["GROUND_IP", "X500_VENV_PYTHON"]


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() != 0, reason="script requires root")
def test_video_sudoers_rule_is_exactly_six_commands(tmp_path):
    fb = fake_bin(tmp_path, {"visudo": "exit 0"})
    user = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
    env = dict(os.environ, PATH=fb + os.pathsep + os.environ["PATH"])
    r = subprocess.run(["bash", str(ROOT / "pi" / "deploy" / "install_video_sudoers.sh"), user], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    rule = next(line for line in r.stdout.splitlines() if "NOPASSWD" in line)
    cmds = [c.strip() for c in rule.split("NOPASSWD:", 1)[1].split(",")]
    assert len(cmds) == 6
    for c in cmds:
        assert c.split()[0] == "/usr/bin/systemctl" and c.split()[1] in ("start", "stop")
        assert c.split()[2] in {f"x500-video@{p}.service" for p in ("high", "normal", "degraded")}
    assert "*" not in rule and "ALL=(root) NOPASSWD: ALL" not in rule
    assert "Dry run" in r.stdout and not Path("/etc/sudoers.d/x500-video").exists()
    r = subprocess.run(["bash", str(ROOT / "pi" / "deploy" / "install_video_sudoers.sh"), user, "--router"],
                       env=env, capture_output=True, text=True)
    rule = next(line for line in r.stdout.splitlines() if "NOPASSWD" in line)
    cmds = [c.strip() for c in rule.split("NOPASSWD:", 1)[1].split(",")]
    assert len(cmds) == 8 and cmds[-2:] == ["/usr/bin/systemctl reset-failed mavlink-router.service",
                                            "/usr/bin/systemctl start mavlink-router.service"]
    assert not any(" stop mavlink" in c or "restart" in c for c in cmds)


# ---------------------------------------------------------------- x500-video-run: Pi camera or USB webcam


def fake_uvc_camera(root: Path, node="video0", name="UVC Camera (046d:0825)"):
    """sysfs as the kernel lays out a UVC webcam: class link -> USB interface bound to uvcvideo."""
    intf = root / "sys/devices/platform/axi/usb1/1-1/1-1:1.0"
    vdir = intf / "video4linux" / node
    vdir.mkdir(parents=True)
    (vdir / "name").write_text(name + "\n")
    (vdir / "index").write_text("0\n")
    (vdir / "device").symlink_to(intf)
    drv = root / "sys/bus/usb/drivers/uvcvideo"
    drv.mkdir(parents=True, exist_ok=True)
    (intf / "driver").symlink_to(drv)
    (intf.parent / "idVendor").write_text("046d\n")
    (intf.parent / "idProduct").write_text("0825\n")
    cls = root / "sys/class/video4linux"
    cls.mkdir(parents=True, exist_ok=True)
    (cls / node).symlink_to(vdir)


def run_video(tmp_path, profile, conf="", usb=False, extra=None):
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    if usb:
        fake_uvc_camera(root)
    (tmp_path / "video.conf").write_text("PORT=5600\n" + conf)
    fb = fake_bin(tmp_path, {"tailscale": "echo 100.64.0.20"})
    env = {"X500_VIDEO_CONF": str(tmp_path / "video.conf"), "X500FS_ROOT": str(root),
           "X500_VIDEO_RUN_DIR": str(tmp_path), "X500_VIDEO_DRY_RUN": "1", **(extra or {})}
    r = run_script("x500-video-run", profile, env_extra=env, path_prefix=fb)
    src = tmp_path / f"x500-video-{profile}.source"
    return r, (src.read_text() if src.exists() else "")


def test_video_run_without_usb_camera_uses_pi_camera_unchanged(tmp_path):
    r, src = run_video(tmp_path, "degraded")
    assert r.returncode == 0, r.stderr
    cmd = r.stdout.strip().splitlines()[-1]
    assert cmd.startswith("rpicam-vid -t 0 -n --codec yuv420 --denoise cdn_off --width 480 --height 360 --framerate 20")
    assert "-f rawvideo -pix_fmt yuv420p -video_size 480x360" in cmd and "v4l2" not in cmd
    assert "srt://100.64.0.20:5600\\?mode=listener" in cmd and "latency=300000" in cmd
    assert "camera=csi" in src and "mode=auto" in src


def test_video_run_auto_prefers_usb_webcam_and_caps_fps(tmp_path):
    r, src = run_video(tmp_path, "high", usb=True)
    assert r.returncode == 0, r.stderr
    cmd = r.stdout.strip().splitlines()[-1]
    assert "rpicam-vid" not in cmd
    assert "-f v4l2 -input_format mjpeg -video_size 640x480 -framerate 30 -i /dev/video0" in cmd
    assert "-g 30 -keyint_min 30" in cmd and "-b:v 1200k" in cmd       # C270 max 30 FPS, 1 s GOP kept
    assert "Camera: usb (/dev/video0)" in r.stdout
    assert "camera=usb" in src and "device=/dev/video0" in src and "fps=30" in src
    import shlex
    name = [ln for ln in src.splitlines() if ln.startswith("name=")][0][5:]
    assert shlex.split(name) == ["UVC Camera (046d:0825)"]


def test_video_run_usb_scales_to_profile_size(tmp_path):
    r, _ = run_video(tmp_path, "degraded", usb=True)
    cmd = r.stdout.strip().splitlines()[-1]
    assert "-video_size 640x480 -framerate 20" in cmd and "scale=480:360\\,format=yuv420p" in cmd


def test_video_run_forced_modes(tmp_path):
    r, _ = run_video(tmp_path, "normal", conf="CAMERA=usb\n")
    assert r.returncode == 11 and "no USB camera found" in r.stdout
    r, src = run_video(tmp_path, "normal", conf="CAMERA=csi\n", usb=True)
    assert r.returncode == 0 and "rpicam-vid" in r.stdout and "camera=csi" in src
    r, _ = run_video(tmp_path, "normal", conf="CAMERA=webcam\n")
    assert r.returncode == 2 and "Unknown CAMERA" in r.stdout


def test_video_run_without_tailscale_exits_10(tmp_path):
    (tmp_path / "video.conf").write_text("PORT=5600\n")
    fb = fake_bin(tmp_path, {"tailscale": "exit 1"})
    r = run_script("x500-video-run", "normal", path_prefix=fb,
                   env_extra={"X500_VIDEO_CONF": str(tmp_path / "video.conf"), "X500_VIDEO_RUN_DIR": str(tmp_path)})
    assert r.returncode == 10 and "No Tailscale" in r.stdout
