"""Simulation mode: a fake X500 that never touches the network.

Everything produced here is stored with ``simulated=True`` in a simulation-only
observation store, every source label starts with "SIM", and the UI shows a
permanent banner. MAVLink messages go through the real :class:`MavIngest`
code path so simulation exercises the same parsing as a live session.
"""

from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass

from ..core.observations import ObservationStore
from ..core.session import SessionLog
from ..transport.mav_ingest import MavIngest

SCENARIOS = {
    "healthy": "תקין (GPS קיבוע 3D, ללא תקלות)",
    "no_gps": "ללא קיבוע GPS (בתוך מבנה)",
    "prearm_fail": "Pre-arm נכשל (מצפן לא מכויל)",
    "arming_disabled": "בדיקות חימוש מושבתות (ARMING_CHECK=0)",
    "battery_unknown": "סוללה לא ידועה (מתח 65535, זרם -1)",
    "mavlink_dropouts": "ניתוקי MAVLink חוזרים",
    "pi_undervoltage": "Pi: מתח נמוך כעת",
    "lte_path": "חיבור דרך Tailscale מעל LTE",
}

SIM_ENDPOINT = "SIM (סימולציה)"
SIM_FINGERPRINT = "SHA256:SIMULATED-NOT-A-REAL-HOST-KEY"

# Parameters of the simulated vehicle. These are NOT the user's values.
SIM_PARAMS = {
    "ARMING_CHECK": 1, "GPS_HDOP_GOOD": 140, "FS_EKF_THRESH": 0.8, "FS_EKF_ACTION": 1,
    "BATT_MONITOR": 4, "BATT_CAPACITY": 5000, "BATT_LOW_VOLT": 14.0, "BATT_CRT_VOLT": 13.2,
    "BATT_ARM_VOLT": 0, "BATT_VOLT_MULT": 18.0, "BATT_AMP_PERVLT": 36.36,
    "BATT_FS_LOW_ACT": 2, "BATT_FS_CRT_ACT": 1, "FS_THR_ENABLE": 1, "FS_GCS_ENABLE": 1,
    "FS_GCS_TIMEOUT": 5, "FS_OPTIONS": 16, "RTL_ALT": 3000, "RTL_ALT_FINAL": 0,
    "RTL_SPEED": 0, "RTL_LOIT_TIME": 5000, "FENCE_ENABLE": 1, "FENCE_TYPE": 7, "FENCE_ACTION": 1,
    "FENCE_ALT_MAX": 100, "FENCE_RADIUS": 300, "FENCE_MARGIN": 2, "SYSID_THISMAV": 1,
    "SYSID_MYGCS": 255, "SYSID_ENFORCE": 0, "LOG_BACKEND_TYPE": 1, "LOG_DISARMED": 0,
    "FRAME_CLASS": 1, "FRAME_TYPE": 1,
}


@dataclass
class SimState:
    scenario: str
    link_path: str


def _hb(armed=False, custom_mode=0, mtype=2, autopilot=3):
    return {"mavpackettype": "HEARTBEAT", "type": mtype, "autopilot": autopilot,
            "base_mode": 1 | (128 if armed else 0) | 16, "custom_mode": custom_mode,
            "system_status": 3, "mavlink_version": 3}


