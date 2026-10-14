"""Test helpers: build observation stores with realistic data."""

from __future__ import annotations

import time

from x500fs.core import profile as profile_mod
from x500fs.core.checks import Ctx, DroneConfig, evaluate
from x500fs.core.observations import ObservationStore
from x500fs.core.session import SessionLog
from x500fs.core.status import aggregate
from x500fs.sim.simulator import SIM_PARAMS, make_collector_doc

SERVICES = {"mavlink_router": "mavlink-router", "blackbox": "x500-blackbox",
            "health_monitor": "x500-health-monitor", "net_monitor": "x500-net-monitor", "tailscale": "tailscaled"}

SENSORS_OK = 0x1 | 0x2 | 0x4 | 0x8 | 0x20 | 0x200000 | 0x1000000 | 0x2000000 | 0x10000000


def drone(**kw) -> DroneConfig:
    d = dict(expected_hostname="x500pi", link_path="wifi", services=dict(SERVICES),
             blackbox_globs=["/var/log/x500/blackbox/x500-blackbox-*.csv"], watchdog_expected_runtime="15s")
    d.update(kw)
    return DroneConfig(**d)


def msgs_healthy() -> dict[str, dict]:
    return {
        "HEARTBEAT": {"mavpackettype": "HEARTBEAT", "type": 2, "autopilot": 3, "base_mode": 81,
                      "custom_mode": 0, "system_status": 3, "mavlink_version": 3},
        "SYS_STATUS": {"mavpackettype": "SYS_STATUS", "onboard_control_sensors_present": SENSORS_OK,
                       "onboard_control_sensors_enabled": SENSORS_OK, "onboard_control_sensors_health": SENSORS_OK,
                       "voltage_battery": 16400, "current_battery": 90, "battery_remaining": 97,
                       "load": 250, "errors_comm": 0},
        "GPS_RAW_INT": {"mavpackettype": "GPS_RAW_INT", "fix_type": 3, "satellites_visible": 14, "eph": 80,
                        "epv": 120, "h_acc": 900},
        "EKF_STATUS_REPORT": {"mavpackettype": "EKF_STATUS_REPORT", "flags": 1 | 2 | 4 | 8 | 16 | 32 | 512,
                              "velocity_variance": 0.1, "pos_horiz_variance": 0.1, "pos_vert_variance": 0.1,
                              "compass_variance": 0.05, "terrain_alt_variance": 0.0},
        "GLOBAL_POSITION_INT": {"mavpackettype": "GLOBAL_POSITION_INT", "lat": 1, "lon": 1},
        "HOME_POSITION": {"mavpackettype": "HOME_POSITION", "latitude": 320000000, "longitude": 348000000,
                          "altitude": 50000},
        "AUTOPILOT_VERSION": {"mavpackettype": "AUTOPILOT_VERSION",
                              "flight_sw_version": (4 << 24) | (7 << 16) | (2 << 8) | 255,
                              "flight_custom_version": list(b"51a7f3c1")},
    }


def prearm_diag_complete(healthy=True, texts=None) -> dict:
    """What MavlinkClient publishes after a successful RUN_PREARM_CHECKS round."""
    return {"state": "complete", "finished": True, "result": 0, "result_name": "ACCEPTED", "ack_from": "1/1",
            "texts": list(texts or []), "prearm_bit": {"present": True, "enabled": True, "healthy": healthy},
            "sys_status_after_ack_s": 1.1}


class World:
    """Mutable fixture: a store plus helpers to evaluate with a profile."""

    def __init__(self, simulated: bool = False, profile_id: str = "bench"):
        self.store = ObservationStore(simulated=simulated)
        self.session = SessionLog()
        self.sim = simulated
        self.profile = profile_mod.load_builtin(profile_id)
        self.drone = drone()

    def put(self, key, value, age: float = 0.0):
        now = time.monotonic()
        self.store.put(key, value, "test", simulated=self.sim, t_mono=now - age)

    def link(self, connected=True, state="live"):
        self.put("mav.link", {"connected": connected, "state": state, "endpoint": "test", "target_sysid": 1,
                              "bad_packets": 0})

    def healthy(self, link_path="wifi", params=None, age=0.0):
        self.link()
        for name, d in msgs_healthy().items():
            self.put(f"mav.msg.{name}", d, age)
        for k, v in (params if params is not None else SIM_PARAMS).items():
            self.put(f"mav.param.{k}", {"value": v})
        self.put("pi.ssh", {"state": "connected", "host": "x500pi", "port": 22, "user": "pi",
                            "fingerprint": "SHA256:test"})
        self.put("pi.collector", make_collector_doc("healthy", link_path, 1000, "boot-1"), age)
        self.put("pi.boot_history", {"current_boot_id": "boot-1", "previous_boot_id": "boot-1",
                                     "boot_ids_this_session": ["boot-1"]})
        self.put("mav.prearm_diag", prearm_diag_complete(), age)
        self.session.rtt.add(20)
        self.drone.link_path = link_path
        return self

    def set_profile(self, doc_changes: dict | None = None, profile_id: str | None = None):
        doc = profile_mod.builtin_doc(profile_id or self.profile.id)
        for k, v in (doc_changes or {}).items():
            doc[k] = v
        self.profile = profile_mod.from_dict(doc, "test")
        return self

    def evaluate(self):
        results = evaluate(Ctx(self.store.snapshot(), self.profile, self.drone, self.session))
        return {r.check_id: r for r in results}, aggregate(results)


def flight_complete_profile(cells=4, chem="LiPo") -> dict:
    """A flight profile with every 'unconfigured' item explicitly configured."""
    doc = profile_mod.builtin_doc("flight")
    doc["battery"].update({"chemistry": chem, "cells": cells})
    doc["expected_params"] = {
        "battery_monitor": {"BATT_MONITOR": {"eq": 4}, "BATT_CAPACITY": {"min": 3000}},
        "failsafe": {"FS_THR_ENABLE": {"eq": 1}, "BATT_FS_LOW_ACT": {"eq": 2}},
        "rtl": {"RTL_ALT": {"min": 1500, "max": 5000}},
        "fence": {"FENCE_ENABLE": {"eq": 1}},
    }
    return doc
