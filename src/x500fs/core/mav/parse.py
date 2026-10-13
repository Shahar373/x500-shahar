"""Pure functions that turn raw MAVLink field dicts into normalized values.

Every function returns ``None`` for values that are unknown, sentinel or
malformed instead of a number that could be mistaken for a real reading.
Inputs are plain dicts (``msg.to_dict()``) so the functions can be tested
without a live link.
"""

from __future__ import annotations

import math
from typing import Any

from . import constants as C


def _int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and math.isfinite(v) and v == int(v):
        return int(v)
    return None


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and math.isfinite(float(v)):
        return float(v)
    return None


# --- HEARTBEAT -------------------------------------------------------------------

def is_armed(hb: dict) -> bool | None:
    base_mode = _int(hb.get("base_mode"))
    if base_mode is None:
        return None
    return bool(base_mode & C.MAV_MODE_FLAG_SAFETY_ARMED)


def copter_mode_name(hb: dict) -> str | None:
    base_mode = _int(hb.get("base_mode"))
    custom = _int(hb.get("custom_mode"))
    if base_mode is None or custom is None:
        return None
    if not base_mode & C.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED:
        return None
    return C.COPTER_MODES.get(custom, f"MODE_{custom}")


def is_autopilot_heartbeat(hb: dict) -> bool:
    t = _int(hb.get("type"))
    ap = _int(hb.get("autopilot"))
    if t is None or ap is None:
        return False
    return t != C.MAV_TYPE_GCS and ap != C.MAV_AUTOPILOT_INVALID


def is_gcs_heartbeat(hb: dict) -> bool:
    return _int(hb.get("type")) == C.MAV_TYPE_GCS


# --- AUTOPILOT_VERSION ----------------------------------------------------------------

def decode_sw_version(v: Any) -> dict | None:
    n = _int(v)
    if n is None or n <= 0:
        return None
    major = (n >> 24) & 0xFF
    minor = (n >> 16) & 0xFF
    patch = (n >> 8) & 0xFF
    vtype = n & 0xFF
    return {
        "major": major,
        "minor": minor,
        "patch": patch,
        "type": C.FIRMWARE_VERSION_TYPES.get(vtype, f"type{vtype}"),
        "text": f"{major}.{minor}.{patch}"
        + ("" if vtype == 255 else f"-{C.FIRMWARE_VERSION_TYPES.get(vtype, vtype)}"),
    }


def decode_custom_version(v: Any) -> str | None:
    """flight_custom_version is 8 bytes; ArduPilot stores the git hash."""
    if isinstance(v, (bytes, bytearray)):
        raw = bytes(v)
    elif isinstance(v, (list, tuple)) and all(isinstance(x, int) for x in v):
        raw = bytes(x & 0xFF for x in v)
    elif isinstance(v, str):
        raw = v.encode("latin-1", "ignore")
    else:
        return None
    raw = raw.rstrip(b"\x00")
    if not raw:
        return None
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return raw.hex()
    return text if text.isprintable() else raw.hex()


def autopilot_version(av: dict) -> dict:
    return {
        "flight_sw": decode_sw_version(av.get("flight_sw_version")),
        "git_hash": decode_custom_version(av.get("flight_custom_version")),
        "board_version": _int(av.get("board_version")),
        "vendor_id": _int(av.get("vendor_id")),
        "product_id": _int(av.get("product_id")),
        "uid": _int(av.get("uid")),
        "capabilities": _int(av.get("capabilities")),
    }


# --- SYS_STATUS -----------------------------------------------------------------

def sensors(sys_status: dict) -> dict[str, dict[str, bool]] | None:
    present = _int(sys_status.get("onboard_control_sensors_present"))
    enabled = _int(sys_status.get("onboard_control_sensors_enabled"))
    health = _int(sys_status.get("onboard_control_sensors_health"))
    if present is None or enabled is None or health is None:
        return None
    out: dict[str, dict[str, bool]] = {}
    for name, bit in C.SENSOR_BITS.items():
        out[name] = {
            "present": bool(present & bit),
            "enabled": bool(enabled & bit),
            "healthy": bool(health & bit),
        }
    return out


def battery_from_sys_status(sys_status: dict) -> dict:
    mv = _int(sys_status.get("voltage_battery"))
    ca = _int(sys_status.get("current_battery"))
    rem = _int(sys_status.get("battery_remaining"))
    return {
        # UINT16_MAX = "not sent by autopilot"
        "voltage_v": None if mv is None or mv == C.UINT16_MAX or mv < 0 else mv / 1000.0,
        # -1 = "not measured"
        "current_a": None if ca is None or ca == -1 else ca / 100.0,
        # -1 = "not estimated"
        "remaining_pct": None if rem is None or rem == -1 or not 0 <= rem <= 100 else rem,
    }