def make_collector_doc(scenario: str, link_path: str, uptime_s: float, boot_id: str,
                       hostname: str = "x500pi", t_wall: float | None = None,
                       video_profile: str | None = None) -> dict:
    """Build a collector document with the same schema as the real collector."""
    t_wall = t_wall or time.time()
    temp = 52.0 + 3 * math.sin(t_wall / 30.0)
    throttled = 0x1 | 0x10000 if scenario == "pi_undervoltage" else 0x0
    lte_kind_arrival = link_path in ("lte", "tailscale")
    ifaces = [
        {"name": "lo", "operstate": "unknown", "kind": "loopback", "driver": None, "usb": None,
         "ipv4": ["127.0.0.1/8"], "ipv6": []},
        {"name": "wlan0", "operstate": "up", "kind": "wifi", "driver": "brcmfmac", "usb": None,
         "ipv4": ["192.0.2.20/24"], "ipv6": []},
        {"name": "tailscale0", "operstate": "unknown", "kind": "tailscale", "driver": None, "usb": None,
         "ipv4": ["100.64.0.20/32"], "ipv6": []},
        {"name": "eth1", "operstate": "up", "kind": "lte", "driver": "cdc_ether",
         "usb": {"vid": "19d2", "pid": "1405", "product": "SIM ZTE modem"},
         "ipv4": ["198.51.100.10/24"], "ipv6": []},
    ]
    routes = [{"dev": "wlan0", "gateway": "192.0.2.1", "metric": 600, "kind": "wifi"},
              {"dev": "eth1", "gateway": "198.51.100.1", "metric": 700, "kind": "lte"}]
    if link_path == "lte":
        routes = list(reversed(routes))
    arrival = ({"server_ip": "100.64.0.20", "client_ip": "100.64.0.5", "dev": "tailscale0", "kind": "tailscale"}
               if lte_kind_arrival else
               {"server_ip": "192.0.2.20", "client_ip": "192.0.2.5", "dev": "wlan0", "kind": "wifi"})
    underlay = "eth1" if link_path == "lte" else "wlan0"
    ok = lambda data: {"ok": True, "data": data, "error": None, "duration_ms": 1}  # noqa: E731
    return {
        "schema": "x500fs.collector/1",
        "collector_version": "sim",
        "generated_at_epoch": t_wall,
        "duration_ms": 900,
        "sections": {
            "identity": ok({"hostname": hostname, "os_release": {"PRETTY_NAME": "SIM Debian GNU/Linux 13 (trixie)",
                                                                  "VERSION_CODENAME": "trixie", "ID": "debian"},
                            "kernel": "6.12.0-sim", "machine": "aarch64",
                            "model": "Raspberry Pi 5 Model B (SIM)", "python": "3.13.0",
                            "boot_id": boot_id, "user": "sim", "ssh_connection": arrival}),
            "uptime": ok({"uptime_s": uptime_s, "boot_time_epoch": t_wall - uptime_s}),
            "thermal": ok({"zones": [{"zone": "thermal_zone0", "type": "cpu-thermal", "temp_c": temp}],
                           "zone_temp_c": round(temp, 1), "vcgencmd_temp_c": round(temp + 0.3, 1),
                           "vcgencmd_error": None, "cpu_temp_c": round(temp, 1), "cpu_temp_source": "thermal_zone"}),
            "cpu": ok({"usage_pct": round(18 + 6 * random.random(), 1), "sample_s": 0.5,
                       "loadavg": [0.42, 0.38, 0.31], "cores": 4, "freq_mhz": 2400.0,
                       "processes": [{"pid": 900, "name": "ffmpeg", "cpu_pct_of_one_core": 31.0},
                                     {"pid": 899, "name": "rpicam-vid", "cpu_pct_of_one_core": 9.5}]
                       if video_profile else []}),
            "memory": ok({"total_kb": 8_000_000, "available_kb": 6_400_000, "available_pct": 80.0}),
            "storage": ok({"mounts": [{"path": "/", "total_mb": 58000.0, "free_mb": 41000.0, "read_only": False},
                                      {"path": "/boot/firmware", "total_mb": 510.0, "free_mb": 445.0,
                                       "read_only": False}]}),
            "power": ok({"vcgencmd": "/usr/bin/vcgencmd", "throttled": throttled,
                         "throttled_raw": f"throttled={hex(throttled)}",
                         "throttled_decoded": {"current": ["under_voltage"] if throttled & 1 else [],
                                               "since_boot": ["under_voltage"] if throttled & 0x10000 else []},
                         "vcgencmd_error": None, "ext5v_v": 4.62 if throttled else 5.08,
                         "hwmon_undervoltage_alarm": 1 if throttled else 0}),
            "services": ok({"units": {
                "mavlink-router": {"LoadState": "loaded", "ActiveState": "active", "SubState": "running",
                                   "NRestarts": "0", "role": "mavlink_router"},
                "x500-blackbox": {"LoadState": "loaded", "ActiveState": "active", "SubState": "running",
                                  "NRestarts": "0", "role": "blackbox"},
                "x500-health-monitor": {"LoadState": "loaded", "ActiveState": "active", "SubState": "running",
                                        "NRestarts": "0", "role": "health_monitor"},
                "x500-net-monitor": {"LoadState": "loaded", "ActiveState": "active", "SubState": "running",
                                     "NRestarts": "0", "role": "net_monitor"},
                "tailscaled": {"LoadState": "loaded", "ActiveState": "active", "SubState": "running",
                               "NRestarts": "0", "role": "tailscale"},
            }, "discovered": ["mavlink-router.service", "tailscaled.service", "x500-blackbox.service",
                              "x500-health-monitor.service", "x500-net-monitor.service", "x500-video@.service"]}),
            "watchdog": ok({"dev_watchdog_exists": True, "devices": [{"name": "watchdog0", "identity": "SIM",
                                                                       "state": "active", "timeout": "15"}],
                            "configured_in_files": [{"file": "60-x500-watchdog.conf", "line": "RuntimeWatchdogSec=15s"}],
                            "systemd": {"RuntimeWatchdogUSec": "15s", "RebootWatchdogUSec": "2min",
                                        "WatchdogDevice": "/dev/watchdog0",
                                        "WatchdogLastPingTimestampMonotonic": "123456789"},
                            "last_ping_age_s": round(random.random() * 7, 2)}),
            "blackbox": ok({"globs": ["/var/log/x500/blackbox/x500-blackbox-*.csv"], "file_count": 6,
                            "total_mb": 4.2, "oldest_file": "/var/log/x500/blackbox/x500-blackbox-20261009.csv",
                            "newest": {"path": "/var/log/x500/blackbox/x500-blackbox-20261014.csv", "size": 123456,
                                       "mtime_age_s": 4.0},
                            "samples": {"header_ok": True, "rows_checked": 40, "valid_rows": 40, "invalid_examples": [],
                                        "last_valid_age_s": 4.0,
                                        "last_valid_values": {"timestamp": "SIM", "temp_c": f"{temp:.1f}"},
                                        "interval_s": {"median": 14.8, "min": 13.9, "max": 16.1, "count": 39}}}),
            "mavlink_router": ok({"config_files": ["/etc/mavlink-router/main.conf"],
                                  "general": {"TcpServerPort": "5760"},
                                  "endpoints": [{"file": "main.conf", "type": "UartEndpoint", "name": "pixhawk",
                                                 "Device": "/dev/serial/by-id/usb-Holybro_Pixhawk6C_SIMSERIAL-if00",
                                                 "Baud": "115200"},
                                                {"file": "main.conf", "type": "UdpEndpoint", "name": "groundstation",
                                                 "Mode": "Normal", "Address": "192.0.2.5", "Port": "14550"}],
                                  "listening": [{"proto": "tcp", "addr": "0.0.0.0", "port": 5760}]}),
            "video": ok({"profiles": ["high", "normal", "degraded"],
                         "units": {p: ({"ActiveState": "active", "SubState": "running", "MainPID": "900", "NRestarts": "0"}
                                       if p == video_profile else {"ActiveState": "inactive", "SubState": "dead"})
                                   for p in ("high", "normal", "degraded")},
                         "active_profile": video_profile,
                         "progress": ({"path": f"/run/x500-video-{video_profile}.progress", "exists": True,
                                       "mtime_age_s": 0.6,
                                       "parsed": {"frame": int(t_wall * 20) % 10_000_000, "fps": 20.0,
                                                  "bitrate": " 351.2kbits/s", "progress": "continue"},
                                       "frame_rate_between_polls": 20.0} if video_profile else None),
                         "config": {"found": True, "PORT": "5600", "GROUND_IP_set": False},
                         "udp_listening": [{"proto": "udp", "addr": "100.64.0.20", "port": 5600}] if video_profile else [],
                         "rpicam_vid": True, "ffmpeg": True}),
            "fc_usb": ok({"glob": "usb-Holybro_Pixhawk6C_*-if00",
                          "matches": [{"by_id": "/dev/serial/by-id/usb-Holybro_Pixhawk6C_SIMSERIAL-if00",
                                       "target": "ttyACM0"}], "tty_acm": ["ttyACM0"]}),
            "camera": ok({"tool": "rpicam-hello", "cameras": [
                {"index": 0, "sensor": "imx219", "desc": "3280x2464 10-bit RGGB", "path": "/base/axi/sim"}],
                "exit_code": 0, "note": None}),
            "mediamtx": ok({"binary": "/usr/local/bin/mediamtx", "api_reachable": True, "path_name": "x500cam",
                            "path": {"ready": False, "source_type": "rpiCameraSource", "readers": 0},
                            "path_config": {"source": "rpiCamera", "sourceOnDemand": True,
                                            "rpiCameraWidth": 640, "rpiCameraHeight": 480, "rpiCameraFPS": 15}}),
            "network": ok({"interfaces": ifaces, "default_routes": routes, "ssh_arrival": arrival,
                           "wifi": {"wlan0": {"connected": True, "ssid": "SIM-WIFI", "signal_dbm": -58}},
                           "tailscale": {"available": True, "backend_state": "Running", "self_online": True,
                                         "self_ips": ["100.64.0.20"],
                                         "peer": {"online": True, "cur_addr": None, "relay": "sim", "direct": False},
                                         "underlay_dev": underlay,
                                         "underlay_kind": "lte" if underlay == "eth1" else "wifi"}}),
            "usb": ok({"devices": [{"bus_path": "1-1", "vid": "19d2", "pid": "1405", "manufacturer": "SIM",
                                    "product": "SIM ZTE modem"}]}),
            "lte": ok({"usb_modems": [{"vid": "19d2", "pid": "1405", "product": "SIM ZTE modem"}],
                       "interfaces": [ifaces[3]], "configured_interface": "eth1",
                       "default_route_via_lte": link_path == "lte", "mmcli": None}),
            "net_probes": ok({"probes": {
                "internet_default_route": {"target": "1.1.1.1", "ok": True, "rtt_ms": 24.0},
                "internet_via_wifi": {"target": "1.1.1.1", "iface": "wlan0", "ok": True, "rtt_ms": 22.0},
                "internet_via_lte": {"target": "1.1.1.1", "iface": "eth1", "ok": True, "rtt_ms": 61.0},
                "zte_gateway": {"target": "192.0.2.254", "iface": "eth1", "ok": True, "rtt_ms": 2.1},
                "ground_station": {"configured": False}},
                "internet_route": {"dev": "eth1" if link_path == "lte" else "wlan0", "gateway": "192.0.2.1"},
                "tailscale": {"service": "active", "ipv4": "100.64.0.20"},
                "config": {"ping_target": "1.1.1.1", "wifi_interface": "wlan0", "lte_interface": "eth1",
                           "zte_gateway_configured": True, "ground_ip_configured": False}}),
            "tools": ok({}),
        },
    }


