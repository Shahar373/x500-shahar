"""SSH: host-key policy, auth, exec with stdin (real collector), MAVLink tunnel."""

from __future__ import annotations

import time

import paramiko
import pytest
from fake_sshd import FakeSshd

from x500fs.core.observations import ObservationStore
from x500fs.core.session import SessionLog
from x500fs.sim.mock_mavlink import MockAutopilot
from x500fs.settings import PiSettings, VideoSettings
from x500fs.transport.mav_ingest import MavIngest
from x500fs.transport.mavlink_client import MavConfig, MavlinkClient
from x500fs.transport.pi_link import collector_command, collector_config, parse_collector_output
from x500fs.transport.ssh import (
    AuthFailed, HostKeyMismatch, HostKeyUnknown, KnownHosts, SshAuth, SshError, SshSession, fingerprint_sha256,
)
from x500fs.transport.streams import SshChannelStream


@pytest.fixture()
def sshd():
    s = FakeSshd().start()
    yield s
    s.stop()


def _kh(tmp_path):
    return KnownHosts(tmp_path / "known_hosts", system_file=tmp_path / "none")


def test_unknown_host_key_is_never_trusted_silently(sshd, tmp_path):
    kh = _kh(tmp_path)
    sess = SshSession("127.0.0.1", sshd.port, "pi", SshAuth("password", password="pw"), kh)
    with pytest.raises(HostKeyUnknown) as ei:
        sess.connect()
    assert ei.value.fingerprint == fingerprint_sha256(sshd.host_key)
    assert ei.value.fingerprint.startswith("SHA256:")
    assert not (tmp_path / "known_hosts").exists()  # nothing saved without user consent


def test_trusted_key_connects_and_exec_works(sshd, tmp_path):
    kh = _kh(tmp_path)
    kh.add("127.0.0.1", sshd.port, sshd.host_key)
    sess = SshSession("127.0.0.1", sshd.port, "pi", SshAuth("password", password="pw"), kh)
    sess.connect()
    try:
        res = sess.exec("echo hello", timeout=10)
        assert res.exit_code == 0 and res.stdout.strip() == b"hello"
        res = sess.exec("cat", stdin=b"from-stdin", timeout=10)
        assert res.stdout == b"from-stdin"
        ms = sess.ping(timeout=3)
        assert ms is not None and ms >= 0
    finally:
        sess.close()


def test_changed_host_key_is_refused(sshd, tmp_path):
    kh = _kh(tmp_path)
    kh.add("127.0.0.1", sshd.port, paramiko.RSAKey.generate(2048))  # a different key for this host
    sess = SshSession("127.0.0.1", sshd.port, "pi", SshAuth("password", password="pw"), kh)
    with pytest.raises(HostKeyMismatch):
        sess.connect()


def test_forget_host_key(sshd, tmp_path):
    kh = _kh(tmp_path)
    kh.add("127.0.0.1", sshd.port, sshd.host_key)
    assert kh.lookup("127.0.0.1", sshd.port)
    assert kh.forget("127.0.0.1", sshd.port)
    assert not kh.lookup("127.0.0.1", sshd.port)


def test_wrong_password_fails_auth(sshd, tmp_path):
    kh = _kh(tmp_path)
    kh.add("127.0.0.1", sshd.port, sshd.host_key)
    sess = SshSession("127.0.0.1", sshd.port, "pi", SshAuth("password", password="nope"), kh)
    with pytest.raises(AuthFailed):
        sess.connect()


def test_key_auth(tmp_path):
    client_key = paramiko.RSAKey.generate(2048)
    key_file = tmp_path / "id_rsa"
    client_key.write_private_key_file(str(key_file))
    s = FakeSshd(client_key=client_key).start()
    try:
        kh = _kh(tmp_path)
        kh.add("127.0.0.1", s.port, s.host_key)
        sess = SshSession("127.0.0.1", s.port, "pi", SshAuth("key", key_path=str(key_file)), kh)
        sess.connect()
        assert sess.exec("true", timeout=10).exit_code == 0
        sess.close()
    finally:
        s.stop()


def test_real_collector_streamed_over_ssh(sshd, tmp_path):
    kh = _kh(tmp_path)
    kh.add("127.0.0.1", sshd.port, sshd.host_key)
    sess = SshSession("127.0.0.1", sshd.port, "pi", SshAuth("password", password="pw"), kh)
    sess.connect()
    try:
        pi = PiSettings()
        cmd, stdin = collector_command(pi, collector_config(pi, VideoSettings()))
        assert stdin and b"x500fs.collector/1" in stdin
        res = sess.exec(cmd, stdin=stdin, timeout=60)
        assert res.exit_code == 0, res.stderr
        doc = parse_collector_output(res.stdout)
        assert doc["sections"]["identity"]["ok"]
        assert doc["sections"]["identity"]["data"]["ssh_connection"]["server_ip"] == "127.0.0.1"
        # The command line installs nothing: it only pipes the script into python.
        assert sshd.commands[-1].startswith("python3 -I - --config-b64 ")
    finally:
        sess.close()


def test_mavlink_over_ssh_tunnel(sshd, tmp_path):
    ap = MockAutopilot().start()
    kh = _kh(tmp_path)
    kh.add("127.0.0.1", sshd.port, sshd.host_key)
    sess = SshSession("127.0.0.1", sshd.port, "pi", SshAuth("password", password="pw"), kh)
    sess.connect()
    store, log = ObservationStore(False), SessionLog()
    ing = MavIngest(store, log, simulated=False, endpoint="t")
    client = MavlinkClient(lambda: SshChannelStream(sess, "127.0.0.1", ap.port), ing, log, MavConfig())
    client.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and store.get("mav.msg.HEARTBEAT") is None:
            time.sleep(0.1)
        assert store.get("mav.msg.HEARTBEAT") is not None
        assert ("127.0.0.1", ap.port) in sshd.forward_requests
    finally:
        client.stop()
        sess.close()
        ap.stop()


def test_forwarding_refused_gives_clear_error(sshd, tmp_path):
    sshd.refuse_forwarding = True
    kh = _kh(tmp_path)
    kh.add("127.0.0.1", sshd.port, sshd.host_key)
    sess = SshSession("127.0.0.1", sshd.port, "pi", SshAuth("password", password="pw"), kh)
    sess.connect()
    try:
        with pytest.raises(SshError, match="AllowTcpForwarding"):
            sess.open_direct_tcpip("127.0.0.1", 5760)
    finally:
        sess.close()


def test_exec_timeout(sshd, tmp_path):
    kh = _kh(tmp_path)
    kh.add("127.0.0.1", sshd.port, sshd.host_key)
    sess = SshSession("127.0.0.1", sshd.port, "pi", SshAuth("password", password="pw"), kh)
    sess.connect()
    try:
        t0 = time.monotonic()
        with pytest.raises(TimeoutError):
            sess.exec("sleep 5", timeout=1.0)
        assert time.monotonic() - t0 < 4
    finally:
        sess.close()
