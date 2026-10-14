"""Check evaluation and overall status."""

from __future__ import annotations

import copy

from helpers import World, flight_complete_profile, msgs_healthy

from x500fs.core import profile as profile_mod
from x500fs.core.status import CheckResult, Overall, Status, aggregate


def test_every_check_has_unique_id_and_category():
    from x500fs.core.checks import CATEGORIES, REGISTRY

    ids = [d.id for d in REGISTRY]
    assert len(ids) == len(set(ids))
    assert all(d.category in CATEGORIES for d in REGISTRY)
    assert len(ids) >= 40


def test_builtin_profiles_cover_every_check():
    from x500fs.core.checks import REGISTRY

    for pid in profile_mod.BUILTIN_IDS:
        p = profile_mod.load_builtin(pid)
        missing = [d.id for d in REGISTRY if d.id not in p.checks]
        assert not missing, f"{pid} missing {missing}"


def test_empty_store_is_never_pass_and_overall_incomplete():
    w = World(profile_id="flight")
    res, overall = w.evaluate()
    assert overall.status is Overall.INCOMPLETE
    passing = [r for r in res.values() if r.status is Status.PASS]
    # Nothing was observed, so nothing may pass.
    assert passing == [], [r.check_id for r in passing]


def test_healthy_bench_passes_with_only_expected_warnings():
    w = World().healthy()
    res, overall = w.evaluate()
    assert overall.status in (Overall.PASS, Overall.PASS_WITH_WARNINGS)
    for cid in ("link.mavlink_heartbeat", "fc.armed", "fc.prearm", "fc.arming_checks", "gps.fix",
                "ekf.status", "pi.temperature", "pi.power_now", "pi.mavlink_router", "link.path"):
        assert res[cid].status is Status.PASS, (cid, res[cid].explanation)


def test_flight_profile_unconfigured_critical_blocks_pass():
    w = World(profile_id="flight").healthy()
    res, overall = w.evaluate()
    # expected params and battery cell count are not configured in the shipped profile
    assert res["cfg.failsafe"].status is Status.UNKNOWN and not res["cfg.failsafe"].configured
    assert res["batt.cells_config"].status is Status.UNKNOWN
    assert overall.status is Overall.INCOMPLETE
    assert "cfg.failsafe" in overall.blocking and "batt.cells_config" in overall.blocking


def test_flight_profile_fully_configured_can_pass():
    w = World(profile_id="flight").healthy()
    w.profile = profile_mod.from_dict(flight_complete_profile(), "test")
    w.put("mav.peer.255.190", {"mavpackettype": "HEARTBEAT", "type": 6, "autopilot": 8, "base_mode": 0,
                               "custom_mode": 0})
    res, overall = w.evaluate()
    not_ok = {k: (r.status.value, r.explanation) for k, r in res.items()
              if r.critical and r.status not in (Status.PASS, Status.NOT_APPLICABLE)}
    assert not not_ok, not_ok
    assert overall.status in (Overall.PASS, Overall.PASS_WITH_WARNINGS)


def test_expected_param_mismatch_fails():
    w = World(profile_id="flight").healthy()
    doc = flight_complete_profile()
    doc["expected_params"]["failsafe"]["FS_THR_ENABLE"] = {"eq": 0}
    w.profile = profile_mod.from_dict(doc, "test")
    res, overall = w.evaluate()
    assert res["cfg.failsafe"].status is Status.FAIL
    assert overall.status is Overall.FAIL


def test_expected_param_not_read_is_unknown_not_pass():
    w = World(profile_id="flight").healthy()
    doc = flight_complete_profile()
    doc["expected_params"]["rtl"] = {"RTL_ALT_M": {"min": 15}}
    w.profile = profile_mod.from_dict(doc, "test")
    res, _ = w.evaluate()
    assert res["cfg.rtl"].status is Status.UNKNOWN


def test_stale_heartbeat_is_fail_and_dependent_checks_unknown():
    w = World().healthy()
    w.put("mav.msg.HEARTBEAT", msgs_healthy()["HEARTBEAT"], age=10)
    res, _ = w.evaluate()
    assert res["link.mavlink_heartbeat"].status is Status.FAIL
    assert res["fc.armed"].status is Status.UNKNOWN
    assert res["fc.mode"].status is Status.UNKNOWN