class SimDrone:
    """Generates simulated telemetry and Pi observations in a worker thread."""

    def __init__(self, store: ObservationStore, session: SessionLog, scenario: str = "healthy",
                 link_path: str = "wifi", on_text=None, poll_interval_s: float = 5.0):
        if not store.simulated:
            raise ValueError("SimDrone requires a simulation-only observation store")
        self.store = store
        self.session = session
        self.state = SimState(scenario if scenario in SCENARIOS else "healthy", link_path)
        self.poll_interval_s = poll_interval_s
        self.ingest = MavIngest(store, session, simulated=True, endpoint=SIM_ENDPOINT, on_text=on_text)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._boot_id = "sim-boot-" + format(random.getrandbits(32), "08x")
        self._t0 = time.monotonic()
        self._params_sent = False
        self.armed = False
        self.video_profile: str | None = None  # set by the controller while simulated video runs
        self._diag_requested = False
        self._diag: dict | None = None

    # -- lifecycle -------------------------------------------------------------
    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sim-drone", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self.ingest.publish_link(False, "disconnected")
        self._put("pi.ssh", {"state": "disconnected", "host": "x500pi (SIM)", "port": 22, "user": "sim"})

    def request_refresh(self, prearm_diagnostic: bool = True) -> None:
        """Equivalent of 'run preflight': re-send params and a fresh collection."""
        self._params_sent = False
        self._next_collect = 0.0
        self._diag_requested = prearm_diagnostic

    # -- helpers ---------------------------------------------------------------
    def _put(self, key, value):
        self.store.put(key, value, "SIM", simulated=True)

    def _msg(self, d):
        self.ingest.handle(d, 1, 1)

    def _run(self) -> None:
        sc = self.state.scenario
        self._put("pi.poll_interval_s", self.poll_interval_s)
        self._put("pi.ssh", {"state": "connected", "host": "x500pi (SIM)", "port": 22, "user": "sim",
                             "fingerprint": SIM_FINGERPRINT})
        self.session.link_up("ssh")
        self.session.link_up("mavlink")
        self.ingest.publish_link(True, "live", transport="simulation")
        self.session.event("info", "sim", "מצב סימולציה פעיל - הנתונים אינם מהרחפן")
        self._put("pi.boot_history", {"current_boot_id": self._boot_id, "previous_boot_id": None,
                                      "boot_ids_this_session": [self._boot_id]})
        tick = 0
        self._next_collect = 0.0
        texts_at = 0.0
        dropout_until = 0.0
        hb_ok = True
        while not self._stop.is_set():
            now = time.monotonic()
            t = now - self._t0
            if sc == "mavlink_dropouts":
                cycle = t % 40.0
                hb_ok = cycle < 32.0
                if not hb_ok and dropout_until < now:
                    dropout_until = now + 8.0
                    self.session.link_down("mavlink", "SIM: heartbeat timeout")
                    self.ingest.publish_link(True, "stale")
                if hb_ok and dropout_until and now > dropout_until:
                    dropout_until = 0.0
                    self.session.link_up("mavlink")
                    self.ingest.publish_link(True, "live", transport="simulation")
            if hb_ok:
                if tick % 10 == 0:
                    self._msg(_hb(armed=self.armed))
                    self.ingest.handle({"mavpackettype": "HEARTBEAT", "type": 6, "autopilot": 8, "base_mode": 0,
                                        "custom_mode": 0, "system_status": 4, "mavlink_version": 3}, 255, 190)
                if tick % 5 == 0:
                    self._send_status(sc, t)
                if not self._params_sent:
                    for name, value in SIM_PARAMS.items():
                        if sc == "arming_disabled" and name == "ARMING_CHECK":
                            value = 0
                        self._msg({"mavpackettype": "PARAM_VALUE", "param_id": name, "param_value": float(value),
                                   "param_type": 9 if isinstance(value, float) else 6,
                                   "param_count": len(SIM_PARAMS), "param_index": 0})
                    self._msg({"mavpackettype": "AUTOPILOT_VERSION", "capabilities": 0xFFFF,
                               "flight_sw_version": (4 << 24) | (7 << 16) | (2 << 8) | 255,
                               "flight_custom_version": list(b"51a7f3c1"), "board_version": 0,
                               "vendor_id": 0x3162, "product_id": 0x0038, "uid": 0})
                    self._params_sent = True
                if sc == "prearm_fail" and now - texts_at > 30:
                    texts_at = now
                    self._msg({"mavpackettype": "STATUSTEXT", "severity": 2,
                               "text": "PreArm: Compass not calibrated", "id": 0, "chunk_seq": 0})
            if hb_ok:
                self._diag_step(sc, now)
            if tick % 20 == 0:
                rtt = {"wifi": 18.0, "tailscale": 45.0, "lte": 85.0}.get(self.state.link_path, 30.0)
                self.session.rtt.add(rtt * (0.8 + 0.4 * random.random()))
            if now >= self._next_collect:
                self._next_collect = now + self.poll_interval_s
                doc = make_collector_doc(sc, self.state.link_path, uptime_s=900 + t, boot_id=self._boot_id,
                                         video_profile=self.video_profile)
                self._put("pi.collector", doc)
            tick += 1
            self._stop.wait(0.1)

    def _diag_step(self, sc: str, now: float) -> None:
        """Mimic MAV_CMD_RUN_PREARM_CHECKS: same states as the live client publishes."""
        if self._diag_requested:
            self._diag_requested = False
            base = {"requested_wall": time.time(), "texts": [], "simulated": True}
            if self.armed:
                self._diag = {**base, "state": "not_sent", "reason": "vehicle armed", "finished": True}
            else:
                self._diag = {**base, "state": "accepted", "finished": False, "result": 0,
                              "result_name": "ACCEPTED", "ack_from": "1/1", "_t": now}
                if sc == "prearm_fail":
                    self._msg({"mavpackettype": "STATUSTEXT", "severity": 2,
                               "text": "PreArm: Compass not calibrated", "id": 0, "chunk_seq": 0})
                    self._diag["texts"] = ["PreArm: Compass not calibrated"]
            self._put("mav.prearm_diag", {k: v for k, v in self._diag.items() if not k.startswith("_")})
        d = self._diag
        if d and d["state"] == "accepted" and now - d["_t"] >= 1.2:
            failing = sc in ("no_gps", "prearm_fail")
            d.update(state="complete", finished=True, sys_status_after_ack_s=1.2,
                     prearm_bit={"present": True, "enabled": sc != "arming_disabled",
                                 "healthy": not failing and sc != "arming_disabled"})
            self._put("mav.prearm_diag", {k: v for k, v in d.items() if not k.startswith("_")})

    def _send_status(self, sc: str, t: float) -> None:
        healthy_bits = 0x1 | 0x2 | 0x4 | 0x8 | 0x20 | 0x200000 | 0x1000000 | 0x2000000 | 0x10000000
        present = healthy_bits | 0x10000  # RC receiver present but unhealthy (no RC in this setup)
        enabled = present
        health = healthy_bits
        if sc == "no_gps":
            health &= ~0x20
        if sc in ("no_gps", "prearm_fail"):
            health &= ~0x10000000
        if sc == "prearm_fail":
            health &= ~0x4
        if sc == "arming_disabled":
            enabled &= ~0x10000000
            health &= ~0x10000000
        volt = 16450 + int(40 * math.sin(t / 10))
        cur = 85 + int(10 * random.random())
        rem = 96
        if sc == "battery_unknown":
            volt, cur, rem = 0xFFFF, -1, -1
        self._msg({"mavpackettype": "SYS_STATUS", "onboard_control_sensors_present": present,
                   "onboard_control_sensors_enabled": enabled, "onboard_control_sensors_health": health,
                   "load": 250, "voltage_battery": volt, "current_battery": cur, "battery_remaining": rem,
                   "drop_rate_comm": 0, "errors_comm": 0})
        gps_ok = sc != "no_gps"
        self._msg({"mavpackettype": "GPS_RAW_INT", "fix_type": 3 if gps_ok else 1,
                   "satellites_visible": 14 if gps_ok else 3, "eph": 78 if gps_ok else 9999,
                   "epv": 120 if gps_ok else 9999, "h_acc": 900 if gps_ok else 0,
                   "lat": 320000000, "lon": 348000000, "alt": 50000})
        flags = 1 | 2 | 4 | 8 | 32 | (16 | 512 if gps_ok else 128)
        self._msg({"mavpackettype": "EKF_STATUS_REPORT", "flags": flags,
                   "velocity_variance": 0.08 if gps_ok else 0.0, "pos_horiz_variance": 0.05 if gps_ok else 0.0,
                   "pos_vert_variance": 0.06, "compass_variance": 0.04 if sc != "prearm_fail" else 0.62,
                   "terrain_alt_variance": 0.0})
        self._msg({"mavpackettype": "GLOBAL_POSITION_INT", "lat": 320000000, "lon": 348000000, "alt": 50000,
                   "relative_alt": 0, "vx": 0, "vy": 0, "vz": 0, "hdg": 9000})
        self._msg({"mavpackettype": "ATTITUDE", "roll": 0.01, "pitch": -0.02, "yaw": 1.57,
                   "rollspeed": 0, "pitchspeed": 0, "yawspeed": 0})
        self._msg({"mavpackettype": "VFR_HUD", "airspeed": 0.0, "groundspeed": 0.0, "heading": 90,
                   "throttle": 0, "alt": 50.0, "climb": 0.0})
        if gps_ok:
            self._msg({"mavpackettype": "HOME_POSITION", "latitude": 320000000, "longitude": 348000000,
                       "altitude": 50000})
