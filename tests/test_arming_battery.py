"""Disabled pre-arm checks, ArduPilot 4.7 renames, unknown battery readings."""

from __future__ import annotations

from helpers import SENSORS_OK, World, msgs_healthy

from x500fs.core import profile as profile_mod
from x500fs.core.status import Overall, Status
from x500fs.sim.simulator import SIM_PARAMS


def params(**changes):
    p = dict(SIM_PARAMS)
    for k, v in changes.items():
        if v is None:
            p.pop(k, None)
        else:
            p[k] = v
    return p


def test_arming_check_zero_fails_and_prearm_is_not_trusted():
    w = World().healthy(params=params(ARMING_CHECK=0))
    res, overall = w.evaluate()
    assert res["fc.arming_checks"].status is Status.FAIL
    assert res["fc.prearm"].status is Status.UNKNOWN
    assert overall.status is Overall.FAIL


def test_arming_check_partial_bitmask_fails_unless_allowed():
    # every check except RC (bit 6)
    value = sum(1 << b for b in range(1, 20) if b not in (6, 9))  # what a Copter GCS would set
    w = World().healthy(params=params(ARMING_CHECK=value))
    res, _ = w.evaluate()
    assert res["fc.arming_checks"].status is Status.FAIL
    assert "RC" in res["fc.arming_checks"].explanation
    w.profile.checks["fc.arming_checks"]["allowed_skipped"] = ["RC"]
    res, _ = w.evaluate()
    assert res["fc.arming_checks"].status is Status.WARN


def test_arming_skipchk_47_semantics():
    p = params(ARMING_CHECK=None, ARMING_SKIPCHK=0)
    w = World().healthy(params=p)
    res, _ = w.evaluate()
    assert res["fc.arming_checks"].status is Status.PASS
    w.put("mav.param.ARMING_SKIPCHK", {"value": 1 << 6})  # skip RC
    res, _ = w.evaluate()
    assert res["fc.arming_checks"].status is Status.FAIL
    w.profile.checks["fc.arming_checks"]["allowed_skipped"] = ["RC"]
    res, _ = w.evaluate()
    assert res["fc.arming_checks"].status is Status.WARN
    w.put("mav.param.ARMING_SKIPCHK", {"value": -1})
    res, _ = w.evaluate()
    assert res["fc.arming_checks"].status is Status.FAIL


def test_arming_param_missing_is_unknown():
    w = World().healthy(params=params(ARMING_CHECK=None))
    res, _ = w.evaluate()
    assert res["fc.arming_checks"].status is Status.UNKNOWN


def test_prearm_failure_reports_messages():
    w = World().healthy()
    ss = dict(msgs_healthy()["SYS_STATUS"])
    ss["onboard_control_sensors_health"] = SENSORS_OK & ~0x10000000
    w.put("mav.msg.SYS_STATUS", ss)
    import time
    w.put("mav.prearm_texts", [{"text": "PreArm: Compass not calibrated", "t_mono": time.monotonic()}])
    res, _ = w.evaluate()
    assert res["fc.prearm"].status is Status.FAIL
    assert "Compass" in res["fc.prearm"].observed


def test_prearm_not_reported_by_firmware_is_unknown():
    w = World().healthy()
    ss = dict(msgs_healthy()["SYS_STATUS"])
    for k in ("onboard_control_sensors_present", "onboard_control_sensors_enabled", "onboard_control_sensors_health"):
        ss[k] = SENSORS_OK & ~0x10000000
    w.put("mav.msg.SYS_STATUS", ss)
    res, _ = w.evaluate()
    assert res["fc.prearm"].status is Status.UNKNOWN
    assert "PreArm" in res["fc.prearm"].limitations


