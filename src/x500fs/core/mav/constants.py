"""MAVLink / ArduPilot constants used by the parsers.

Values are copied from the MAVLink common/ardupilotmega definitions. The test
suite (tests/test_mav_parse.py) cross-checks them against the pymavlink
generated dialect so a typo cannot slip through.
"""

from __future__ import annotations

# --- sentinels (MAVLink "unknown / not available" values) -------------------
UINT8_MAX = 0xFF
UINT16_MAX = 0xFFFF
INT16_MAX = 0x7FFF

# --- HEARTBEAT ----------------------------------------------------------------
MAV_TYPE_GCS = 6
MAV_TYPE_QUADROTOR = 2
MAV_TYPE_ONBOARD_CONTROLLER = 18
MAV_AUTOPILOT_ARDUPILOTMEGA = 3
MAV_AUTOPILOT_INVALID = 8
MAV_MODE_FLAG_SAFETY_ARMED = 128
MAV_MODE_FLAG_CUSTOM_MODE_ENABLED = 1
MAV_COMP_ID_AUTOPILOT1 = 1

MAV_TYPE_NAMES = {
    0: "GENERIC", 1: "FIXED_WING", 2: "QUADROTOR", 3: "COAXIAL", 4: "HELICOPTER",
    6: "GCS", 13: "HEXAROTOR", 14: "OCTOROTOR", 15: "TRICOPTER", 18: "ONBOARD_CONTROLLER",
    27: "ADSB", 26: "GIMBAL", 30: "CAMERA",
}
MAV_AUTOPILOT_NAMES = {0: "GENERIC", 3: "ARDUPILOTMEGA", 8: "INVALID", 12: "PX4"}

MAV_STATE_NAMES = {
    0: "UNINIT", 1: "BOOT", 2: "CALIBRATING", 3: "STANDBY", 4: "ACTIVE",
    5: "CRITICAL", 6: "EMERGENCY", 7: "POWEROFF", 8: "FLIGHT_TERMINATION",
}

# --- SYS_STATUS onboard_control_sensors_* bits ---------------------------------
SENSOR_BITS: dict[str, int] = {
    "gyro": 0x1,
    "accel": 0x2,
    "mag": 0x4,
    "baro": 0x8,
    "diff_pressure": 0x10,
    "gps": 0x20,
    "optical_flow": 0x40,
    "vision_position": 0x80,
    "laser_position": 0x100,
    "rate_control": 0x400,
    "attitude_stabilization": 0x800,
    "yaw_position": 0x1000,
    "z_altitude_control": 0x2000,
    "xy_position_control": 0x4000,
    "motor_outputs": 0x8000,
    "rc_receiver": 0x10000,
    "gyro2": 0x20000,
    "accel2": 0x40000,
    "mag2": 0x80000,
    "geofence": 0x100000,
    "ahrs": 0x200000,
    "terrain": 0x400000,
    "reverse_motor": 0x800000,
    "logging": 0x1000000,
    "battery": 0x2000000,
    "proximity": 0x4000000,
    "satcom": 0x8000000,
    "prearm_check": 0x10000000,
    "obstacle_avoidance": 0x20000000,
    "propulsion": 0x40000000,
}

SENSOR_LABELS_HE: dict[str, str] = {
    "gyro": "ג'ירו",
    "accel": "מד תאוצה",
    "mag": "מצפן",
    "baro": "ברומטר",
    "gps": "GPS",
    "ahrs": "AHRS",
    "rc_receiver": "מקלט RC",
    "logging": "רישום (Logging)",
    "battery": "סוללה",
    "geofence": "גדר (Geofence)",
    "motor_outputs": "יציאות מנועים",
    "prearm_check": "בדיקות Pre-arm",
    "gyro2": "ג'ירו 2",
    "accel2": "מד תאוצה 2",
    "mag2": "מצפן 2",
    "terrain": "Terrain",
    "proximity": "Proximity",
}

# --- EKF_STATUS_REPORT flags ---------------------------------------------------
EKF_FLAGS: dict[str, int] = {
    "attitude": 1,
    "velocity_horiz": 2,
    "velocity_vert": 4,
    "pos_horiz_rel": 8,
    "pos_horiz_abs": 16,
    "pos_vert_abs": 32,
    "pos_vert_agl": 64,
    "const_pos_mode": 128,
    "pred_pos_horiz_rel": 256,
    "pred_pos_horiz_abs": 512,
    "uninitialized": 1024,
    "gps_glitching": 32768,
}