def test_stale_telemetry_is_unknown():
    w = World().healthy()
    w.put("mav.msg.SYS_STATUS", msgs_healthy()["SYS_STATUS"], age=30)
    res, _ = w.evaluate()
    for cid in ("fc.prearm", "fc.sensors", "batt.voltage", "link.telemetry"):
        assert res[cid].status is Status.UNKNOWN, cid
        assert res[cid].status is not Status.PASS


def test_missing_message_reports_missing_evidence():
    w = World().healthy()
    w.store.remove_prefix("mav.msg.GPS_RAW_INT")
    res, _ = w.evaluate()
    assert res["gps.fix"].status is Status.UNKNOWN
    assert "GPS_RAW_INT" in res["gps.fix"].missing


def test_unsupported_request_is_explained():
    w = World(profile_id="flight").healthy()
    w.store.remove_prefix("mav.msg.HOME_POSITION")
    w.put("mav.unsupported.HOME_POSITION", "UNSUPPORTED")
    res, _ = w.evaluate()
    assert res["nav.home"].status is Status.UNKNOWN
    assert "UNSUPPORTED" in res["nav.home"].explanation


def test_malformed_fields_never_pass():
    w = World().healthy()
    bad = dict(msgs_healthy()["SYS_STATUS"])
    bad["onboard_control_sensors_health"] = "garbage"
    bad["voltage_battery"] = None
    w.put("mav.msg.SYS_STATUS", bad)
    w.put("mav.msg.GPS_RAW_INT", {"mavpackettype": "GPS_RAW_INT", "fix_type": "3D"})
    w.put("mav.msg.HEARTBEAT", {"mavpackettype": "HEARTBEAT", "base_mode": [1, 2]})
    res, _ = w.evaluate()
    for cid in ("fc.prearm", "fc.sensors", "batt.voltage", "gps.fix", "fc.armed"):
        assert res[cid].status is Status.UNKNOWN, (cid, res[cid].status, res[cid].explanation)


def test_malformed_collector_section_is_unknown():
    w = World().healthy()
    doc = copy.deepcopy(w.store.get("pi.collector").value)
    doc["sections"]["thermal"] = {"ok": True, "data": {"cpu_temp_c": "hot"}}
    doc["sections"]["power"] = {"ok": False, "data": None, "error": "PermissionError: denied"}
    doc["sections"]["cpu"] = "not a dict"
    w.put("pi.collector", doc)
    res, _ = w.evaluate()
    assert res["pi.temperature"].status is Status.UNKNOWN
    assert res["pi.power_now"].status is Status.UNKNOWN
    assert "PermissionError" in res["pi.power_now"].explanation
    assert res["pi.cpu"].status is Status.UNKNOWN


def test_stale_collector_data_is_unknown():
    w = World().healthy()
    w.put("pi.collector", w.store.get("pi.collector").value, age=600)
    res, _ = w.evaluate()
    assert res["pi.temperature"].status is Status.UNKNOWN
    assert "ישנים" in res["pi.temperature"].explanation


def test_check_exception_becomes_unknown(monkeypatch):
    from x500fs.core.checks import base

    def boom(ctx, cfg):
        raise ZeroDivisionError("x")

    defs = [d if d.id != "pi.cpu" else base.CheckDef(d.id, d.category, d.title, boom) for d in base.REGISTRY]
    monkeypatch.setattr(base, "REGISTRY", defs)
    w = World().healthy()
    res, _ = w.evaluate()
    assert res["pi.cpu"].status is Status.UNKNOWN


def test_disabled_check_is_not_applicable():
    w = World().healthy()
    res, _ = w.evaluate()
    assert res["gps.satellites"].status is Status.NOT_APPLICABLE
    assert res["gps.satellites"].critical is False


def test_aggregate_rules():
    def r(cid, st, crit, configured=True):
        return CheckResult(cid, "fc", cid, st, crit, "x", configured=configured)

    assert aggregate([]).status is Overall.NOT_RUN
    assert aggregate([r("a", Status.PASS, True)]).status is Overall.PASS
    assert aggregate([r("a", Status.PASS, True), r("b", Status.WARN, False)]).status is Overall.PASS_WITH_WARNINGS
    assert aggregate([r("a", Status.PASS, True), r("b", Status.UNKNOWN, False)]).status is Overall.PASS_WITH_WARNINGS
    assert aggregate([r("a", Status.PASS, True), r("b", Status.UNKNOWN, True)]).status is Overall.INCOMPLETE
    assert aggregate([r("a", Status.UNKNOWN, True), r("b", Status.FAIL, True)]).status is Overall.FAIL
    assert aggregate([r("a", Status.NOT_APPLICABLE, True)]).status is Overall.INCOMPLETE
    # a buggy check that claims PASS while unconfigured cannot produce an overall pass
    assert aggregate([r("a", Status.PASS, True, configured=False)]).status is Overall.INCOMPLETE


