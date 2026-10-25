"""MAVLink parsing, sentinels, and constants cross-checked against pymavlink."""

from __future__ import annotations

from pymavlink import mavutil
from pymavlink.dialects.v20 import ardupilotmega as m

from x500fs.core.mav import constants as C
from x500fs.core.mav import parse as P
from x500fs.core.observations import ObservationStore
from x500fs.core.session import SessionLog
from x500fs.transport.mav_ingest import MavIngest


def test_sensor_bits_match_pymavlink():
    names = {
        "gyro": "MAV_SYS_STATUS_SENSOR_3D_GYRO", "accel": "MAV_SYS_STATUS_SENSOR_3D_ACCEL",
        "mag": "MAV_SYS_STATUS_SENSOR_3D_MAG", "baro": "MAV_SYS_STATUS_SENSOR_ABSOLUTE_PRESSURE",
        "gps": "MAV_SYS_STATUS_SENSOR_GPS", "rc_receiver": "MAV_SYS_STATUS_SENSOR_RC_RECEIVER",
        "ahrs": "MAV_SYS_STATUS_AHRS", "logging": "MAV_SYS_STATUS_LOGGING",
        "battery": "MAV_SYS_STATUS_SENSOR_BATTERY", "prearm_check": "MAV_SYS_STATUS_PREARM_CHECK",
        "geofence": "MAV_SYS_STATUS_GEOFENCE", "motor_outputs": "MAV_SYS_STATUS_SENSOR_MOTOR_OUTPUTS",
    }
    for ours, theirs in names.items():
        assert C.SENSOR_BITS[ours] == getattr(m, theirs), ours


def test_ekf_flags_match_pymavlink():
    for name, bit in C.EKF_FLAGS.items():
        assert getattr(m, "EKF_" + name.upper()) == bit


def test_copter_modes_match_pymavlink():
    ref = mavutil.mode_mapping_acm
    for num, name in C.COPTER_MODES.items():
        assert ref.get(num) == name, num


def test_misc_constants():
    assert C.MAV_CMD_REQUEST_MESSAGE == m.MAV_CMD_REQUEST_MESSAGE
    assert C.MSG_IDS["AUTOPILOT_VERSION"] == m.MAVLINK_MSG_ID_AUTOPILOT_VERSION
    assert C.MSG_IDS["HOME_POSITION"] == m.MAVLINK_MSG_ID_HOME_POSITION
    assert C.MSG_IDS["EKF_STATUS_REPORT"] == m.MAVLINK_MSG_ID_EKF_STATUS_REPORT
    assert C.MAV_MODE_FLAG_SAFETY_ARMED == m.MAV_MODE_FLAG_SAFETY_ARMED
    assert C.MAV_TYPE_GCS == m.MAV_TYPE_GCS


def test_heartbeat_decoding():
    hb = {"type": 2, "autopilot": 3, "base_mode": 1 | 128, "custom_mode": 5}
    assert P.is_armed(hb) is True
    assert P.copter_mode_name(hb) == "LOITER"
    assert P.is_autopilot_heartbeat(hb)
    assert not P.is_autopilot_heartbeat({"type": 6, "autopilot": 8})
    assert P.is_armed({"base_mode": "x"}) is None
    assert P.copter_mode_name({"base_mode": 0, "custom_mode": 5}) is None  # custom mode flag not set


def test_version_decoding():
    v = P.decode_sw_version((4 << 24) | (7 << 16) | (2 << 8) | 255)
    assert v["text"] == "4.7.2" and v["type"] == "official"
    v = P.decode_sw_version((4 << 24) | (8 << 16) | (0 << 8) | 0)
    assert v["text"].endswith("-dev")
    assert P.decode_sw_version(0) is None
    assert P.decode_custom_version(list(b"51a7f3c1")) == "51a7f3c1"
    assert P.decode_custom_version([0] * 8) is None


def test_battery_sentinels():
    b = P.battery_from_sys_status({"voltage_battery": 65535, "current_battery": -1, "battery_remaining": -1})
    assert b == {"voltage_v": None, "current_a": None, "remaining_pct": None}
    b = P.battery_from_sys_status({"voltage_battery": 16400, "current_battery": 250, "battery_remaining": 80})
    assert b["voltage_v"] == 16.4 and b["current_a"] == 2.5 and b["remaining_pct"] == 80
    b = P.battery_from_sys_status({"voltage_battery": 16400, "current_battery": 250, "battery_remaining": 130})
    assert b["remaining_pct"] is None
    bs = P.battery_from_battery_status({"voltages": [16400] + [65535] * 9, "current_battery": -1,
                                        "battery_remaining": -1, "current_consumed": -1, "temperature": 32767})
    assert bs["voltage_fields_v"] == [16.4]
    assert bs["temperature_c"] is None and bs["consumed_mah"] is None


