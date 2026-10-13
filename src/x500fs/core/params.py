"""Parameters the app reads (PARAM_REQUEST_READ only - never PARAM_SET).

Names that do not exist on the detected firmware simply never answer; they
are then reported as missing (never assumed). Some names changed between
ArduPilot releases, so both spellings are requested where relevant.
"""

from __future__ import annotations

PARAM_GROUPS: dict[str, list[str]] = {
    "failsafe": [
        "FS_THR_ENABLE", "FS_THR_VALUE", "FS_GCS_ENABLE", "FS_GCS_TIMEOUT",
        "FS_EKF_ACTION", "FS_EKF_THRESH", "FS_CRASH_CHECK", "FS_OPTIONS",
        "BATT_FS_LOW_ACT", "BATT_FS_CRT_ACT", "BATT_LOW_VOLT", "BATT_CRT_VOLT",
        "BATT_LOW_MAH", "BATT_CRT_MAH", "BATT_FS_VOLTSRC", "BATT_LOW_TIMER",
    ],
    "rtl": [
        # RTL_ALT (cm, <= 4.6) became RTL_ALT_M (m) in 4.7. Other RTL distance/speed
        # names also moved to metre-based names in 4.7; add those to the profile's
        # expected_params if you rely on them - every name listed there is read.
        "RTL_ALT", "RTL_ALT_M", "RTL_ALT_FINAL", "RTL_CLIMB_MIN", "RTL_SPEED", "RTL_LOIT_TIME",
        "RTL_CONE_SLOPE", "RTL_ALT_TYPE", "RTL_OPTIONS",
    ],
    "fence": [
        "FENCE_ENABLE", "FENCE_TYPE", "FENCE_ACTION", "FENCE_ALT_MAX",
        "FENCE_ALT_MIN", "FENCE_RADIUS", "FENCE_MARGIN",
    ],
    "arming": ["ARMING_CHECK", "ARMING_SKIPCHK", "ARMING_RUDDER", "ARMING_OPTIONS"],
    "gps": ["GPS_HDOP_GOOD", "GPS_TYPE", "GPS1_TYPE"],
    "ekf": ["FS_EKF_THRESH", "AHRS_EKF_TYPE"],
    "battery": [
        "BATT_MONITOR", "BATT_CAPACITY", "BATT_ARM_VOLT", "BATT_ARM_MAH",
        "BATT_VOLT_MULT", "BATT_AMP_PERVLT", "BATT_AMP_OFFSET", "BATT_VOLT_PIN", "BATT_CURR_PIN",
    ],
    # 4.7 renames: SYSID_MYGCS -> MAV_GCS_SYSID (+ MAV_GCS_SYSID_HI range),
    # SYSID_THISMAV -> MAV_SYSID, SYSID_ENFORCE -> MAV_OPTIONS bit 0.
    "gcs": ["SYSID_THISMAV", "MAV_SYSID", "SYSID_MYGCS", "MAV_GCS_SYSID", "MAV_GCS_SYSID_HI",
            "SYSID_ENFORCE", "MAV_OPTIONS"],
    "logging": ["LOG_BACKEND_TYPE", "LOG_DISARMED", "LOG_BITMASK"],
    "frame": ["FRAME_CLASS", "FRAME_TYPE"],
}


def all_params(extra: list[str] | None = None) -> list[str]:
    names: list[str] = []
    for group in PARAM_GROUPS.values():
        for n in group:
            if n not in names:
                names.append(n)
    for n in extra or []:
        if n not in names:
            names.append(n)
    return names
