"""End-to-end 'live' controller against a fake SSH server and a mock autopilot.

Exercises the real code path used with hardware: host-key prompt, SSH, the
streamed collector, MAVLink over an SSH tunnel, interruptions/reconnect,
evaluation and report export - with no Raspberry Pi or Pixhawk.
"""

from __future__ import annotations

import json
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
    assert all(d.get("command") == 512 for _, _, t, d in ap.received if t == "COMMAND_LONG")

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