def test_armed_vehicle_fails_preflight():
    w = World().healthy()
    hb = dict(msgs_healthy()["HEARTBEAT"])
    hb["base_mode"] = 81 | 128
    w.put("mav.msg.HEARTBEAT", hb)
    res, overall = w.evaluate()
    assert res["fc.armed"].status is Status.FAIL
    assert overall.status is Overall.FAIL


def test_ekf_variance_thresholds_from_fc_param():
    w = World(profile_id="flight").healthy()
    e = dict(msgs_healthy()["EKF_STATUS_REPORT"])
    e["compass_variance"] = 0.6
    w.put("mav.msg.EKF_STATUS_REPORT", e)
    res, _ = w.evaluate()
    assert res["ekf.status"].status is Status.WARN
    e["compass_variance"] = 0.85  # FS_EKF_THRESH = 0.8 in SIM_PARAMS
    w.put("mav.msg.EKF_STATUS_REPORT", e)
    res, _ = w.evaluate()
    assert res["ekf.status"].status is Status.FAIL


def test_ekf_const_pos_mode_forbidden_in_flight():
    w = World(profile_id="flight").healthy()
    e = dict(msgs_healthy()["EKF_STATUS_REPORT"])
    e["flags"] = 1 | 2 | 4 | 32 | 128
    w.put("mav.msg.EKF_STATUS_REPORT", e)
    res, _ = w.evaluate()
    assert res["ekf.status"].status is Status.FAIL


def test_hdop_threshold_uses_gps_hdop_good_scaled():
    w = World(profile_id="flight").healthy()
    g = dict(msgs_healthy()["GPS_RAW_INT"])
    g["eph"] = 150  # HDOP 1.5 > GPS_HDOP_GOOD 140 (=1.4)
    w.put("mav.msg.GPS_RAW_INT", g)
    res, _ = w.evaluate()
    assert res["gps.hdop"].status is Status.FAIL
    g["eph"] = 130
    w.put("mav.msg.GPS_RAW_INT", g)
    res, _ = w.evaluate()
    assert res["gps.hdop"].status is Status.PASS


def test_hdop_without_fc_param_is_unconfigured():
    params = {k: v for k, v in __import__("x500fs.sim.simulator", fromlist=["SIM_PARAMS"]).SIM_PARAMS.items()
              if k != "GPS_HDOP_GOOD"}
    w = World(profile_id="flight").healthy(params=params)
    res, _ = w.evaluate()
    assert res["gps.hdop"].status is Status.UNKNOWN
    assert not res["gps.hdop"].configured


def test_satellite_threshold_not_invented():
    w = World().healthy()
    w.set_profile()
    p = w.profile
    p.checks["gps.satellites"] = {"enabled": True, "critical": False, "min_sats": None}
    res, _ = w.evaluate()
    assert res["gps.satellites"].status is Status.UNKNOWN
    assert res["gps.satellites"].configured is False


def test_sensor_unhealthy_required_fails():
    w = World().healthy()
    ss = dict(msgs_healthy()["SYS_STATUS"])
    ss["onboard_control_sensors_health"] &= ~0x4  # compass
    w.put("mav.msg.SYS_STATUS", ss)
    res, _ = w.evaluate()
    assert res["fc.sensors"].status is Status.FAIL


def test_pi_service_unconfigured_is_unknown_with_discovered_hint():
    w = World().healthy()
    w.drone.services = {"mavlink_router": "mavlink-router"}
    res, _ = w.evaluate()
    r = res["pi.monitors"]
    assert r.status is Status.UNKNOWN and not r.configured
    r = res["pi.blackbox"]
    assert r.status is Status.UNKNOWN and not r.configured
    assert "x500-blackbox.service" in r.explanation


