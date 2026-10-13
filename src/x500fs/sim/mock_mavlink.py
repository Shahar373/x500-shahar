"""A mock mavlink-router TCP endpoint that speaks real MAVLink2 bytes.

Used by tests and for development without hardware::

    python -m x500fs.sim.mock_mavlink --port 5760

It emits autopilot telemetry (sysid 1) plus a Mission-Planner-like GCS
heartbeat (sysid 255), answers PARAM_REQUEST_READ and MAV_CMD_REQUEST_MESSAGE,
and records every message clients send so tests can prove the app never sends
HEARTBEAT or any command other than REQUEST_MESSAGE.
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
    "SYS_STATUS": 1, "GPS_RAW_INT": 24, "GLOBAL_POSITION_INT": 33, "AUTOPILOT_VERSION": 148,
    "BATTERY_STATUS": 147, "EKF_STATUS_REPORT": 193, "HOME_POSITION": 242,
}.items()}


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
                 send_gcs_heartbeat: bool = True, armed: bool = False, respond_requests: bool = True):
        self.params = dict(SIM_PARAMS if params is None else params)
        self.send_gcs_heartbeat = send_gcs_heartbeat
        self.armed = armed
        self.respond_requests = respond_requests
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
                    if now - last["fast"] >= 0.5:
                        last["fast"] = now
                        for d in self.telemetry(now - t0):
                            ap.send(encode(ap, d))
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
        return [
            {"mavpackettype": "SYS_STATUS", "onboard_control_sensors_present": 0x1321002F,
             "onboard_control_sensors_enabled": 0x1321002F, "onboard_control_sensors_health": 0x1321002F,
             "voltage_battery": 16400 + int(30 * math.sin(t)), "current_battery": 90, "battery_remaining": 97},
            {"mavpackettype": "GPS_RAW_INT", "fix_type": 3, "satellites_visible": 12, "eph": 85, "epv": 130},
        ]

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
            else:
                ap.command_ack_send(m.command, 3, 0, 0, m.get_srcSystem(), m.get_srcComponent())


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