def test_gps_sentinels():
    g = P.gps_raw({"fix_type": 3, "satellites_visible": 255, "eph": 65535, "epv": 65535, "h_acc": 0})
    assert g["satellites"] is None and g["hdop"] is None and g["vdop"] is None and g["h_acc_m"] is None
    g = P.gps_raw({"fix_type": 3, "satellites_visible": 12, "eph": 87, "epv": 120, "h_acc": 1500})
    assert g["hdop"] == 0.87 and g["h_acc_m"] == 1.5 and g["fix_name"] == "3D_FIX"


def test_param_value_decoding():
    assert P.param_value({"param_id": "ARMING_CHECK", "param_value": 1.0, "param_type": 6}) == ("ARMING_CHECK", 1)
    assert P.param_value({"param_id": "FS_EKF_THRESH", "param_value": 0.8, "param_type": 9}) == ("FS_EKF_THRESH", 0.8)
    assert P.param_value({"param_id": b"BATT_MONITOR\x00\x00\x00\x00", "param_value": 4.0, "param_type": 2}) == ("BATT_MONITOR", 4)
    assert P.param_value({"param_id": "", "param_value": 1.0}) is None
    assert P.param_value({"param_id": "X", "param_value": float("nan")}) is None


def test_statustext_and_prearm_detection():
    s = P.statustext({"severity": 2, "text": "PreArm: GPS not healthy\x00\x00"})
    assert s["text"] == "PreArm: GPS not healthy" and s["severity_name"] == "CRITICAL"
    assert P.is_prearm_text(s["text"])
    assert not P.is_prearm_text("Arming motors")


def test_arming_check_decode():
    assert P.arming_check_decode(0)["disabled"]
    assert P.arming_check_decode(1)["all"]
    d = P.arming_check_decode((1 << 1) | (1 << 2))
    assert d["enabled_bits"] == ["BARO", "COMPASS"]


def test_home_position_unset():
    assert P.home_position({"latitude": 0, "longitude": 0}) is None
    assert P.home_position({"latitude": 320000000, "longitude": 348000000, "altitude": 1000})["lat"] == 32.0


def _ingest(texts=None):
    store = ObservationStore(False)
    ing = MavIngest(store, SessionLog(), simulated=False, endpoint="t", target_sysid=1,
                    on_text=(texts.append if texts is not None else None))
    return ing, store


def test_param_value_clears_only_its_own_missing_marker():
    ing, store = _ingest()
    for name in ("RTL_ALT", "RTL_ALT_M", "MAV_GCS_SYSID", "MAV_GCS_SYSID_HI"):
        ing.mark_param_missing(name)
    for name in ("RTL_ALT", "MAV_GCS_SYSID"):
        ing.handle({"mavpackettype": "PARAM_VALUE", "param_id": name, "param_value": 1.0, "param_type": 9,
                    "param_index": 0, "param_count": 1}, 1, C.MAV_COMP_ID_AUTOPILOT1, t_mono=1.0)
    assert store.get("mav.param_missing.RTL_ALT") is None
    assert store.get("mav.param_missing.MAV_GCS_SYSID") is None
    assert store.get("mav.param_missing.RTL_ALT_M") is not None
    assert store.get("mav.param_missing.MAV_GCS_SYSID_HI") is not None


def _text(ing, text, mid, seq, t):
    ing.handle({"mavpackettype": "STATUSTEXT", "severity": 6, "text": text, "id": mid, "chunk_seq": seq},
               1, C.MAV_COMP_ID_AUTOPILOT1, t_mono=t)


def test_statustext_stale_chunks_expire_and_never_merge_with_reused_id():
    texts = []
    ing, _ = _ingest(texts)
    _text(ing, "A" * 50, 7, 0, 10.0)              # first chunk; the rest never arrives
    _text(ing, "B" * 50, 7, 0, 20.0)              # id 7 reused much later
    _text(ing, "end", 7, 1, 20.1)
    assert [t["text"] for t in texts] == ["B" * 50 + "end"]
    _text(ing, "ok" * 25, 8, 0, 21.0)             # chunks within the window still reassemble
    _text(ing, "!", 8, 1, 22.5)
    assert texts[-1]["text"] == "ok" * 25 + "!"
    for i in range(100):                          # unfinished messages are capped
        _text(ing, "X" * 50, 100 + i, 0, 30.0)
    assert len(ing._chunks) <= 16 and len(ing._chunk_t) == len(ing._chunks)
