"""A mock mavlink-router TCP endpoint that speaks real MAVLink2 bytes.

Used by tests and for development without hardware::

    python -m x500fs.sim.mock_mavlink --port 5760

It emits autopilot telemetry (sysid 1) plus a Mission-Planner-like GCS
heartbeat (sysid 255), answers PARAM_REQUEST_READ, MAV_CMD_REQUEST_MESSAGE and
MAV_CMD_RUN_PREARM_CHECKS (like ArduPilot: ACCEPTED when disarmed - which is
not a pass - and TEMPORARILY_REJECTED when armed), and records every message
clients send so tests can prove the app never sends HEARTBEAT or any command
outside its read-only allowlist.

``second_vehicle=True`` adds another autopilot (sysid 2) with conflicting
telemetry, PreArm text and a COMMAND_ACK addressed to the app, to prove that
data from other systems is never attributed to the selected vehicle.
"""

from __future__ import annotations

import argparse
import math
import socket
import threading
import time

from pymavlink.dialects.v20 import ardupilotmega as mavlink2

from .simulator import SIM_PARAMS

MSG_ID_TO_NAME = {v: k for k, v in {
    "SYS_STATUS": 1, "GPS_RAW_INT": 24, "ATTITUDE": 30, "GLOBAL_POSITION_INT": 33, "VFR_HUD": 74,
    "AUTOPILOT_VERSION": 148, "BATTERY_STATUS": 147, "EKF_STATUS_REPORT": 193, "HOME_POSITION": 242,
}.items()}

PREARM_BIT = 0x10000000
SENSORS = 0x1321002F  # includes PREARM_CHECK (bit 28) present/enabled/healthy


def encode(mav: mavlink2.MAVLink, d: dict):
    name = d["mavpackettype"]
    cls = getattr(mavlink2, f"MAVLink_{name.lower()}_message")
    arr = dict(zip(cls.ordered_fieldnames, cls.array_lengths))
    args = []
    for f, t in zip(cls.fieldnames, cls.fieldtypes):
        if f in d:
            v = d[f]
            if t == "char" and isinstance(v, str):
                v = v.encode("ascii")
            args.append(v)
        elif arr.get(f):
            args.append(b"" if t == "char" else [0] * arr[f])
        else:
            args.append(0)
    return cls(*args)


class _Out:
    def __init__(self, sock):
        self.sock = sock
        self.lock = threading.Lock()

    def write(self, data):
        with self.lock:
            self.sock.sendall(data)