def test_pi_service_inactive_fails_and_restarts_warn():
    w = World().healthy()
    doc = copy.deepcopy(w.store.get("pi.collector").value)
    doc["sections"]["services"]["data"]["units"]["mavlink-router"].update({"ActiveState": "failed"})
    doc["sections"]["services"]["data"]["units"]["x500-health-monitor"].update({"NRestarts": "3"})
    w.put("pi.collector", doc)
    res, _ = w.evaluate()
    assert res["pi.mavlink_router"].status is Status.FAIL
    assert res["pi.monitors"].status is Status.WARN


def _wd(w, **systemd):
    doc = copy.deepcopy(w.store.get("pi.collector").value)
    wd = doc["sections"]["watchdog"]["data"]
    wd["systemd"].update(systemd)
    return doc, wd


def test_watchdog_configured_and_active():
    w = World().healthy()
    res, _ = w.evaluate()
    assert res["pi.watchdog"].status is Status.PASS


def test_watchdog_configured_without_runtime_proof_is_not_pass():
    w = World().healthy()
    doc, wd = _wd(w)
    wd["last_ping_age_s"] = None
    for d in wd["devices"]:
        d.pop("state", None)
    w.put("pi.collector", doc)
    res, _ = w.evaluate()
    assert res["pi.watchdog"].status is Status.WARN
    assert "הוכחת ריצה" in res["pi.watchdog"].explanation


def test_watchdog_stale_ping_and_wrong_config():
    w = World().healthy()
    doc, wd = _wd(w)
    wd["last_ping_age_s"] = 120.0
    for d in wd["devices"]:
        d["state"] = "inactive"
    w.put("pi.collector", doc)
    res, _ = w.evaluate()
    assert res["pi.watchdog"].status is Status.WARN
    doc, wd = _wd(w, RuntimeWatchdogUSec="0")
    w.put("pi.collector", doc)
    res, _ = w.evaluate()
    assert res["pi.watchdog"].status is Status.FAIL
    doc, wd = _wd(w, RuntimeWatchdogUSec="30s")
    w.put("pi.collector", doc)
    res, _ = w.evaluate()
    assert res["pi.watchdog"].status is Status.WARN


def test_blackbox_needs_service_and_recent_valid_sample():
    w = World().healthy()
    res, _ = w.evaluate()
    assert res["pi.blackbox"].status is Status.PASS
    doc = copy.deepcopy(w.store.get("pi.collector").value)
    doc["sections"]["blackbox"]["data"]["samples"]["last_valid_age_s"] = 3600
    w.put("pi.collector", doc)
    res, _ = w.evaluate()
    assert res["pi.blackbox"].status is Status.FAIL
    # a fresh file mtime alone is not enough: no valid rows -> FAIL
    doc["sections"]["blackbox"]["data"]["samples"].update({"valid_rows": 0, "last_valid_age_s": None,
                                                           "invalid_examples": [["bad timestamp"]]})
    doc["sections"]["blackbox"]["data"]["newest"]["mtime_age_s"] = 1
    w.put("pi.collector", doc)
    res, _ = w.evaluate()
    assert res["pi.blackbox"].status is Status.FAIL
    # samples fine but service stopped -> FAIL
    doc = copy.deepcopy(w.store.get("pi.collector").value)
    doc["sections"]["blackbox"]["data"]["samples"].update({"valid_rows": 10, "last_valid_age_s": 3})
    doc["sections"]["services"]["data"]["units"]["x500-blackbox"]["ActiveState"] = "inactive"
    w.put("pi.collector", doc)
    res, _ = w.evaluate()
    assert res["pi.blackbox"].status is Status.FAIL
    assert "retention" in res["pi.blackbox"].limitations


def test_power_flags_and_limitation_text():
    w = World().healthy()
    doc = copy.deepcopy(w.store.get("pi.collector").value)
    doc["sections"]["power"]["data"]["throttled"] = 0x50005
    w.put("pi.collector", doc)
    res, _ = w.evaluate()
    assert res["pi.power_now"].status is Status.FAIL
    assert res["pi.power_history"].status is Status.WARN
    assert "קפיצות מתח" in res["pi.power_now"].limitations


def test_camera_missing_is_warning_unless_video_required():
    w = World().healthy()
    doc = copy.deepcopy(w.store.get("pi.collector").value)
    doc["sections"]["camera"]["data"]["cameras"] = []
    w.put("pi.collector", doc)
    res, _ = w.evaluate()
    assert res["pi.camera"].status is Status.WARN
    w.profile.video_required = True
    res, _ = w.evaluate()
    assert res["pi.camera"].status is Status.FAIL