# --- GPS_RAW_INT fix_type (GPS_FIX_TYPE) ----------------------------------------
GPS_FIX_NAMES = {
    0: "NO_GPS",
    1: "NO_FIX",
    2: "2D_FIX",
    3: "3D_FIX",
    4: "DGPS",
    5: "RTK_FLOAT",
    6: "RTK_FIXED",
    7: "STATIC",
    8: "PPP",
}

# --- ArduCopter flight modes (custom_mode) --------------------------------------
COPTER_MODES: dict[int, str] = {
    0: "STABILIZE", 1: "ACRO", 2: "ALT_HOLD", 3: "AUTO", 4: "GUIDED", 5: "LOITER",
    6: "RTL", 7: "CIRCLE", 9: "LAND", 11: "DRIFT", 13: "SPORT", 14: "FLIP",
    15: "AUTOTUNE", 16: "POSHOLD", 17: "BRAKE", 18: "THROW", 19: "AVOID_ADSB",
    20: "GUIDED_NOGPS", 21: "SMART_RTL", 22: "FLOWHOLD", 23: "FOLLOW", 24: "ZIGZAG",
    25: "SYSTEMID", 26: "AUTOROTATE", 27: "AUTO_RTL", 28: "TURTLE", 29: "RATE_ACRO",
}

# --- MAV_SEVERITY ---------------------------------------------------------------
SEVERITY_NAMES = {
    0: "EMERGENCY", 1: "ALERT", 2: "CRITICAL", 3: "ERROR",
    4: "WARNING", 5: "NOTICE", 6: "INFO", 7: "DEBUG",
}

# --- commands / results -----------------------------------------------------------
MAV_CMD_REQUEST_MESSAGE = 512
MAV_RESULT_NAMES = {
    0: "ACCEPTED", 1: "TEMPORARILY_REJECTED", 2: "DENIED", 3: "UNSUPPORTED",
    4: "FAILED", 5: "IN_PROGRESS", 6: "CANCELLED",
}

# Message ids we may ask for with MAV_CMD_REQUEST_MESSAGE (one-shot, does not
# change stream rates and does not write parameters).
MSG_IDS = {
    "HEARTBEAT": 0,
    "SYS_STATUS": 1,
    "GPS_RAW_INT": 24,
    "GLOBAL_POSITION_INT": 33,
    "AUTOPILOT_VERSION": 148,
    "BATTERY_STATUS": 147,
    "EKF_STATUS_REPORT": 193,
    "HOME_POSITION": 242,
}

FIRMWARE_VERSION_TYPES = {0: "dev", 64: "alpha", 128: "beta", 192: "rc", 255: "official"}

# --- ArduPilot arming check bitmask -------------------------------------------------
# ARMING_CHECK (ArduPilot <= 4.6): "checks to perform", value 1 (bit 0) = all.
# ARMING_SKIPCHK (ArduPilot >= 4.7.0): "checks to skip", 0 = none skipped,
# -1 = skip all non-mandatory checks. Same bit positions in both.
# Source: libraries/AP_Arming/AP_Arming.cpp (Copter-4.6.3 / Copter-4.7.2).
ARMING_CHECK_BITS: dict[int, str] = {
    0: "ALL",
    1: "BARO",
    2: "COMPASS",
    3: "GPS_LOCK",
    4: "INS",
    5: "PARAMETERS",
    6: "RC",
    7: "BOARD_VOLTAGE",
    8: "BATTERY",
    9: "AIRSPEED",
    10: "LOGGING",
    11: "SAFETY_SWITCH",
    12: "GPS_CONFIG",
    13: "SYSTEM",
    14: "MISSION",
    15: "RANGEFINDER",
    16: "CAMERA",
    17: "AUX_AUTH",
    18: "VISION",
    19: "FFT",
    20: "OSD",
}

# Bits of ARMING_CHECK that do not apply to Copter on firmware that still uses
# ARMING_CHECK (<= 4.6): 9 = airspeed (Plane only), 20 = OSD (added later).
ARMING_CHECK_NOT_COPTER = frozenset({9, 20})