class MockAutopilot:
    def __init__(self, host: str = "127.0.0.1", port: int = 0, params: dict | None = None,
                 send_gcs_heartbeat: bool = True, armed: bool = False, respond_requests: bool = True,
                 prearm: str = "pass", prearm_texts: list[str] | None = None,
                 second_vehicle: bool = False, chunked_text: str | None = None):
        """prearm: pass | fail | unsupported | silent (no ACK) | imposter (only sysid 2 ACKs)."""
        self.params = dict(SIM_PARAMS if params is None else params)
        self.send_gcs_heartbeat = send_gcs_heartbeat
        self.armed = armed
        self.respond_requests = respond_requests
        self.prearm = prearm
        self.prearm_texts = list(prearm_texts or ["PreArm: Compass not calibrated"])
        self.second_vehicle = second_vehicle
        self.chunked_text = chunked_text
        self.send_sys_status = True
        self.received: list[tuple[int, int, str, dict]] = []  # (sysid, compid, type, fields)
        self.paused = threading.Event()  # set = stop sending telemetry (simulated outage)
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((host, port))
        self._srv.listen(4)
        self._srv.settimeout(0.3)
        self.port = self._srv.getsockname()[1]
        self._stop = threading.Event()
        self._clients: list[socket.socket] = []
        self._lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._accept, name="mock-ap-accept", daemon=True).start()
        return self

    def stop(self):
        self._stop.set()
        self.drop_clients()
        self._srv.close()

    def drop_clients(self):
        with self._lock:
            for c in self._clients:
                try:
                    c.shutdown(socket.SHUT_RDWR)
                    c.close()
                except OSError:
                    pass
            self._clients.clear()

    def _accept(self):
        while not self._stop.is_set():
            try:
                c, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self._lock:
                self._clients.append(c)
            threading.Thread(target=self._serve, args=(c,), name="mock-ap-client", daemon=True).start()

    def _serve(self, c: socket.socket):
        out = _Out(c)
        ap = mavlink2.MAVLink(out, srcSystem=1, srcComponent=1)
        gcs = mavlink2.MAVLink(out, srcSystem=255, srcComponent=190)
        other = mavlink2.MAVLink(out, srcSystem=2, srcComponent=1)
        self._other = other
        chunk_sent = False
        parser = mavlink2.MAVLink(None)
        parser.robust_parsing = True
        c.settimeout(0.05)
        t0 = time.monotonic()
        last = {"hb": 0.0, "fast": 0.0}
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if not self.paused.is_set():
                    if now - last["hb"] >= 1.0:
                        last["hb"] = now
                        ap.heartbeat_send(2, 3, 1 | 16 | (128 if self.armed else 0), 0, 3)
                        if self.send_gcs_heartbeat:
                            gcs.heartbeat_send(6, 8, 0, 0, 4)
                        if self.second_vehicle:
                            other.heartbeat_send(2, 3, 1 | 16 | 128, 5, 4)  # armed, different mode
                    if now - last["fast"] >= 0.5:
                        last["fast"] = now
                        for d in self.telemetry(now - t0):
                            if d["mavpackettype"] == "SYS_STATUS" and not self.send_sys_status:
                                continue
                            ap.send(encode(ap, d))
                        if self.second_vehicle:
                            for d in self.other_telemetry():
                                other.send(encode(other, d))
                    if self.chunked_text and not chunk_sent and now - t0 > 0.3:
                        chunk_sent = True
                        self.send_chunked(ap, self.chunked_text)
                try:
                    data = c.recv(4096)
                    if not data:
                        return
                except socket.timeout:
                    continue
                for m in parser.parse_buffer(data) or []:
                    self._handle(ap, m)
        except OSError:
            return

    def telemetry(self, t: float) -> list[dict]:
        health = SENSORS & ~PREARM_BIT if self.prearm == "fail" else SENSORS
        return [
            {"mavpackettype": "SYS_STATUS", "onboard_control_sensors_present": SENSORS,
             "onboard_control_sensors_enabled": SENSORS, "onboard_control_sensors_health": health,
             "voltage_battery": 16400 + int(30 * math.sin(t)), "current_battery": 90, "battery_remaining": 97,
             "load": 210},
            {"mavpackettype": "GPS_RAW_INT", "fix_type": 3, "satellites_visible": 12, "eph": 85, "epv": 130},
            {"mavpackettype": "ATTITUDE", "roll": 0.02, "pitch": -0.01, "yaw": 1.57},
            {"mavpackettype": "VFR_HUD", "heading": 90, "groundspeed": 0.0, "alt": 12.5, "throttle": 0},
        ]

    @staticmethod
    def other_telemetry() -> list[dict]:
        """A second autopilot with values that must never show up for vehicle 1."""
        return [
            {"mavpackettype": "SYS_STATUS", "onboard_control_sensors_present": SENSORS,
             "onboard_control_sensors_enabled": SENSORS, "onboard_control_sensors_health": SENSORS & ~0x20,
             "voltage_battery": 11000, "current_battery": 500, "battery_remaining": 5},
            {"mavpackettype": "GPS_RAW_INT", "fix_type": 0, "satellites_visible": 0, "eph": 9999, "epv": 9999},
            {"mavpackettype": "STATUSTEXT", "severity": 2, "text": "PreArm: Vehicle 2 is not ready",
             "id": 0, "chunk_seq": 0},
        ]

    @staticmethod
    def send_chunked(mav, text: str, msg_id: int = 7) -> None:
        """Send a MAVLink2 chunked STATUSTEXT: 50-char chunks, sent out of order."""
        parts = [text[i:i + 50] for i in range(0, len(text), 50)]
        if len(parts[-1]) == 50:
            parts.append("")
        order = list(range(len(parts)))
        if len(order) > 2:
            order[0], order[1] = order[1], order[0]
        for i in order:
            mav.statustext_send(4, parts[i].encode("ascii"), msg_id, i)

    def _handle(self, ap, m):
        mtype = m.get_type()
        self.received.append((m.get_srcSystem(), m.get_srcComponent(), mtype, m.to_dict()))
        if not self.respond_requests:
            return
        if mtype == "PARAM_REQUEST_READ":
            name = m.param_id
            if name in self.params:
                v = self.params[name]
                ap.param_value_send(name.encode("ascii"), float(v), 9 if isinstance(v, float) else 6,
                                    len(self.params), list(self.params).index(name))
        elif mtype == "COMMAND_LONG":
            if m.command == 512:
                mid = int(m.param1)
                name = MSG_ID_TO_NAME.get(mid)
                result = 0 if name else 4
                ap.command_ack_send(512, result, 0, 0, m.get_srcSystem(), m.get_srcComponent())
                if name == "AUTOPILOT_VERSION":
                    ap.autopilot_version_send(0, (4 << 24) | (6 << 16) | (3 << 8) | 255, 0, 0, 0,
                                              list(b"abcdef12"), [0] * 8, [0] * 8, 0x3162, 0x0038, 0)
                elif name == "EKF_STATUS_REPORT":
                    ap.ekf_status_report_send(1 | 2 | 4 | 8 | 16 | 32 | 512, 0.1, 0.1, 0.1, 0.05, 0)
                elif name == "GLOBAL_POSITION_INT":
                    ap.global_position_int_send(0, 320000000, 348000000, 50000, 0, 0, 0, 0, 0)
                elif name == "HOME_POSITION":
                    ap.home_position_send(320000000, 348000000, 50000, 0, 0, 0, [1, 0, 0, 0], 0, 0, 0)
                elif name == "BATTERY_STATUS":
                    ap.battery_status_send(0, 1, 3, 3200, [16400] + [65535] * 9, 90, 1200, -1, 97)
                elif name == "ATTITUDE":
                    ap.attitude_send(0, 0.02, -0.01, 1.57, 0, 0, 0)
                elif name == "VFR_HUD":
                    ap.vfr_hud_send(0, 0, 90, 0, 12.5, 0)
            elif m.command == 401:
                self._prearm_command(ap, m)
            else:
                ap.command_ack_send(m.command, 3, 0, 0, m.get_srcSystem(), m.get_srcComponent())

    def _prearm_command(self, ap, m) -> None:
        src = (m.get_srcSystem(), m.get_srcComponent())
        if self.second_vehicle or self.prearm == "imposter":
            # Another system ACKs the same command to us - must not count.
            self._other.command_ack_send(401, 0, 0, 0, *src)
        if self.prearm in ("silent", "imposter"):
            return
        if self.armed:
            ap.command_ack_send(401, 1, 0, 0, *src)  # TEMPORARILY_REJECTED
            return
        if self.prearm == "unsupported":
            ap.command_ack_send(401, 3, 0, 0, *src)
            return
        ap.command_ack_send(401, 0, 0, 0, *src)  # ACCEPTED = checks were run, not passed
        if self.prearm == "fail":
            for t in self.prearm_texts:
                ap.statustext_send(2, t.encode("ascii")[:50], 0, 0)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Mock ArduPilot behind a mavlink-router TCP port")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5760)
    args = ap.parse_args(argv)
    m = MockAutopilot(args.host, args.port).start()
    print(f"mock autopilot listening on {args.host}:{m.port} (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        m.stop()


if __name__ == "__main__":
    main()
