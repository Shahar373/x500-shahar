"""MAV_CMD_RUN_PREARM_CHECKS: ACK result and source are validated; silence is never a pass."""

from __future__ import annotations

import time

from helpers import World, prearm_diag_complete
from x500fs.core.status import Status
from x500fs.sim.mock_mavlink import MockAutopilot
from test_mavlink_safety import _client, wait


def _diag(store):
    o = store.get("mav.prearm_diag")
    return o.value if o is not None else None


def _run_diag(ap_kwargs=None, timeout=10, **cfg):
    ap = MockAutopilot(**(ap_kwargs or {})).start()
    c, store, log = _client(ap, **cfg)
    c.start()
    try:
        assert wait(lambda: store.get("mav.msg.HEARTBEAT") is not None)
        assert wait(lambda: store.get("mav.msg.SYS_STATUS") is not None or not ap.send_sys_status)
        c.request_prearm_diagnostic()
        assert wait(lambda: (_diag(store) or {}).get("finished"), timeout)
        time.sleep(0.2)
    finally:
        c.stop()
        ap.stop()
    sent = [(s, cid, d) for s, cid, t, d in ap.received if t == "COMMAND_LONG" and d["command"] == 401]
    return _diag(store), sent, store


def test_accepted_then_fresh_healthy_sys_status_completes():
    diag, sent, _ = _run_diag()
    assert diag["state"] == "complete"
    assert diag["result_name"] == "ACCEPTED" and diag["ack_from"] == "1/1"
    assert diag["prearm_bit"]["healthy"] is True
    assert diag["texts"] == []
    assert diag["sys_status_after_ack_s"] >= 0
    assert len(sent) == 1
    sysid, compid, d = sent[0]
    assert (sysid, compid) == (247, 25)
    assert (d["target_system"], d["target_component"]) == (1, 1)


def test_accepted_is_not_a_pass_failure_texts_are_collected():
    diag, _, _ = _run_diag({"prearm": "fail", "prearm_texts": ["PreArm: Compass not calibrated",
                                                                 "PreArm: Battery 1 below minimum"]})
    assert diag["state"] == "complete"
    assert diag["result_name"] == "ACCEPTED"
    assert diag["prearm_bit"]["healthy"] is False
    assert "PreArm: Compass not calibrated" in diag["texts"]
    assert "PreArm: Battery 1 below minimum" in diag["texts"]


def test_never_sent_while_armed():
    diag, sent, _ = _run_diag({"armed": True})
    assert diag["state"] == "not_sent" and diag["reason"] == "vehicle armed"
    assert sent == []


def test_passive_mode_never_sends():
    diag, sent, _ = _run_diag(request_messages=False)
    assert diag["state"] == "not_sent"
    assert sent == []


def test_unsupported_ack_is_rejected_not_pass():
    diag, _, _ = _run_diag({"prearm": "unsupported"})
    assert diag["state"] == "rejected" and diag["result_name"] == "UNSUPPORTED"


def test_no_ack_times_out():
    diag, sent, _ = _run_diag({"prearm": "silent"}, prearm_ack_timeout_s=1.0)
    assert diag["state"] == "no_ack"
    assert len(sent) == 1


def test_ack_from_another_system_is_ignored():
    diag, _, _ = _run_diag({"prearm": "imposter"}, prearm_ack_timeout_s=1.0)
    assert diag["state"] == "no_ack"


def test_accepted_without_fresh_sys_status_is_not_complete():
    ap = MockAutopilot().start()
    c, store, _ = _client(ap, prearm_window_s=2.0)
    c.start()
    try:
        assert wait(lambda: store.get("mav.msg.SYS_STATUS") is not None)
        ap.send_sys_status = False
        time.sleep(0.7)
        c.request_prearm_diagnostic()
        assert wait(lambda: (_diag(store) or {}).get("finished"), 8)
    finally:
        c.stop()
        ap.stop()
    assert _diag(store)["state"] == "no_fresh_sys_status"


def test_second_vehicle_never_mixed_into_selected_vehicle():
    diag, _, store = _run_diag({"second_vehicle": True})
    assert store.get("mav.link").value["target_sysid"] == 1
    assert store.get("mav.peer.2.1") is not None
    ss = store.get("mav.msg.SYS_STATUS").value
    assert ss["voltage_battery"] > 15000  # vehicle 2 reports 11 V
    assert store.get("mav.msg.GPS_RAW_INT").value["fix_type"] == 3
    texts = store.get("mav.prearm_texts")
    assert texts is None or not any("Vehicle 2" in t["text"] for t in texts.value)
    assert diag["state"] == "complete" and diag["ack_from"] == "1/1"
    assert not any("Vehicle 2" in t for t in diag["texts"])


