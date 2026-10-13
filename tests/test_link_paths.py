"""Bench vs flight requirements and transport-specific link checks."""

from __future__ import annotations

import copy

from helpers import World

from x500fs.core.status import Overall, Status


def _lte_absent(w):
    doc = copy.deepcopy(w.store.get("pi.collector").value)
    doc["sections"]["lte"]["data"].update({"usb_modems": [], "interfaces": [], "ping": None})
    w.put("pi.collector", doc)


def test_wifi_bench_does_not_fail_when_lte_absent():
    w = World().healthy("wifi")
    _lte_absent(w)
    res, overall = w.evaluate()
    assert res["link.lte"].status is Status.NOT_APPLICABLE
    assert overall.status is not Overall.FAIL


def test_lte_selected_requires_modem():
    w = World(profile_id="flight").healthy("lte")
    _lte_absent(w)
    res, _ = w.evaluate()
    assert res["link.lte"].status is Status.FAIL
    assert res["link.lte"].critical


def test_lte_selected_with_modem_and_ping_passes():
    w = World(profile_id="flight").healthy("lte")
    res, _ = w.evaluate()
    assert res["link.lte"].status is Status.PASS
    assert res["link.path"].status is Status.PASS, res["link.path"].explanation


def test_lte_selected_but_traffic_over_wifi_fails_path_check():
    w = World(profile_id="flight").healthy("lte")
    doc = copy.deepcopy(w.store.get("pi.collector").value)
    doc["sections"]["network"]["data"]["tailscale"]["underlay_kind"] = "wifi"
    doc["sections"]["network"]["data"]["tailscale"]["underlay_dev"] = "wlan0"
    w.put("pi.collector", doc)
    res, _ = w.evaluate()
    assert res["link.path"].status is Status.FAIL


def test_lte_selected_unknown_underlay_is_unknown():
    w = World(profile_id="flight").healthy("lte")
    doc = copy.deepcopy(w.store.get("pi.collector").value)
    doc["sections"]["network"]["data"]["tailscale"]["underlay_kind"] = None
    w.put("pi.collector", doc)
    res, _ = w.evaluate()
    assert res["link.path"].status is Status.UNKNOWN


def test_wifi_selected_but_connected_via_tailscale_fails():
    w = World(profile_id="flight").healthy("tailscale")
    w.drone.link_path = "wifi"
    res, _ = w.evaluate()
    assert res["link.path"].status is Status.FAIL


def test_ping_success_does_not_make_mavlink_pass():
    w = World().healthy("lte")
    w.store.remove_prefix("mav.msg.HEARTBEAT")
    res, _ = w.evaluate()
    assert res["link.lte"].status in (Status.NOT_APPLICABLE, Status.PASS)
    assert res["link.mavlink_heartbeat"].status is Status.UNKNOWN


def test_rtt_thresholds_are_per_path():
    w = World().healthy("lte")
    w.session.rtt.samples_ms.clear()
    for _ in range(5):
        w.session.rtt.add(300)  # fine for LTE (warn 400), bad for Wi-Fi (warn 100)
    res, _ = w.evaluate()
    assert res["link.rtt"].status is Status.PASS
    w.drone.link_path = "wifi"
    res, _ = w.evaluate()
    assert res["link.rtt"].status is Status.WARN


def test_rtt_all_timeouts_fails():
    w = World().healthy()
    w.session.rtt.samples_ms.clear()
    w.session.rtt.timeouts = 4
    res, _ = w.evaluate()
    assert res["link.rtt"].status is Status.FAIL


def test_interruptions_counted_and_ongoing_warns():
    w = World().healthy()
    res, _ = w.evaluate()
    assert res["link.interruptions"].status is Status.PASS
    w.session.link_down("mavlink", "never up")  # not an interruption: the link was never up
    assert w.session.interruptions() == []
    w.session.link_up("mavlink")
    w.session.link_up("ssh")
    w.session.link_down("mavlink", "test")
    res, _ = w.evaluate()
    assert res["link.interruptions"].status is Status.WARN
    w.session.link_up("mavlink")
    for i in range(3):
        w.session.link_down("ssh", f"x{i}")
        w.session.link_up("ssh")
    res, _ = w.evaluate()
    assert res["link.interruptions"].status is Status.FAIL


def test_gcs_peer_detection_flight():
    w = World(profile_id="flight").healthy()
    res, _ = w.evaluate()
    assert res["link.gcs_peer"].status is Status.WARN
    w.put("mav.peer.255.190", {"mavpackettype": "HEARTBEAT", "type": 6, "autopilot": 8})
    res, _ = w.evaluate()
    assert res["link.gcs_peer"].status is Status.PASS