def test_prearm_enabled_bit_clear_is_unknown():
    w = World().healthy()
    ss = dict(msgs_healthy()["SYS_STATUS"])
    ss["onboard_control_sensors_enabled"] = SENSORS_OK & ~0x10000000
    w.put("mav.msg.SYS_STATUS", ss)
    res, _ = w.evaluate()
    assert res["fc.prearm"].status is Status.UNKNOWN


def test_absence_of_prearm_text_does_not_pass_when_bit_unhealthy():
    w = World().healthy()
    ss = dict(msgs_healthy()["SYS_STATUS"])
    ss["onboard_control_sensors_health"] = SENSORS_OK & ~0x10000000
    w.put("mav.msg.SYS_STATUS", ss)
    res, _ = w.evaluate()
    assert res["fc.prearm"].status is Status.FAIL


def test_unknown_battery_readings():
    w = World().healthy()
    ss = dict(msgs_healthy()["SYS_STATUS"])
    ss.update({"voltage_battery": 65535, "current_battery": -1, "battery_remaining": -1})
    w.put("mav.msg.SYS_STATUS", ss)
    res, _ = w.evaluate()
    for cid in ("batt.voltage", "batt.current", "batt.remaining"):
        assert res[cid].status is Status.UNKNOWN, cid


def test_zero_voltage_is_unknown_not_pass():
    w = World().healthy()
    ss = dict(msgs_healthy()["SYS_STATUS"])
    ss["voltage_battery"] = 0
    w.put("mav.msg.SYS_STATUS", ss)
    res, _ = w.evaluate()
    assert res["batt.voltage"].status is Status.UNKNOWN


def test_battery_thresholds_from_fc_and_cell_plausibility():
    doc = profile_mod.builtin_doc("flight")
    doc["battery"].update({"chemistry": "LiPo", "cells": 4})
    w = World(profile_id="flight").healthy()
    w.profile = profile_mod.from_dict(doc, "t")
    res, _ = w.evaluate()
    assert res["batt.voltage"].status is Status.PASS  # 16.4V, low 14.0, crt 13.2
    ss = dict(msgs_healthy()["SYS_STATUS"])
    ss["voltage_battery"] = 13900
    w.put("mav.msg.SYS_STATUS", ss)
    res, _ = w.evaluate()
    assert res["batt.voltage"].status is Status.WARN
    ss["voltage_battery"] = 13100
    w.put("mav.msg.SYS_STATUS", ss)
    res, _ = w.evaluate()
    assert res["batt.voltage"].status is Status.FAIL
    # 3S pack reading with profile saying 4S -> implausible per-cell voltage
    ss["voltage_battery"] = 11000
    w.put("mav.msg.SYS_STATUS", ss)
    res, _ = w.evaluate()
    assert res["batt.voltage"].status is Status.FAIL
    assert "תאים" in res["batt.voltage"].explanation


def test_battery_thresholds_disabled_means_unconfigured():
    w = World().healthy(params=params(BATT_LOW_VOLT=0, BATT_CRT_VOLT=0))
    res, _ = w.evaluate()
    assert res["batt.voltage"].status is Status.UNKNOWN
    assert not res["batt.voltage"].configured


def test_battery_monitor_disabled_fails():
    w = World().healthy(params=params(BATT_MONITOR=0))
    res, _ = w.evaluate()
    assert res["batt.monitor"].status is Status.FAIL


def test_gcs_identity_47_range_and_enforce():
    w = World().healthy(params=params(SYSID_MYGCS=None, MAV_GCS_SYSID=240, MAV_GCS_SYSID_HI=250))
    res, _ = w.evaluate()
    assert res["cfg.gcs_identity"].status is Status.WARN  # app sysid 247 inside 240..250
    w = World().healthy(params=params(SYSID_ENFORCE=None, MAV_OPTIONS=1))
    res, _ = w.evaluate()
    assert res["cfg.gcs_identity"].status is Status.WARN
    w = World().healthy()
    res, _ = w.evaluate()
    assert res["cfg.gcs_identity"].status is Status.PASS