def _with_section(w, name, **changes):
    doc = copy.deepcopy(w.store.get("pi.collector").value)
    doc["sections"][name]["data"].update(changes)
    w.put("pi.collector", doc)


def test_storage_small_boot_partition_uses_percent_only():
    # Real Pi 5: /boot/firmware is ~512 MB and 13% used - must not fail the 512 MB free-space floor.
    w = World().healthy()
    _with_section(w, "storage", mounts=[
        {"path": "/", "total_mb": 58000.0, "free_mb": 46600.0},
        {"path": "/boot/firmware", "total_mb": 510.0, "free_mb": 445.0}])
    res, _ = w.evaluate()
    r = res["pi.storage"]
    assert r.status is Status.PASS, r.explanation
    assert "/boot/firmware" in r.expected and "445 MB" in r.observed
    # the percentage thresholds still apply to it
    _with_section(w, "storage", mounts=[{"path": "/boot/firmware", "total_mb": 510.0, "free_mb": 15.0}])
    res, _ = w.evaluate()
    assert res["pi.storage"].status is Status.FAIL
    # and the MB floor still applies to the root filesystem
    _with_section(w, "storage", mounts=[{"path": "/", "total_mb": 58000.0, "free_mb": 400.0}])
    res, _ = w.evaluate()
    assert res["pi.storage"].status is Status.FAIL


def test_storage_percent_only_default_for_old_user_profile():
    # A user copy of the profile written before percent_only_paths existed still gets the boot exemption.
    w = World().healthy()
    doc = profile_mod.builtin_doc("bench")
    doc["checks"]["pi.storage"].pop("percent_only_paths")
    w.profile = profile_mod.from_dict(doc, "old-user-copy")
    _with_section(w, "storage", mounts=[{"path": "/boot/firmware", "total_mb": 510.0, "free_mb": 445.0}])
    res, _ = w.evaluate()
    assert res["pi.storage"].status is Status.PASS


def test_tools_not_configured_is_not_applicable():
    w = World().healthy()
    res, _ = w.evaluate()
    assert res["pi.tools"].status is Status.NOT_APPLICABLE


def test_blackbox_header_difference_is_described():
    w = World().healthy()
    samples = copy.deepcopy(w.store.get("pi.collector").value["sections"]["blackbox"]["data"]["samples"])
    samples.update(header_ok=False, header_seen=["timestamp", "temp_c", "throttled", "ram_pct"])
    _with_section(w, "blackbox", samples=samples)
    res, _ = w.evaluate()
    r = res["pi.blackbox"]
    assert r.status is Status.WARN
    assert "חסרות עמודות" in r.explanation and "mavlink" in r.explanation
    samples.update(header_seen=["2026-10-14T18:00:00+03:00", "46.6", "0x0"])
    _with_section(w, "blackbox", samples=samples)
    res, _ = w.evaluate()
    assert "אינה כותרת" in res["pi.blackbox"].explanation


def test_camera_missing_hint_from_kernel_and_permissions():
    w = World().healthy()
    base = {"cameras": [], "note": "No cameras available", "exit_code": 0}
    _with_section(w, "camera", **base, kernel_sensor={"driver": "imx219", "driver_loaded": False, "devices": []},
                  in_video_group=True, media_access=True)
    res, _ = w.evaluate()
    assert res["pi.camera"].status is Status.WARN
    assert "לא טען את מנהל ההתקן" in res["pi.camera"].explanation
    _with_section(w, "camera", **base, in_video_group=False)
    res, _ = w.evaluate()
    assert "הרשאה" in res["pi.camera"].explanation
    _with_section(w, "camera", **base, in_video_group=True, media_access=True,
                  kernel_sensor={"driver": "imx219", "driver_loaded": True, "devices": ["4-0010"]})
    res, _ = w.evaluate()
    assert "הקרנל זיהה" in res["pi.camera"].explanation


def test_unread_param_explains_pending_or_no_answer():
    w = World().healthy(params={})
    res, _ = w.evaluate()
    assert "אוטומטית" in res["fc.arming_checks"].explanation
    w.put("mav.param_missing.ARMING_CHECK", True)
    w.put("mav.param_missing.ARMING_SKIPCHK", True)
    res, _ = w.evaluate()
    assert "לא ענה" in res["fc.arming_checks"].explanation
