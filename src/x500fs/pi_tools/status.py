"""``x500-status``: human system + telemetry status on the Pi (read-only).

Pi values come from the shared collector; unavailable values are printed as
"unavailable", never as zero. Telemetry comes from one short MAVLink session
through mavlink-router, filtered to the selected vehicle, with the age of every
message shown. Nothing is sent except read-only message requests.
"""

from __future__ import annotations

import argparse
import math
import sys
import time

from ..core.mav import parse as P
from ..core.observations import ObservationStore
from ..core.session import SessionLog
from ..pi_side import x500fs_collector as collector
from . import conf as conf_mod
from .preflight import parse_endpoint

NA = "unavailable"


def _fmt(v, unit="", nd=1):
    if v is None:
        return NA
    if isinstance(v, float):
        return f"{v:.{nd}f}{unit}"
    return f"{v}{unit}"


def pi_lines(doc: dict) -> list[str]:
    s = doc["sections"]

    def d(name):
        sec = s.get(name) or {}
        return sec.get("data") if sec.get("ok") else None

    out = ["[PI]"]
    ident = d("identity") or {}
    out.append(f"Host:          {ident.get('hostname', NA)}  {ident.get('model') or ''}".rstrip())
    up = d("uptime") or {}
    out.append(f"Uptime:        {_fmt(round(up['uptime_s'] / 60) if up.get('uptime_s') else None, ' min')}")
    th = d("thermal") or {}
    out.append(f"CPU temp:      {_fmt(th.get('cpu_temp_c'), ' C')}"
               + (f"  (vcgencmd: {th['vcgencmd_error']})" if th.get("cpu_temp_c") is None and th.get("vcgencmd_error") else ""))
    pw = d("power") or {}
    dec = pw.get("throttled_decoded")
    if dec is None:
        out.append(f"Power flags:   {NA} ({pw.get('vcgencmd_error') or 'no data'})")
    else:
        now = [k for k, v in (dec.get("now") or {}).items() if v]
        hist = [k for k, v in (dec.get("since_boot") or {}).items() if v]
        out.append(f"Power now:     {'OK' if not now else 'FAULT: ' + ', '.join(now)}  ({pw.get('throttled_raw')})")
        out.append(f"Since boot:    {'none' if not hist else ', '.join(hist)}")
    mem = d("memory") or {}
    out.append(f"RAM available: {_fmt(mem.get('available_pct'), '%')}")
    st = d("storage") or {}
    for m in st.get("mounts") or []:
        total, free = m.get("total_mb"), m.get("free_mb")
        used = round(100.0 * (1 - free / total), 1) if total and free is not None else None
        out.append(f"Disk {m.get('path', '?'):<9} {_fmt(used, '% used')} ({_fmt(free, ' MB free', 0)})"
                   + (" READ-ONLY" if m.get("read_only") else ""))
    if not st.get("mounts"):
        out.append(f"Disk:          {NA}")
    cpu = d("cpu") or {}
    out.append(f"Load:          {cpu.get('loadavg', NA)}")
    return out


def service_lines(doc: dict) -> list[str]:
    s = doc["sections"]
    out = ["", "[SERVICES]"]
    units = ((s.get("services") or {}).get("data") or {}).get("units") or {}
    for unit, info in units.items():
        if "ActiveState" in info:
            out.append(f"{unit:<22} {info['ActiveState']}/{info.get('SubState', '')}")
        else:
            out.append(f"{unit:<22} {NA}: {' '.join(str(info.get('error', '')).split())[:90]}")
    v = (s.get("video") or {}).get("data") or {}
    prof = v.get("active_profile")
    prog = v.get("progress") or {}
    if prof:
        age = prog.get("mtime_age_s")
        enc = ("encoder progressing" if isinstance(age, (int, float)) and age <= 3 else
               "no encoder progress (waiting for an SRT caller?)")
        out.append(f"x500-video@{prof:<11} active · {enc}")
        out.append("  (encoder progress is not proof that a viewer shows a live picture)")
    elif v.get("units") and all("error" in u for u in v["units"].values()):
        out.append(f"x500-video            {NA} (systemctl failed)")
    else:
        out.append("x500-video            stopped")
    wd = (s.get("watchdog") or {}).get("data") or {}
    age = wd.get("last_ping_age_s")
    out.append("Watchdog:             " + (f"active (last ping {age:.1f}s ago)" if isinstance(age, (int, float))
                                          else "runtime proof unavailable (" + (wd.get("last_ping_note") or "no data") + ")"))
    return out


def network_lines(doc: dict) -> list[str]:
    s = doc["sections"]
    net = (s.get("network") or {}).get("data") or {}
    probes = (s.get("net_probes") or {}).get("data") or {}
    out = ["", "[NETWORK]"]
    for i in net.get("interfaces") or []:
        addrs = ",".join(a.get("addr", "") for a in i.get("addrs") or [] if a.get("family") == "inet")
        out.append(f"{i.get('name', '?'):<12} {i.get('kind', ''):<10} {i.get('operstate', ''):<6} {addrs}")
    route = probes.get("internet_route") or {}
    out.append(f"Internet route: dev={route.get('dev', NA)} via={route.get('gateway', '-')}")
    for name, p in (probes.get("probes") or {}).items():
        if p.get("configured") is False:
            out.append(f"ICMP {name:<16} not configured")
        elif p.get("ok"):
            out.append(f"ICMP {name:<16} reply" + (f" {p.get('rtt_ms')}ms" if p.get("rtt_ms") is not None else ""))
        else:
            out.append(f"ICMP {name:<16} " + (f"probe failed: {p['error']}" if p.get("error") else "no reply"))
    ts = probes.get("tailscale") or {}
    out.append(f"Tailscale:      service={ts.get('service', NA)} ipv4={ts.get('ipv4') or NA}")
    out.append("  (ICMP replies show reachability only; they do not measure MAVLink quality)")
    return out