def test_chunked_statustext_is_reassembled():
    long = "PreArm: " + "A" * 60 + " " + "B" * 40 + " end"
    ap = MockAutopilot(chunked_text=long).start()
    c, store, _ = _client(ap)
    c.start()
    try:
        assert wait(lambda: store.get("mav.prearm_texts") is not None)
    finally:
        c.stop()
        ap.stop()
    texts = [t["text"] for t in store.get("mav.prearm_texts").value]
    assert texts == [long]


def test_attitude_and_vfr_hud_requested():
    ap = MockAutopilot().start()
    c, store, _ = _client(ap)
    c.start()
    try:
        assert wait(lambda: store.get("mav.msg.ATTITUDE") is not None)
        assert wait(lambda: store.get("mav.msg.VFR_HUD") is not None)
    finally:
        c.stop()
        ap.stop()


# ---------------------------------------------------------------- evaluation


def _prearm(world):
    return world.evaluate()[0]["fc.prearm"]


def test_flight_requires_completed_diagnostic():
    w = World(profile_id="flight").healthy()
    w.store.remove_prefix("mav.prearm_diag")
    r = _prearm(w)
    assert r.status == Status.UNKNOWN
    assert "אבחון" in r.explanation


def test_flight_completed_diagnostic_passes():
    w = World(profile_id="flight").healthy()
    assert _prearm(w).status == Status.PASS


def test_bench_without_diagnostic_uses_sys_status_bit():
    w = World(profile_id="bench").healthy()
    w.store.remove_prefix("mav.prearm_diag")
    assert _prearm(w).status == Status.PASS


def test_stale_diagnostic_does_not_count():
    w = World(profile_id="flight").healthy()
    w.put("mav.prearm_diag", prearm_diag_complete(), age=400)
    assert _prearm(w).status == Status.UNKNOWN


def test_rejected_and_missing_ack_are_unknown():
    for diag, word in (({"state": "rejected", "result": 3, "result_name": "UNSUPPORTED", "finished": True}, "UNSUPPORTED"),
                       ({"state": "rejected", "result": 1, "result_name": "TEMPORARILY_REJECTED", "finished": True},
                        "חמוש"),
                       ({"state": "no_ack", "finished": True}, "COMMAND_ACK"),
                       ({"state": "no_fresh_sys_status", "finished": True}, "SYS_STATUS"),
                       ({"state": "not_sent", "reason": "vehicle armed", "finished": True}, "חמוש"),
                       ({"state": "sent", "finished": False}, "ממתין")):
        w = World(profile_id="flight").healthy()
        w.put("mav.prearm_diag", diag)
        r = _prearm(w)
        assert r.status == Status.UNKNOWN, diag
        assert word in r.explanation, (diag, r.explanation)


def test_diagnostic_failure_texts_fail_even_if_bit_looks_healthy():
    w = World(profile_id="flight").healthy()
    w.put("mav.prearm_diag", prearm_diag_complete(texts=["PreArm: Gyros inconsistent"]))
    r = _prearm(w)
    assert r.status == Status.FAIL
    assert "Gyros" in r.observed


def test_diagnostic_unhealthy_bit_fails():
    w = World(profile_id="bench").healthy()
    w.put("mav.prearm_diag", prearm_diag_complete(healthy=False))
    assert _prearm(w).status == Status.FAIL


def test_disabled_prearm_bit_is_unknown_even_with_diagnostic():
    w = World(profile_id="flight").healthy()
    ss = dict(w.store.get("mav.msg.SYS_STATUS").value)
    ss["onboard_control_sensors_enabled"] &= ~0x10000000
    w.put("mav.msg.SYS_STATUS", ss)
    assert _prearm(w).status == Status.UNKNOWN


def test_second_autopilot_warns_unless_sysid_selected():
    w = World(profile_id="bench").healthy()
    w.put("mav.peer.2.1", {"mavpackettype": "HEARTBEAT", "type": 2, "autopilot": 3, "base_mode": 81,
                           "custom_mode": 0, "system_status": 3, "mavlink_version": 3})
    r = w.evaluate()[0]["fc.identity"]
    assert r.status == Status.WARN and "sysid" in r.explanation
    w.drone.mav_target_sysid = 1
    assert w.evaluate()[0]["fc.identity"].status == Status.PASS