def battery_from_battery_status(bs: dict) -> dict:
    ca = _int(bs.get("current_battery"))
    rem = _int(bs.get("battery_remaining"))
    consumed = _int(bs.get("current_consumed"))
    temp = _int(bs.get("temperature"))
    volts = bs.get("voltages")
    cells: list[float] = []
    if isinstance(volts, (list, tuple)):
        for v in volts:
            iv = _int(v)
            if iv is None or iv == C.UINT16_MAX:
                continue
            cells.append(iv / 1000.0)
    return {
        "id": _int(bs.get("id")),
        "current_a": None if ca is None or ca == -1 else ca / 100.0,
        "remaining_pct": None if rem is None or rem == -1 or not 0 <= rem <= 100 else rem,
        "consumed_mah": None if consumed is None or consumed == -1 else consumed,
        "temperature_c": None if temp is None or temp == C.INT16_MAX else temp / 100.0,
        "voltage_fields_v": cells,
    }


# --- GPS_RAW_INT ------------------------------------------------------------------

def gps_raw(g: dict) -> dict:
    fix = _int(g.get("fix_type"))
    sats = _int(g.get("satellites_visible"))
    eph = _int(g.get("eph"))
    epv = _int(g.get("epv"))
    h_acc = _int(g.get("h_acc"))
    return {
        "fix_type": fix,
        "fix_name": None if fix is None else C.GPS_FIX_NAMES.get(fix, f"FIX_{fix}"),
        "satellites": None if sats is None or sats == C.UINT8_MAX else sats,
        "hdop": None if eph is None or eph == C.UINT16_MAX else eph / 100.0,
        "vdop": None if epv is None or epv == C.UINT16_MAX else epv / 100.0,
        # h_acc is a MAVLink2 extension in mm; 0 means "not reported".
        "h_acc_m": None if h_acc is None or h_acc <= 0 else h_acc / 1000.0,
    }


# --- EKF_STATUS_REPORT ------------------------------------------------------------

def ekf_status(e: dict) -> dict | None:
    flags = _int(e.get("flags"))
    if flags is None:
        return None
    variances = {}
    for k in ("velocity_variance", "pos_horiz_variance", "pos_vert_variance",
              "compass_variance", "terrain_alt_variance"):
        variances[k] = _num(e.get(k))
    return {
        "flags_raw": flags,
        "flags": {name: bool(flags & bit) for name, bit in C.EKF_FLAGS.items()},
        "variances": variances,
    }


# --- HOME_POSITION ----------------------------------------------------------------

def home_position(h: dict) -> dict | None:
    lat = _int(h.get("latitude"))
    lon = _int(h.get("longitude"))
    alt = _int(h.get("altitude"))
    if lat is None or lon is None:
        return None
    if lat == 0 and lon == 0:
        return None  # not set
    return {"lat": lat / 1e7, "lon": lon / 1e7, "alt_m": None if alt is None else alt / 1000.0}


# --- PARAM_VALUE ------------------------------------------------------------------

_INT_PARAM_TYPES = {1, 2, 3, 4, 5, 6, 7, 8}  # MAV_PARAM_TYPE_UINT8..INT64


def param_value(p: dict) -> tuple[str, float | int] | None:
    name = p.get("param_id")
    if isinstance(name, bytes):
        name = name.decode("ascii", "ignore")
    if not isinstance(name, str):
        return None
    name = name.split("\x00", 1)[0].strip()
    if not name:
        return None
    value = _num(p.get("param_value"))
    if value is None:
        return None
    ptype = _int(p.get("param_type"))
    # ArduPilot sends integer parameters cast to float (not byte-wise).
    if ptype in _INT_PARAM_TYPES and value == int(value):
        return name, int(value)
    return name, value


# --- STATUSTEXT ------------------------------------------------------------------

def statustext(s: dict) -> dict | None:
    text = s.get("text")
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    if not isinstance(text, str):
        return None
    text = text.split("\x00", 1)[0]
    sev = _int(s.get("severity"))
    return {
        "severity": sev,
        "severity_name": C.SEVERITY_NAMES.get(sev, str(sev)) if sev is not None else None,
        "text": text,
        "id": _int(s.get("id")) or 0,
        "chunk_seq": _int(s.get("chunk_seq")) or 0,
    }


def is_prearm_text(text: str) -> bool:
    return text.strip().lower().startswith("prearm")


def arming_check_decode(value: int) -> dict:
    """Decode ARMING_CHECK (ArduPilot <= 4.6 semantics)."""
    if value == 0:
        return {"all": False, "disabled": True, "enabled_bits": [], "value": value}
    if value & 1:
        return {"all": True, "disabled": False, "enabled_bits": ["ALL"], "value": value}
    names = [name for bit, name in C.ARMING_CHECK_BITS.items() if bit and value & (1 << bit)]
    return {"all": False, "disabled": False, "enabled_bits": names, "value": value}