def telemetry_lines(store: ObservationStore, link: dict) -> list[str]:
    now = time.monotonic()
    out = ["", f"[VEHICLE] via {link.get('endpoint')} sysid={link.get('target_sysid') or NA}"]

    def msg(name, max_age=5.0):
        o = store.get(f"mav.msg.{name}")
        if o is None:
            return None, None
        return o.value, now - o.t_mono

    def age(a):
        return f"  ({a:.1f}s ago)" if a is not None else ""

    hb, a = msg("HEARTBEAT")
    if hb:
        out.append(f"Armed:         {'ARMED' if P.is_armed(hb) else 'disarmed'}{age(a)}")
        out.append(f"Mode:          {P.copter_mode_name(hb) or NA}{age(a)}")
    else:
        out.append(f"Armed/Mode:    {NA} (no vehicle HEARTBEAT)")
    att, a = msg("ATTITUDE")
    if att and all(isinstance(att.get(k), float) for k in ("roll", "pitch", "yaw")):
        out.append(f"Roll/Pitch/Yaw: {math.degrees(att['roll']):.1f} / {math.degrees(att['pitch']):.1f} / "
                   f"{(math.degrees(att['yaw']) + 360) % 360:.0f} deg{age(a)}")
    else:
        out.append(f"Roll/Pitch/Yaw: {NA}")
    g, a = msg("GPS_RAW_INT")
    if g:
        gp = P.gps_raw(g)
        out.append(f"GPS:           {gp['fix_name']} sats={_fmt(gp['satellites'])} hdop={_fmt(gp['hdop'])}{age(a)}")
    else:
        out.append(f"GPS:           {NA}")
    ss, a = msg("SYS_STATUS")
    if ss:
        b = P.battery_from_sys_status(ss)
        out.append(f"Battery:       {_fmt(b['voltage_v'], ' V', 2)} {_fmt(b['current_a'], ' A')} "
                   f"remaining={_fmt(b['remaining_pct'], '%')}{age(a)}")
    else:
        out.append(f"Battery:       {NA}")
    e, a = msg("EKF_STATUS_REPORT")
    if e:
        st = P.ekf_status(e) or {}
        flags = [k for k, on in (st.get("flags") or {}).items() if on]
        out.append(f"EKF flags:     {', '.join(flags) or 'none'}{age(a)}")
    else:
        out.append(f"EKF:           {NA}")
    home, a = msg("HOME_POSITION")
    out.append(f"Home:          {'set' if home else NA}{age(a)}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="x500-status", description="X500 system and telemetry status (read-only)")
    ap.add_argument("--config", default=conf_mod.DEFAULT_PATH)
    ap.add_argument("--seconds", type=float, default=4.0, help="MAVLink listening time")
    ap.add_argument("--no-mavlink", action="store_true")
    ap.add_argument("--camera", action="store_true", help="also enumerate cameras (slow)")
    args = ap.parse_args(argv)
    conf = conf_mod.load(args.config)
    cfg = dict(collector.DEFAULT_CONFIG)
    cfg.update(conf_mod.collector_config(conf))
    cfg["camera_probe"] = args.camera
    only = collector.FAST_SECTIONS + ["network", "net_probes"] + (["camera"] if args.camera else [])
    doc = collector.collect(cfg, only)
    lines = ["=" * 40, " X500 Status", "=" * 40] + pi_lines(doc) + service_lines(doc) + network_lines(doc)
    if args.camera:
        cams = ((doc["sections"].get("camera") or {}).get("data") or {}).get("cameras")
        lines += ["", "[CAMERA]", f"Detected:      {cams if cams is not None else NA}"]
    if not args.no_mavlink:
        from ..transport.mav_ingest import MavIngest
        from ..transport.mavlink_client import MavConfig, MavlinkClient
        from ..transport.streams import TcpStream

        store, session = ObservationStore(simulated=False), SessionLog()
        try:
            host, port = parse_endpoint(conf["MAVLINK_ENDPOINT"])
            ing = MavIngest(store, session, simulated=False, endpoint=f"tcp:{host}:{port}",
                            target_sysid=int(conf["TARGET_SYSID"] or 0), my_sysid=int(conf["MAVLINK_SYSID"]),
                            my_compid=int(conf["MAVLINK_COMPID"]))
            c = MavlinkClient(lambda: TcpStream(host, port), ing, session,
                              MavConfig(source_sysid=int(conf["MAVLINK_SYSID"]),
                                        source_compid=int(conf["MAVLINK_COMPID"]), request_interval_s=1.0))
            c.start()
            time.sleep(max(1.0, args.seconds))
            c.stop()
            link = (store.get("mav.link").value if store.get("mav.link") else {}) | {"endpoint": f"tcp:{host}:{port}"}
            lines += telemetry_lines(store, link)
        except Exception as e:  # noqa: BLE001
            lines += ["", f"[VEHICLE] {NA}: {type(e).__name__}: {e}"]
    lines += ["=" * 40]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
