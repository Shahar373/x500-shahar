"""MAVLink ingestion shared by the live client and the simulator.

Takes decoded MAVLink messages as plain dicts (``msg.to_dict()``) plus the
sender's system/component id and records them in the observation store.
The simulator feeds the very same code path, so simulation exercises the real
parsing and filtering logic.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

from ..core.mav import constants as C
from ..core.mav import parse as P
from ..core.observations import ObservationStore
from ..core.session import SessionLog

TextCallback = Callable[[dict], None]


class MavIngest:
    def __init__(
        self,
        store: ObservationStore,
        session: SessionLog,
        *,
        simulated: bool,
        endpoint: str,
        target_sysid: int = 0,
        my_sysid: int = 247,
        my_compid: int = 25,
        on_text: TextCallback | None = None,
    ):
        self.store = store
        self.session = session
        self.simulated = simulated
        self.endpoint = endpoint
        self.fixed_target = target_sysid or 0
        self.target = target_sysid or 0
        self.my_sysid = my_sysid
        self.my_compid = my_compid
        self.on_text = on_text
        self.bad_packets = 0
        self.msgs_rx = 0
        self._lock = threading.Lock()
        self._prearm = deque(maxlen=50)
        self._chunks: dict[int, list[str]] = {}
        self.acks: deque = deque(maxlen=50)
        self._ack_event = threading.Event()
        self.connected = False
        self.state = "disconnected"

    # ------------------------------------------------------------------
    def _put(self, key, value, t_mono=None):
        self.store.put(key, value, self.endpoint, simulated=self.simulated, t_mono=t_mono)

    def publish_link(self, connected: bool, state: str, error: str | None = None, **extra) -> None:
        self.connected = connected
        self.state = state
        self._put("mav.link", {
            "connected": connected,
            "state": state,
            "endpoint": self.endpoint,
            "target_sysid": self.target or None,
            "bad_packets": self.bad_packets,
            "msgs_rx": self.msgs_rx,
            "error": error,
            "my_sysid": self.my_sysid,
            "my_compid": self.my_compid,
            **extra,
        })

    def note_bad_packet(self) -> None:
        self.bad_packets += 1

    # ------------------------------------------------------------------
    def handle(self, d: dict, sysid: int, compid: int, t_mono: float | None = None) -> None:
        mtype = d.get("mavpackettype")
        if not isinstance(mtype, str):
            self.note_bad_packet()
            return
        if mtype == "BAD_DATA":
            self.note_bad_packet()
            return
        if sysid == self.my_sysid and compid == self.my_compid:
            return  # our own traffic echoed back (should not happen with mavlink-router)
        self.msgs_rx += 1
        t_mono = time.monotonic() if t_mono is None else t_mono

        if mtype == "HEARTBEAT":
            vehicle = P.is_autopilot_heartbeat(d) and compid == C.MAV_COMP_ID_AUTOPILOT1
            if vehicle and not self.target:
                self.target = sysid
                self.session.event("info", "mavlink", f"זוהה בקר טיסה sysid={sysid}")
                self.publish_link(self.connected, self.state)
            if not vehicle or sysid != self.target:
                self._put(f"mav.peer.{sysid}.{compid}", dict(d), t_mono)
                return

        if not self.target or sysid != self.target or compid != C.MAV_COMP_ID_AUTOPILOT1:
            # Ignore other systems/components except their heartbeats (above).
            if mtype == "COMMAND_ACK":
                self._handle_ack(d, sysid, compid)
            return

        if mtype == "PARAM_VALUE":
            pv = P.param_value(d)
            if pv is None:
                self.note_bad_packet()
                return
            name, value = pv
            self._put(f"mav.param.{name}", {
                "value": value, "type": d.get("param_type"),
                "index": d.get("param_index"), "count": d.get("param_count"),
            }, t_mono)
            self.store.remove_prefix(f"mav.param_missing.{name}")
            return
        if mtype == "STATUSTEXT":
            self._handle_text(d, t_mono)
            return
        if mtype == "COMMAND_ACK":
            self._handle_ack(d, sysid, compid)
            return
        self._put(f"mav.msg.{mtype}", dict(d), t_mono)

    # ------------------------------------------------------------------
    def _handle_ack(self, d: dict, sysid: int, compid: int) -> None:
        ts = d.get("target_system")
        if ts not in (None, 0, self.my_sysid):
            return  # someone else's command (e.g. Mission Planner)
        self.acks.append({
            "command": d.get("command"),
            "result": d.get("result"),
            "result_name": C.MAV_RESULT_NAMES.get(d.get("result"), str(d.get("result"))),
            "t_mono": time.monotonic(),
        })
        self._ack_event.set()

    def wait_ack(self, command: int, after: float, timeout: float) -> dict | None:
        deadline = time.monotonic() + timeout
        while True:
            for a in list(self.acks):
                if a["command"] == command and a["t_mono"] >= after:
                    return a
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            self._ack_event.clear()
            self._ack_event.wait(min(left, 0.1))

    def mark_unsupported(self, msg_name: str, result_name: str) -> None:
        self._put(f"mav.unsupported.{msg_name}", result_name)

    def mark_param_missing(self, name: str) -> None:
        self._put(f"mav.param_missing.{name}", True)

    def _handle_text(self, d: dict, t_mono: float) -> None:
        st = P.statustext(d)
        if st is None:
            self.note_bad_packet()
            return
        text = st["text"]
        if st["id"]:
            # MAVLink2 chunked statustext: collect until a short/terminated chunk.
            parts = self._chunks.setdefault(st["id"], [])
            parts.append(text)
            if len(text) >= 50 and len(parts) < 20:
                return
            text = "".join(self._chunks.pop(st["id"], []))
        item = {
            "t_wall": time.time(),
            "t_mono": t_mono,
            "severity": st["severity"],
            "severity_name": st["severity_name"],
            "text": text,
            "source": f"sysid {self.target}",
        }
        if P.is_prearm_text(text):
            with self._lock:
                self._prearm.append({"text": text, "t_mono": t_mono})
                snapshot = list(self._prearm)
            self._put("mav.prearm_texts", snapshot, t_mono)
        if self.on_text:
            try:
                self.on_text(item)
            except Exception:  # noqa: BLE001 - UI callback must not kill the worker
                pass
