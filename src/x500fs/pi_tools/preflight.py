"""``x500-preflight [bench|flight]`` on the Pi, rebuilt on the shared evaluation layer.

* Pi observations come from the same read-only collector the desktop app streams.
* MAVLink: ONE connection to mavlink-router (default tcp:127.0.0.1:5760), the
  vehicle selected explicitly (TARGET_SYSID) or by its autopilot HEARTBEAT, and
  every message filtered by source system/component. Read-only requests only;
  no HEARTBEAT is sent. MAV_CMD_RUN_PREARM_CHECKS is requested only while the
  vehicle is verified disarmed, and its ACK result and source are validated.
* The same PASS/WARN/FAIL/UNKNOWN policy and profiles (bench.json, flight.json;
  an edited copy in /etc/x500/profiles/ takes precedence) as the app.

Legacy exit codes: 0 = PASS, 1 = PASS WITH WARNINGS, 2 = FAIL. A critical check
that cannot be confirmed (INCOMPLETE) also exits 2 - it is never a pass.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import time
from pathlib import Path

from .. import __version__
from ..core import profile as profile_mod
from ..core.checks import Ctx, DroneConfig, evaluate
from ..core.observations import ObservationStore
from ..core.params import all_params
from ..core.session import SessionLog
from ..core.status import Overall, Status, aggregate
from ..pi_side import x500fs_collector as collector
from . import conf as conf_mod

EXIT = {Overall.PASS: 0, Overall.PASS_WITH_WARNINGS: 1, Overall.FAIL: 2, Overall.INCOMPLETE: 2,
        Overall.NOT_RUN: 2}
_BIDI = re.compile("[⁦-⁩‎‏]")


def plain(text) -> str:
    return " ".join(_BIDI.sub("", str(text or "")).split())


def parse_endpoint(ep: str) -> tuple[str, int]:
    m = re.fullmatch(r"(?:tcp:)?([^:]+):(\d+)", ep.strip())
    if not m:
        raise ValueError(f"unsupported MAVLINK_ENDPOINT {ep!r} (expected tcp:HOST:PORT)")
    return m.group(1), int(m.group(2))


def collect_pi(store: ObservationStore, conf: dict) -> dict:
    cfg = dict(collector.DEFAULT_CONFIG)
    cfg.update(conf_mod.collector_config(conf))
    doc = collector.collect(cfg)
    now = time.monotonic()
    slow = set(collector.SLOW_SECTIONS)
    for name, sec in doc["sections"].items():
        if isinstance(sec, dict):
            sec["_received_mono"] = now
            sec["_cadence"] = "slow" if name in slow else "fast"
    store.put("pi.collector", doc, "local collector", simulated=False)
    store.put("pi.ssh", {"state": "local", "host": socket.gethostname(), "port": 0, "user": "local"},
              "local", simulated=False)
    return doc


def collect_mavlink(store, session, conf, profile, timeout_s: float, prearm: bool, log) -> dict:
    from ..transport.mav_ingest import MavIngest
    from ..transport.mavlink_client import MavConfig, MavlinkClient
    from ..transport.streams import TcpStream

    host, port = parse_endpoint(conf["MAVLINK_ENDPOINT"])
    sysid, compid = int(conf["MAVLINK_SYSID"]), int(conf["MAVLINK_COMPID"])
    ingest = MavIngest(store, session, simulated=False, endpoint=f"tcp:{host}:{port}",
                       target_sysid=int(conf["TARGET_SYSID"] or 0), my_sysid=sysid, my_compid=compid)
    client = MavlinkClient(lambda: TcpStream(host, port), ingest, session,
                           MavConfig(source_sysid=sysid, source_compid=compid, request_messages=True,
                                     request_interval_s=1.0, reconnect_max_s=2.0))
    extra = [n for group in (profile.expected_params or {}).values() for n in group]
    client.start()
    client.refresh(all_params(extra))
    if prearm:
        client.request_prearm_diagnostic()
    deadline = time.monotonic() + timeout_s
    want = ("HEARTBEAT", "SYS_STATUS", "GPS_RAW_INT", "EKF_STATUS_REPORT", "AUTOPILOT_VERSION")
    try:
        while time.monotonic() < deadline:
            have = all(store.get(f"mav.msg.{m}") for m in want)
            diag = store.get("mav.prearm_diag")
            diag_done = not prearm or (diag is not None and diag.value.get("finished"))
            if have and client.pending_params() == 0 and diag_done:
                break
            time.sleep(0.2)
    finally:
        client.stop()
    link = store.get("mav.link")
    return {"endpoint": f"tcp:{host}:{port}", "target_sysid": ingest.target or None,
            "params_pending": client.pending_params(), "state": link.value.get("state") if link else None}


def drone_from_conf(conf: dict) -> DroneConfig:
    return DroneConfig(
        name="X500", expected_hostname=conf["EXPECTED_HOSTNAME"], link_path=conf["LINK_PATH"] or "wifi",
        services=dict(collector.DEFAULT_CONFIG["services"]),
        blackbox_globs=conf_mod.collector_config(conf)["blackbox_globs"],
        mav_source_sysid=int(conf["MAVLINK_SYSID"]), mav_source_compid=int(conf["MAVLINK_COMPID"]),
        mav_target_sysid=int(conf["TARGET_SYSID"] or 0), watchdog_expected_runtime=conf["WATCHDOG_EXPECTED"],
        blackbox_sleep_s=float(conf["BLACKBOX_INTERVAL"] or 10), expected_fc_byid=conf["EXPECTED_FC_BYID"],
        ping_target_configured=bool(conf["PING_TARGET"]), zte_gateway_configured=bool(conf["ZTE_GATEWAY"]),
        ground_ip_configured=bool(conf["GROUND_IP"]), evaluator="pi_local",
        zte_gateway=conf["ZTE_GATEWAY"], ground_ip=conf["GROUND_IP"], lte_interface=conf["LTE_IF"],
    )


def load_profile(mode: str, profile_dir: str):
    prof, err = profile_mod.load(mode, Path(profile_dir) if profile_dir else None)
    return prof, err


def render(results, overall, mode, meta, profile, err=None) -> str:
    out = ["=" * 40, " X500 Companion Computer Preflight", f" Mode: {mode}  (profile {profile.id}, {profile.source}, sha256 {profile.sha256[:12]})",
           " Evaluator: x500fs " + __version__ + " (same checks as the desktop app)", "=" * 40, ""]
    if err:
        out += [f"[WARN] profile file invalid, using built-in: {err}", ""]
    order = {Status.FAIL: 0, Status.UNKNOWN: 1, Status.WARN: 2, Status.PASS: 3, Status.NOT_APPLICABLE: 4}
    tag = {Status.PASS: "PASS", Status.WARN: "WARN", Status.FAIL: "FAIL", Status.UNKNOWN: "UNKN",
           Status.NOT_APPLICABLE: "N/A "}
    for r in sorted(results, key=lambda r: (order[r.status], r.check_id)):
        crit = "*" if r.critical else " "
        line = f"[{tag[r.status]}]{crit} {r.check_id:<24} {plain(r.observed)[:110]}"
        out.append(line)
        if r.status in (Status.FAIL, Status.UNKNOWN, Status.WARN):
            out.append(f"         {plain(r.explanation)[:220]}")
    c = overall.counts
    out += ["", "=" * 40, " SUMMARY", "=" * 40,
            f"PASS: {c.get('PASS', 0)}", f"WARN: {c.get('WARN', 0)}", f"FAIL: {c.get('FAIL', 0)}",
            f"UNKNOWN: {c.get('UNKNOWN', 0)}", f"N/A: {c.get('NOT_APPLICABLE', 0)}",
            f"MAVLink: {meta.get('mavlink', {}).get('endpoint')} vehicle sysid={meta.get('mavlink', {}).get('target_sysid')}",
            "(* = critical in this profile)", ""]
    label = {Overall.PASS: "PASS", Overall.PASS_WITH_WARNINGS: "PASS WITH WARNINGS", Overall.FAIL: "FAIL",
             Overall.INCOMPLETE: "INCOMPLETE (critical checks could not be confirmed)",
             Overall.NOT_RUN: "NOT RUN"}[overall.status]
    out.append(f"PREFLIGHT: {label}")
    if overall.blocking:
        out.append("Blocking: " + ", ".join(overall.blocking))
    out.append("Automatic checks only - complete the manual checklist. This is not a guarantee of a safe flight.")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="x500-preflight", description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", nargs="?", default="bench", choices=["bench", "flight"])
    ap.add_argument("--config", default=conf_mod.DEFAULT_PATH)
    ap.add_argument("--profile-dir", default="/etc/x500/profiles")
    ap.add_argument("--timeout", type=float, default=20.0, help="MAVLink collection time limit (s)")
    ap.add_argument("--no-mavlink", action="store_true", help="Pi checks only")
    ap.add_argument("--no-prearm-diagnostic", action="store_true")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)
    conf = conf_mod.load(args.config)
    prof, err = load_profile(args.mode, args.profile_dir)
    store, session = ObservationStore(simulated=False), SessionLog()
    meta: dict = {"config": args.config, "mode": args.mode}
    try:
        collect_pi(store, conf)
    except Exception as e:  # noqa: BLE001
        store.put("pi.collector_error", f"{type(e).__name__}: {e}", "local", simulated=False)
    if not args.no_mavlink:
        try:
            meta["mavlink"] = collect_mavlink(store, session, conf, prof, args.timeout,
                                              not args.no_prearm_diagnostic, print)
        except Exception as e:  # noqa: BLE001
            meta["mavlink"] = {"error": f"{type(e).__name__}: {e}"}
    results = evaluate(Ctx(store.snapshot(), prof, drone_from_conf(conf), session))
    overall = aggregate(results)
    if args.json:
        print(json.dumps({"schema": "x500fs.pi_preflight/1", "version": __version__, "meta": meta,
                          "profile": prof.summary(), "profile_error": err, "overall": overall.to_dict(),
                          "results": [r.to_dict() for r in results]}, ensure_ascii=False, indent=1, default=str))
    else:
        print(render(results, overall, args.mode, meta, prof, err))
    return EXIT[overall.status]


if __name__ == "__main__":
    sys.exit(main())
