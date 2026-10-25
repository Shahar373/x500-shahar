"""The MAVLink client is read-only and never acts as a ground station."""

from __future__ import annotations

import time

import pytest

from x500fs.core.observations import ObservationStore
from x500fs.core.session import SessionLog
from x500fs.sim.mock_mavlink import MockAutopilot
from x500fs.transport.mav_ingest import MavIngest
from x500fs.transport.mavlink_client import ForbiddenMessage, MavConfig, MavlinkClient
from x500fs.transport.streams import TcpStream


def _client(ap, **cfg):
    store, log = ObservationStore(False), SessionLog()
    ing = MavIngest(store, log, simulated=False, endpoint="t")
    c = MavlinkClient(lambda: TcpStream("127.0.0.1", ap.port), ing, log, MavConfig(**cfg))
    return c, store, log


def wait(pred, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_never_sends_heartbeat_or_commands_other_than_request_message():
    ap = MockAutopilot().start()
    c, store, _ = _client(ap)
    c.start()
    c.refresh(["ARMING_CHECK", "GPS_HDOP_GOOD", "DOES_NOT_EXIST"])
    try:
        assert wait(lambda: store.get("mav.param.GPS_HDOP_GOOD") is not None)
        assert wait(lambda: store.get("mav.msg.AUTOPILOT_VERSION") is not None)
        time.sleep(1)
    finally:
        c.stop()
        ap.stop()
    types = {t for _, _, t, _ in ap.received}
    assert "HEARTBEAT" not in types
    assert types <= {"PARAM_REQUEST_READ", "COMMAND_LONG"}
    for sysid, compid, t, d in ap.received:
        assert (sysid, compid) == (247, 25)
        if t == "COMMAND_LONG":
            assert d["command"] == 512


def test_passive_mode_sends_nothing():
    ap = MockAutopilot().start()
    c, store, _ = _client(ap, request_messages=False)
    c.start()
    c.refresh(["ARMING_CHECK"])
    try:
        assert wait(lambda: store.get("mav.msg.HEARTBEAT") is not None)
        time.sleep(2)
    finally:
        c.stop()
        ap.stop()
    assert ap.received == []


def test_forbidden_messages_raise():
    from pymavlink.dialects.v20 import ardupilotmega as m

    ap = MockAutopilot().start()
    c, store, _ = _client(ap)
    c.start()
    try:
        assert wait(lambda: c._mav is not None and store.get("mav.msg.HEARTBEAT") is not None)
        mav = c._mav
        with pytest.raises(ForbiddenMessage):
            c._send(mav.heartbeat_encode(6, 8, 0, 0, 0))
        with pytest.raises(ForbiddenMessage):
            c._send(mav.command_long_encode(1, 1, m.MAV_CMD_COMPONENT_ARM_DISARM, 0, 1, 0, 0, 0, 0, 0, 0))
        with pytest.raises(ForbiddenMessage):
            c._send(mav.param_set_encode(1, 1, b"ARMING_CHECK", 0, 6))
        with pytest.raises(ForbiddenMessage):
            c._send(mav.set_mode_encode(1, 1, 4))
        with pytest.raises(ForbiddenMessage):
            c._send(mav.request_data_stream_encode(1, 1, 0, 4, 1))
    finally:
        c.stop()
        ap.stop()


def test_missing_param_marked_after_retries():
    ap = MockAutopilot().start()
    c, store, _ = _client(ap)
    c.start()
    c.refresh(["DOES_NOT_EXIST"])
    try:
        assert wait(lambda: store.get("mav.param_missing.DOES_NOT_EXIST") is not None, 15)
    finally:
        c.stop()
        ap.stop()
    reads = [d for _, _, t, d in ap.received if t == "PARAM_REQUEST_READ"]
    assert len(reads) == 3


def test_disconnect_and_reconnect_recorded():
    ap = MockAutopilot().start()
    c, store, log = _client(ap)
    c.start()
    try:
        assert wait(lambda: store.get("mav.msg.HEARTBEAT") is not None)
        ap.drop_clients()
        assert wait(lambda: any(i.link == "mavlink" for i in log.interruptions()))
        assert wait(lambda: all(not i.open for i in log.interruptions()), 15)
        assert store.get("mav.link").value["connected"]
    finally:
        c.stop()
        ap.stop()


def test_heartbeat_timeout_without_socket_close_is_detected():
    ap = MockAutopilot().start()
    c, store, log = _client(ap)
    c.start()
    try:
        assert wait(lambda: store.get("mav.msg.HEARTBEAT") is not None)
        ap.paused.set()
        assert wait(lambda: any(i.link == "mavlink" and i.open for i in log.interruptions()), 8)
        assert store.get("mav.link").value["state"] == "stale"
        ap.paused.clear()
        assert wait(lambda: all(not i.open for i in log.interruptions()), 8)
    finally:
        c.stop()
        ap.stop()


def test_gcs_heartbeat_is_peer_not_vehicle():
    ap = MockAutopilot().start()
    c, store, _ = _client(ap)
    c.start()
    try:
        assert wait(lambda: store.get("mav.peer.255.190") is not None)
        assert store.get("mav.link").value["target_sysid"] == 1
    finally:
        c.stop()
        ap.stop()


def test_malformed_bytes_counted_not_fatal():
    store, log = ObservationStore(False), SessionLog()
    ing = MavIngest(store, log, simulated=False, endpoint="t")
    ing.handle({"mavpackettype": "BAD_DATA"}, 1, 1)
    ing.handle({"no": "type"}, 1, 1)
    ing.handle({"mavpackettype": "PARAM_VALUE", "param_id": None}, 1, 1)
    assert ing.bad_packets == 2  # param from unknown target is ignored, not counted


def test_udp_listen_accepts_only_one_sender():
    import socket

    from x500fs.transport.streams import UdpListenStream

    st = UdpListenStream(0, bind="127.0.0.1", read_timeout=1.0)
    port = st._sock.getsockname()[1]
    router = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    router.bind(("127.0.0.1", 0))
    router.sendto(b"vehicle", ("127.0.0.1", port))
    assert st.recv(4096) == b"vehicle" and st.allowed_ip == "127.0.0.1"
    st.close()
    router.close()
    # a pinned address: datagrams from anyone else are dropped and counted, never answered
    st = UdpListenStream(0, bind="127.0.0.1", read_timeout=1.0, allowed_peer="192.0.2.7")
    port = st._sock.getsockname()[1]
    spoof = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    spoof.sendto(b"fake heartbeat", ("127.0.0.1", port))
    assert st.recv(4096) is None and st.rejected == 1 and st.rejected_from == {"127.0.0.1"}
    st.send(b"request")  # no peer was accepted, so nothing is sent to the spoofer
    spoof.settimeout(0.3)
    with pytest.raises(socket.timeout):
        spoof.recvfrom(100)
    st.close()
    spoof.close()
