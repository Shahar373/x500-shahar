"""End-to-end 'live' controller against a fake SSH server and a mock autopilot.

Exercises the real code path used with hardware: host-key prompt, SSH, the
streamed collector, MAVLink over an SSH tunnel, interruptions/reconnect,
evaluation and report export - with no Raspberry Pi or Pixhawk.
"""

from __future__ import annotations

import json
import re
import socket
import sys
import time

import pytest
from fake_sshd import FakeSshd

from x500fs import credentials
from x500fs.controller import Controller, Listener
from x500fs.core.status import Status
from x500fs.settings import SettingsStore
from x500fs.sim.mock_mavlink import MockAutopilot
from x500fs.video.testsource import SrtTestSource, free_udp_port
from videotools import network_ffmpeg

PASSWORD = "s3cret-Passw0rd"

# The fake SSH server executes POSIX shell commands (echo, cat, python3 -), as
# the real Pi does. These run on Linux CI; the Windows job runs everything else.
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="fake sshd runs POSIX shell commands")


class Recorder(Listener):
    def __init__(self):
        self.hostkeys = []
        self.notices = []

    def on_hostkey_unknown(self, info):
        self.hostkeys.append(info)

    def on_notice(self, level, text):
        self.notices.append((level, text))


def wait(pred, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture()
def live(tmp_path, monkeypatch):
    sshd = FakeSshd(password=PASSWORD).start()
    ap = MockAutopilot().start()
    monkeypatch.setattr(credentials, "get", lambda ident: PASSWORD if ident.startswith("ssh-password") else None)
    store = SettingsStore(tmp_path / "cfg")
    s = store.load()
    s.connection.host = "127.0.0.1"
    s.connection.ssh_port = sshd.port
    s.connection.username = "pi"
    s.connection.auth_method = "password"
    s.connection.expected_hostname = socket.gethostname()
    s.mavlink.tunnel_port = ap.port
    s.pi.poll_interval_s = 3
    s.reports_dir = str(tmp_path / "reports")
    store.save(s)
    rec = Recorder()
    ctl = Controller(store, rec)
    yield ctl, rec, sshd, ap
    ctl.disconnect()
    time.sleep(0.3)
    sshd.stop()
    ap.stop()


def test_live_end_to_end(live, tmp_path):
    ctl, rec, sshd, ap = live
    assert not ctl.simulation
    ctl.connect()
    assert wait(lambda: rec.hostkeys), "expected a host-key prompt"
    assert ctl.store.get("pi.ssh").value["state"] == "hostkey_unknown"
    ctl.trust_pending_host_key()
    assert wait(lambda: (ctl.store.get("pi.ssh") or None) and ctl.store.get("pi.ssh").value["state"] == "connected")
    ctl.run_preflight()
    assert wait(lambda: ctl.store.get("mav.msg.HEARTBEAT") is not None)
    assert wait(lambda: ctl.store.get("pi.collector") is not None, 30), ctl.store.get("pi.collector_error")
    assert wait(lambda: ctl.store.get("mav.param.ARMING_CHECK") is not None)
    results, overall, snap = ctl.evaluate()
    res = {r.check_id: r for r in results}
    assert res["pi.reachable"].status is Status.PASS
    assert res["pi.identity"].status in (Status.PASS, Status.FAIL)  # model is not a Pi in CI
    assert res["link.mavlink_heartbeat"].status is Status.PASS
    assert res["fc.identity"].status is Status.PASS
    assert res["fc.armed"].status is Status.PASS
    assert res["fc.arming_checks"].status is Status.PASS
    assert res["link.rtt"].status in (Status.PASS, Status.WARN)
    assert not res["pi.reachable"].source.startswith("SIM")
    # the app never sent anything but read requests
    sent = {t for _, _, t, _ in ap.received}
    assert sent <= {"PARAM_REQUEST_READ", "COMMAND_LONG"}
    assert wait(lambda: (ctl.store.get("mav.prearm_diag") or None)
                and ctl.store.get("mav.prearm_diag").value["state"] == "complete")
    cmds = [d.get("command") for _, _, t, d in ap.received if t == "COMMAND_LONG"]
    assert set(cmds) <= {512, 401}  # REQUEST_MESSAGE + RUN_PREARM_CHECKS (explicit preflight only)
    assert cmds.count(401) == 1

    # interruption + automatic reconnect
    sshd.kill_connections()
    assert wait(lambda: any(i.link in ("ssh", "mavlink") for i in ctl.session.interruptions()), 20)
    assert wait(lambda: ctl.store.get("pi.ssh").value["state"] == "connected", 30)
    assert wait(lambda: (time.monotonic() - ctl.store.get("mav.msg.HEARTBEAT").t_mono) < 2, 20)

    # camera gate: disarmed + fresh heartbeat -> allowed
    assert ctl.camera_gate().allowed
    ap.armed = True
    assert wait(lambda: ctl.camera_gate().allowed is False, 5)

    jp, hp = ctl.export_report(tmp_path / "rep")
    text = jp.read_text("utf-8") + hp.read_text("utf-8")
    assert PASSWORD not in text
    doc = json.loads(jp.read_text("utf-8"))
    assert doc["simulation"] is False
    assert doc["interruptions"], "interruption should be in the report"
    assert doc["hardware"]["ssh_host_key"].startswith("SHA256:")


def test_live_connect_reads_params_without_run_preflight(live):
    """Parameters are read (read-only) right after connecting; the pre-arm command still waits for the button."""
    ctl, rec, sshd, ap = live
    ctl.connect()
    assert wait(lambda: rec.hostkeys)
    ctl.trust_pending_host_key()
    assert wait(lambda: ctl.store.get("mav.param.ARMING_CHECK") is not None, 20)
    assert wait(lambda: ctl.store.get("mav.param.BATT_MONITOR") is not None, 20)
    assert ctl.preflight_started is None
    res = {r.check_id: r for r in ctl.evaluate()[0]}
    assert res["fc.arming_checks"].status is Status.PASS
    assert res["batt.monitor"].status is not Status.UNKNOWN
    sent = {t for _, _, t, _ in ap.received}
    assert sent <= {"PARAM_REQUEST_READ", "COMMAND_LONG"}
    cmds = [d.get("command") for _, _, t, d in ap.received if t == "COMMAND_LONG"]
    assert 401 not in cmds  # MAV_CMD_RUN_PREARM_CHECKS only on "run preflight"


def test_hostkey_rejection_disconnects(live):
    ctl, rec, sshd, ap = live
    ctl.connect()
    assert wait(lambda: rec.hostkeys)
    ctl.reject_pending_host_key()
    assert not ctl.connected


def test_camera_refused_without_heartbeat(tmp_path):
    store = SettingsStore(tmp_path / "c")
    ctl = Controller(store, Listener())
    gate = ctl.camera_gate()
    assert not gate.allowed
    ctl.connected = True  # pretend: connected but no telemetry
    gate = ctl.camera_gate()
    assert not gate.allowed and "HEARTBEAT" in gate.reason


SUDO_RE = re.compile(r"sudo -n /usr/bin/systemctl (start|stop) x500-video@(high|normal|degraded)\.service")


def _fake_video_service(sshd, port, ffmpeg, password_required=False):
    state = {"active": None, "src": None}

    def show(m):
        on = state["active"] == m.group(1)
        out = (f"ActiveState={'active' if on else 'inactive'}\nSubState={'running' if on else 'dead'}\n"
               "MainPID=0\nNRestarts=0\n")
        return 0, out.encode(), b""

    def control(m):
        if password_required:
            return 1, b"", b"sudo: a password is required\n"
        action, prof = m.group(1), m.group(2)
        if action == "start":
            state["active"] = prof
            state["src"] = SrtTestSource(port, prof, ffmpeg_path=ffmpeg).start()
        else:
            if state["src"]:
                state["src"].stop()
            state["active"] = None
        return 0, b"", b""

    sshd.handlers += [(re.compile(r"/usr/bin/systemctl show x500-video@(high|normal|degraded)\.service .*"), show),
                      (SUDO_RE, control)]
    return state


def _connect(ctl, rec):
    ctl.connect()
    assert wait(lambda: rec.hostkeys)
    ctl.trust_pending_host_key()
    assert wait(lambda: ctl.store.get("mav.msg.HEARTBEAT") is not None)
    assert wait(lambda: ctl.camera_gate().allowed, 10)


def _srt_settings(ctl, port, ffmpeg):
    v = ctl.settings.video
    v.backend, v.srt_profile, v.srt_host, v.srt_port, v.ffmpeg_path = "srt", "degraded", "127.0.0.1", port, ffmpeg
    v.service_start_wait_s = 3.0


@pytest.mark.video
def test_live_srt_video_starts_pi_service_and_decodes(live):
    ctl, rec, sshd, ap = live
    ff = network_ffmpeg()
    port = free_udp_port()
    state = _fake_video_service(sshd, port, ff)
    _srt_settings(ctl, port, ff)
    _connect(ctl, rec)
    try:
        assert ctl.start_video().allowed
        assert wait(lambda: ctl.video_worker() is not None and ctl.video_worker().frames >= 10, 30), \
            (ctl.video_worker().status() if ctl.video_worker() else None, rec.notices)
        st = ctl.video_worker().status()
        assert st["transport"] == "srt" and st["source_size"] == "480x360"
        assert "sudo -n /usr/bin/systemctl start x500-video@degraded.service" in sshd.commands
        ctl.stop_video()
        assert wait(lambda: "sudo -n /usr/bin/systemctl stop x500-video@degraded.service" in sshd.commands, 10)
        # every privileged command was one of the six allowlisted ones
        assert all(SUDO_RE.fullmatch(c) for c in sshd.commands if "sudo" in c)
        # armed: neither start nor a service change is allowed
        ap.armed = True
        assert wait(lambda: ctl.camera_gate().allowed is False, 5)
        assert ctl.start_video().allowed is False
    finally:
        if state["src"]:
            state["src"].stop()


@pytest.mark.video
def test_live_srt_password_required_is_explained_not_bypassed(live):
    ctl, rec, sshd, ap = live
    ff = network_ffmpeg()
    port = free_udp_port()
    _fake_video_service(sshd, port, ff, password_required=True)
    _srt_settings(ctl, port, ff)
    _connect(ctl, rec)
    assert ctl.start_video().allowed
    assert wait(lambda: ctl.video_worker() is None, 10)
    assert any("x500-video degraded" in t for _, t in rec.notices), rec.notices
    assert ctl.store.get("video.state").value["state"] == "failed"
    assert [c for c in sshd.commands if "sudo" in c] == ["sudo -n /usr/bin/systemctl start x500-video@degraded.service"]


@pytest.mark.video
def test_live_disconnect_stops_the_pi_service_this_app_started(live):
    ctl, rec, sshd, ap = live
    ff = network_ffmpeg()
    port = free_udp_port()
    state = _fake_video_service(sshd, port, ff)
    _srt_settings(ctl, port, ff)
    _connect(ctl, rec)
    try:
        assert ctl.start_video().allowed
        assert wait(lambda: state["active"] == "degraded", 15)
        t = ctl.disconnect()
        t.join(25)
        assert "sudo -n /usr/bin/systemctl stop x500-video@degraded.service" in sshd.commands, rec.notices
        assert state["active"] is None
    finally:
        if state["src"]:
            state["src"].stop()


def test_live_stop_while_service_starts_undoes_the_start(live):
    ctl, rec, sshd, ap = live
    started = {"n": 0}
    stopped = []

    def show(m):
        on = started["n"] > 0 and not stopped
        return 0, (f"ActiveState={'active' if on else 'inactive'}\nSubState={'running' if on else 'dead'}\n"
                   "MainPID=0\nNRestarts=0\n").encode(), b""

    def control(m):
        if m.group(1) == "start":
            ctl.stop_video()             # the user presses "stop" while systemctl start is running
            started["n"] += 1
        else:
            stopped.append(m.group(2))
        return 0, b"", b""

    sshd.handlers += [(re.compile(r"/usr/bin/systemctl show x500-video@(high|normal|degraded)\.service .*"), show),
                      (SUDO_RE, control)]
    _srt_settings(ctl, free_udp_port(), "ffmpeg")
    _connect(ctl, rec)
    assert ctl.start_video().allowed
    assert wait(lambda: stopped == ["degraded"], 15), (sshd.commands, rec.notices)
    assert ctl.video_worker() is None and ctl._video_service_started is None


ROUTER_SUDO_RE = re.compile(r"sudo -n /usr/bin/systemctl (reset-failed|start) mavlink-router\.service")


def test_router_restart_only_when_systemd_reports_it_down(live):
    ctl, rec, sshd, ap = live
    router = {"state": "active"}

    def show(m):
        st = router["state"]
        return 0, (f"ActiveState={st}\nSubState={'running' if st == 'active' else 'dead'}\n"
                   "MainPID=0\nNRestarts=0\nResult=success\n").encode(), b""

    def control(m):
        if m.group(1) == "start":
            router["state"] = "active"
        return 0, b"", b""

    sshd.handlers[:0] = [(re.compile(r"/usr/bin/systemctl show mavlink-router(\.service)? .*"), show),
                         (ROUTER_SUDO_RE, control)]
    _connect(ctl, rec)
    assert wait(lambda: ctl.store.get("pi.collector") is not None, 15)

    def collector_says(state):
        # the streamed collector runs on this test machine, so its systemd view is injected here
        import copy
        doc = copy.deepcopy(ctl.store.get("pi.collector").value)
        doc["sections"]["services"]["data"]["units"]["mavlink-router"] = {"ActiveState": state, "SubState": "x"}
        ctl.store.put("pi.collector", doc, "Pi collector", simulated=False)

    collector_says("active")
    gate = ctl.router_gate()
    assert not gate.allowed and "לא מדווח כנפול" in gate.reason
    assert ctl.restart_mavlink_router().allowed is False
    assert not any("sudo" in c and "mavlink" in c for c in sshd.commands)
    # systemd reports it failed: the button is offered and starts it (reset-failed first)
    router["state"] = "failed"
    collector_says("failed")
    assert ctl.router_gate().allowed, ctl.router_gate().reason
    assert ctl.restart_mavlink_router().allowed
    assert wait(lambda: any("router" in t or "mavlink-router" in t for _, t in rec.notices), 15)
    sudo = [c for c in sshd.commands if c.startswith("sudo")]
    assert sudo == ["sudo -n /usr/bin/systemctl reset-failed mavlink-router.service",
                    "sudo -n /usr/bin/systemctl start mavlink-router.service"], sudo
    assert router["state"] == "active"


def test_router_control_rechecks_state_before_sending():
    from x500fs.transport.pi_router import RouterControl

    class Res:
        def __init__(self, out=b"", rc=0, err=b""):
            self.stdout, self.exit_code, self.stderr = out, rc, err

    class Sess:
        def __init__(self, state):
            self.state, self.cmds = state, []

        def exec(self, cmd, timeout=None):
            self.cmds.append(cmd)
            if "show" in cmd:
                return Res(f"ActiveState={self.state}\nSubState=x\n".encode())
            if "start" in cmd:
                return Res(rc=1, err=b"sudo: a password is required\n")
            return Res()

    s = Sess("active")  # the app's data said "down", but it is running now: nothing is sent
    r = RouterControl(s).start_if_down()
    assert not r.ok and not any("sudo" in c for c in s.cmds)
    s = Sess("inactive")
    r = RouterControl(s).start_if_down(wait_s=0.1)
    assert not r.ok and r.needs_password and r.commands == ("sudo -n /usr/bin/systemctl start mavlink-router.service",)
