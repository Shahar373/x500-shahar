"""Read-only MAVLink client.

Safety properties (enforced in :meth:`MavlinkClient._send` and tested):

* Never sends HEARTBEAT. ArduPilot only refreshes its GCS-failsafe timer from
  HEARTBEAT / RC_CHANNELS_OVERRIDE / MANUAL_CONTROL sent by a GCS sysid; this
  client sends none of them, so it can never mask a lost Mission Planner link.
* Uses its own sysid/compid (default 247/25), distinct from the GCS sysid.
* The only messages it may transmit are PARAM_REQUEST_READ and COMMAND_LONG
  carrying MAV_CMD_REQUEST_MESSAGE (a one-shot request that neither changes
  stream rates nor writes parameters) or MAV_CMD_RUN_PREARM_CHECKS (a
  non-actuating diagnostic, sent only on an explicit preflight action while a
  fresh HEARTBEAT shows the vehicle disarmed). Anything else raises.
* Active requests are optional (``request_messages``); in passive mode the
  client only listens.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from ..core.mav import constants as C
from ..core.session import SessionLog
from .mav_ingest import MavIngest
from .streams import ByteStream

log = logging.getLogger(__name__)

ALLOWED_COMMANDS = frozenset({C.MAV_CMD_REQUEST_MESSAGE, C.MAV_CMD_RUN_PREARM_CHECKS})
ALLOWED_MSG_TYPES = frozenset({"PARAM_REQUEST_READ", "COMMAND_LONG"})


class ForbiddenMessage(RuntimeError):
    pass


@dataclass
class MavConfig:
    source_sysid: int = 247
    source_compid: int = 25
    heartbeat_timeout_s: float = 3.0
    request_messages: bool = True
    request_interval_s: float = 2.0
    request_list: list[str] = field(default_factory=lambda: [
        "SYS_STATUS", "GPS_RAW_INT", "EKF_STATUS_REPORT", "GLOBAL_POSITION_INT", "BATTERY_STATUS",
        "ATTITUDE", "VFR_HUD",
    ])
    once_list: list[str] = field(default_factory=lambda: ["AUTOPILOT_VERSION", "HOME_POSITION"])
    reconnect_min_s: float = 1.0
    reconnect_max_s: float = 10.0
    prearm_ack_timeout_s: float = 3.0
    prearm_window_s: float = 6.0     # wait this long after ACCEPTED for a fresh SYS_STATUS


class _Writer:
    def __init__(self, stream: ByteStream):
        self.stream = stream

    def write(self, data: bytes) -> None:
        self.stream.send(data)


class MavlinkClient:
    def __init__(self, stream_factory: Callable[[], ByteStream], ingest: MavIngest, session: SessionLog,
                 config: MavConfig, describe: str = "MAVLink"):
        self.stream_factory = stream_factory
        self.ingest = ingest
        self.session = session
        self.cfg = config
        self.describe = describe
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._params: dict[str, dict] = {}
        self._once_pending: list[str] = list(config.once_list)
        self._home_retry_at = 0.0
        self.sent_log: list[tuple[str, int | None]] = []  # (msg type, command) for tests/diagnostics
        self._mav = None
        self._stream: ByteStream | None = None
        self._req_ack_misses = 0
        self._prearm_request = False
        self._prearm: dict | None = None

    # ------------------------------------------------------------------
    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mavlink", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        s = self._stream
        if s:
            s.close()
        if self._thread:
            self._thread.join(timeout)

    def refresh(self, params: list[str]) -> None:
        """Called on 'run preflight': re-read params and one-shot messages."""
        now = time.monotonic()
        with self._lock:
            for p in params:
                self._params[p] = {"tries": 0, "last_sent": 0.0, "requested_at": now}
            self._once_pending = list(self.cfg.once_list)

    def request_prearm_diagnostic(self) -> None:
        """Ask ArduPilot to run its pre-arm checks now (explicit preflight only)."""
        with self._lock:
            self._prearm_request = True

    # ------------------------------------------------------------------
    def _run(self) -> None:
        from pymavlink.dialects.v20 import ardupilotmega as mavlink2

        backoff = self.cfg.reconnect_min_s
        was_live = False
        while not self._stop.is_set():
            self.ingest.publish_link(False, "connecting")
            try:
                stream = self.stream_factory()
            except Exception as e:  # noqa: BLE001
                msg = f"{type(e).__name__}: {e}"
                self.ingest.publish_link(False, "failed", error=msg)
                self.session.link_down("mavlink", msg)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, self.cfg.reconnect_max_s)
                continue
            self._stream = stream
            self.ingest.endpoint = getattr(stream, "description", self.describe)
            mav = mavlink2.MAVLink(_Writer(stream), srcSystem=self.cfg.source_sysid,
                                   srcComponent=self.cfg.source_compid)
            mav.robust_parsing = True
            self._mav = mav
            self.ingest.publish_link(True, "waiting_heartbeat")
            last_req = 0.0
            last_param_batch = 0.0
            last_state_pub = 0.0
            last_state = ""
            pending_req: tuple[str, float] | None = None
            req_queue: list[str] = []
            try:
                while not self._stop.is_set():
                    data = stream.recv(4096)
                    now = time.monotonic()
                    if data:
                        try:
                            msgs = mav.parse_buffer(data) or []
                        except Exception:  # noqa: BLE001 - malformed input must not kill the link
                            self.ingest.note_bad_packet()
                            msgs = []
                        for m in msgs:
                            try:
                                d = m.to_dict()
                            except Exception:  # noqa: BLE001
                                self.ingest.note_bad_packet()
                                continue
                            self.ingest.handle(d, m.get_srcSystem(), m.get_srcComponent(), now)
                    # --- heartbeat freshness / link state -------------------
                    hb = self.ingest.store.get("mav.msg.HEARTBEAT")
                    fresh = hb is not None and now - hb.t_mono <= self.cfg.heartbeat_timeout_s
                    # Publish the link state before recording the transition, so anyone
                    # reacting to the session log already sees the new state.
                    state = "live" if fresh else ("stale" if hb else "waiting_heartbeat")
                    if now - last_state_pub > 0.5 or state != last_state:
                        last_state_pub, last_state = now, state
                        self.ingest.publish_link(True, state, requests=self.cfg.request_messages,
                                                 request_ack_misses=self._req_ack_misses)
                    if fresh and not was_live:
                        was_live = True
                        backoff = self.cfg.reconnect_min_s
                        self.session.link_up("mavlink")
                    elif not fresh and was_live:
                        was_live = False
                        self.session.link_down("mavlink", "HEARTBEAT timeout")
                    if self.ingest.target and fresh:
                        self._prearm_step(now)
                    if not self.cfg.request_messages or not self.ingest.target or not fresh:
                        continue
                    # --- one-shot message requests, one at a time -----------
                    if pending_req is not None:
                        name, sent = pending_req
                        ack = self.ingest.wait_ack(C.MAV_CMD_REQUEST_MESSAGE, sent, 0,
                                                   src=(self.ingest.target, C.MAV_COMP_ID_AUTOPILOT1))
                        if ack is not None:
                            pending_req = None
                            self._req_ack_misses = 0
                            if ack["result_name"] in ("UNSUPPORTED", "DENIED", "FAILED"):
                                self.ingest.mark_unsupported(name, ack["result_name"])
                        elif now - sent > 1.5:
                            pending_req = None
                            self._req_ack_misses += 1
                    if pending_req is None:
                        if not req_queue and now - last_req >= self.cfg.request_interval_s:
                            last_req = now
                            with self._lock:
                                once = list(self._once_pending)
                            req_queue = list(self.cfg.request_list) + once
                        if req_queue:
                            name = req_queue.pop(0)
                            self._request_message(name)
                            pending_req = (name, time.monotonic())
                            with self._lock:
                                if name in self._once_pending and name != "HOME_POSITION":
                                    self._once_pending.remove(name)
                                if name == "HOME_POSITION" and self.ingest.store.get("mav.msg.HOME_POSITION"):
                                    self._once_pending.remove(name)
                    # --- parameter reads, paced ----------------------------
                    if now - last_param_batch > 0.25:
                        last_param_batch = now
                        self._param_batch()
            except ConnectionError as e:
                self.session.link_down("mavlink", str(e))
                self.ingest.publish_link(False, "lost", error=str(e))
            except ForbiddenMessage:
                raise
            except Exception as e:  # noqa: BLE001
                log.exception("mavlink worker error")
                self.session.link_down("mavlink", f"{type(e).__name__}: {e}")
                self.ingest.publish_link(False, "lost", error=str(e))
            finally:
                was_live = False
                stream.close()
                self._stream = None
            self._stop.wait(backoff)
            backoff = min(backoff * 2, self.cfg.reconnect_max_s)
        self.ingest.publish_link(False, "disconnected")

    # ------------------------------------------------------------------
    def _send(self, msg) -> None:
        mtype = msg.get_type()
        if mtype not in ALLOWED_MSG_TYPES:
            raise ForbiddenMessage(f"refusing to send {mtype}")
        cmd = getattr(msg, "command", None) if mtype == "COMMAND_LONG" else None
        if mtype == "COMMAND_LONG" and cmd not in ALLOWED_COMMANDS:
            raise ForbiddenMessage(f"refusing to send COMMAND_LONG {cmd}")
        self._mav.send(msg)
        self.sent_log.append((mtype, cmd))
        if len(self.sent_log) > 2000:
            del self.sent_log[:1000]

    # ------------------------------------------------------------------ pre-arm diagnostic
    def _publish_prearm(self) -> None:
        self.ingest._put("mav.prearm_diag", dict(self._prearm))

    def _prearm_step(self, now: float) -> None:
        with self._lock:
            requested, self._prearm_request = self._prearm_request, False
        st = self._prearm
        if requested and (st is None or st.get("finished")):
            hb = self.ingest.store.get("mav.msg.HEARTBEAT")
            armed = None
            if hb is not None and now - hb.t_mono <= self.cfg.heartbeat_timeout_s:
                from ..core.mav import parse as P
                armed = P.is_armed(hb.value)
            base = {"requested_wall": time.time(), "finished": True, "texts": []}
            if not self.cfg.request_messages:
                self._prearm = {**base, "state": "not_sent", "reason": "passive mode (requests disabled)"}
            elif armed is None:
                self._prearm = {**base, "state": "not_sent", "reason": "no fresh vehicle HEARTBEAT - disarmed state not verified"}
            elif armed:
                self._prearm = {**base, "state": "not_sent", "reason": "vehicle armed"}
            else:
                msg = self._mav.command_long_encode(self.ingest.target, C.MAV_COMP_ID_AUTOPILOT1,
                                                    C.MAV_CMD_RUN_PREARM_CHECKS, 0, 0, 0, 0, 0, 0, 0, 0)
                self._send(msg)
                self._prearm = {**base, "state": "sent", "finished": False, "sent_mono": now}
            self._publish_prearm()
            return
        if st is None or st.get("finished"):
            return
        if st["state"] == "sent":
            ack = self.ingest.wait_ack(C.MAV_CMD_RUN_PREARM_CHECKS, st["sent_mono"], 0,
                                       src=(self.ingest.target, C.MAV_COMP_ID_AUTOPILOT1))
            if ack is not None:
                st.update(result=ack["result"], result_name=ack["result_name"], ack_mono=ack["t_mono"],
                          ack_from=f"{ack['src_sysid']}/{ack['src_compid']}")
                if ack["result_name"] == "ACCEPTED":
                    st["state"] = "accepted"
                else:
                    st.update(state="rejected", finished=True)
                self._publish_prearm()
            elif now - st["sent_mono"] > self.cfg.prearm_ack_timeout_s:
                st.update(state="no_ack", finished=True)
                self._publish_prearm()
            return
        if st["state"] == "accepted":
            ss = self.ingest.store.get("mav.msg.SYS_STATUS")
            fresh = ss is not None and ss.t_mono > st["ack_mono"]
            # PreArm STATUSTEXT is emitted while the checks run, i.e. around the ACK.
            st["texts"] = self.ingest.prearm_since(st["sent_mono"] - 0.5)
            if fresh and now - st["ack_mono"] >= 1.0:
                from ..core.mav import parse as P
                sens = P.sensors(ss.value) or {}
                st.update(state="complete", finished=True, prearm_bit=sens.get("prearm_check"),
                          sys_status_after_ack_s=round(ss.t_mono - st["ack_mono"], 2))
                self._publish_prearm()
            elif now - st["ack_mono"] > self.cfg.prearm_window_s:
                st.update(state="no_fresh_sys_status", finished=True)
                self._publish_prearm()

    def _request_message(self, name: str) -> None:
        msg_id = C.MSG_IDS[name]
        msg = self._mav.command_long_encode(
            self.ingest.target, C.MAV_COMP_ID_AUTOPILOT1, C.MAV_CMD_REQUEST_MESSAGE, 0,
            float(msg_id), 0, 0, 0, 0, 0, 0)
        self._send(msg)

    def _param_batch(self) -> None:
        """Send up to 10 PARAM_REQUEST_READ; retry unanswered names 3 times."""
        now = time.monotonic()
        batch = []
        with self._lock:
            for name in list(self._params):
                st = self._params[name]
                got = self.ingest.store.get(f"mav.param.{name}")
                if got is not None and got.t_mono >= st["requested_at"]:
                    del self._params[name]
                    continue
                if st["last_sent"] and now - st["last_sent"] < 3.0:
                    continue
                if st["tries"] >= 3:
                    del self._params[name]
                    self.ingest.mark_param_missing(name)
                    continue
                st["tries"] += 1
                st["last_sent"] = now
                batch.append(name)
                if len(batch) >= 10:
                    break
        for name in batch:
            msg = self._mav.param_request_read_encode(self.ingest.target, C.MAV_COMP_ID_AUTOPILOT1,
                                                      name.encode("ascii"), -1)
            self._send(msg)

    def pending_params(self) -> int:
        with self._lock:
            return len(self._params)
