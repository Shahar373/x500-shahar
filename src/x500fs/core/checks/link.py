"""Communication checks: actual path, RTT, interruptions, LTE, MAVLink freshness."""

from __future__ import annotations

from ..mav import parse as P
from ..status import Status
from .base import R, Unavailable, check, fmt_age, ltr, num

PATH_LABEL = {"wifi": "Wi-Fi", "lte": "LTE", "tailscale": "Tailscale", "ethernet": "Ethernet"}
NOT_MAVLINK = "הצלחת ping או גישה לאינטרנט אינן מעידות על תקינות MAVLink - זה נבדק בנפרד לפי HEARTBEAT."


def _iface_summary(ifaces):
    parts = []
    for i in ifaces:
        if i.get("name") == "lo" or i.get("operstate") not in ("up", "unknown"):
            continue
        addrs = [a.split("/")[0] for a in (i.get("ipv4") or [])]
        if not addrs:
            continue
        parts.append(f"{i.get('name')}({i.get('kind', '?')}) {', '.join(addrs)}")
    return parts


@check("link.path", "link", "נתיב התקשורת בפועל")
def link_path(ctx, cfg):
    data, age = ctx.require_section("network")
    selected = ctx.drone.link_path
    src = ctx.src("Pi collector · SSH_CONNECTION + ip addr/route")
    arrival = data.get("ssh_arrival") if isinstance(data, dict) else None
    if not isinstance(arrival, dict) or not arrival.get("dev"):
        raise Unavailable(R(Status.UNKNOWN, "לא ניתן היה לקבוע באיזה ממשק הגיע החיבור ל-Pi.",
                            source=src, age_s=age, evidence={"arrival": arrival}))
    kind = arrival.get("kind")
    dev = arrival.get("dev")
    ts = data.get("tailscale") or {}
    underlay_kind = ts.get("underlay_kind")
    underlay_dev = ts.get("underlay_dev")
    relay = (ts.get("peer") or {}).get("relay")
    direct = (ts.get("peer") or {}).get("direct")
    desc = f"{ltr(dev)} ({PATH_LABEL.get(kind, kind)})"
    if kind == "tailscale":
        mode = "ישיר" if direct else (f"דרך ממסר DERP {ltr(relay)}" if relay else "לא ידוע")
        desc += f" · Tailscale {mode}"
        if underlay_dev:
            desc += f" מעל {ltr(underlay_dev)} ({PATH_LABEL.get(underlay_kind, underlay_kind)})"
    expected = PATH_LABEL.get(selected, selected)
    ev = {"arrival": arrival, "tailscale": ts}

    if selected == "wifi":
        ok = kind == "wifi"
    elif selected == "tailscale":
        ok = kind == "tailscale"
    elif selected == "lte":
        if kind == "lte":
            ok = True
        elif kind == "tailscale":
            if underlay_kind is None:
                return R(Status.UNKNOWN, "החיבור עובר ב-Tailscale אך לא ניתן לקבוע אם התעבורה עוברת ב-LTE.",
                         observed=desc, expected=f"{expected} (דרך Tailscale)", source=src, age_s=age, evidence=ev)
            ok = underlay_kind == "lte"
            expected = "Tailscale מעל LTE"
        else:
            ok = False
    else:
        return R(Status.UNKNOWN, f"נתיב לא מוכר בהגדרות: {ltr(selected)}", observed=desc,
                 source=src, age_s=age, configured=False, evidence=ev)
    if ok:
        return R(Status.PASS, "החיבור ל-Pi עובר בנתיב שנבחר.", observed=desc, expected=expected,
                 source=src, age_s=age, evidence=ev)
    return R(Status.FAIL, "החיבור ל-Pi עובר בנתיב שונה מזה שנבחר בפרופיל.", observed=desc,
             expected=expected, source=src, age_s=age, evidence=ev)


@check("link.rtt", "link", "זמן תגובה (RTT) לאפליקציה")
def link_rtt(ctx, cfg):
    summ = ctx.session.rtt.summary()
    src = ctx.src("SSH keepalive request/reply (רמת אפליקציה)")
    lim = "נמדד כבקשה/תשובה של SSH מהמחשב ל-Pi. אינו מודד את זמן ההשהיה של MAVLink או של הווידאו."
    if not summ.get("count"):
        if summ.get("timeouts"):
            return R(Status.FAIL, "כל מדידות ה-RTT נכשלו (timeout).", observed=f"timeouts={summ['timeouts']}",
                     source=src, limitations=lim, evidence=summ)
        raise Unavailable(R(Status.UNKNOWN, "אין עדיין מדידות RTT.", source=src, limitations=lim))
    path = ctx.drone.link_path
    warn_cfg, fail_cfg = cfg.get("warn_ms"), cfg.get("fail_ms")
    warn = num(warn_cfg.get(path)) if isinstance(warn_cfg, dict) else num(warn_cfg)
    fail = num(fail_cfg.get(path)) if isinstance(fail_cfg, dict) else num(fail_cfg)
    med = summ["median_ms"]
    last, mx, n = summ["last_ms"], summ["max_ms"], summ["count"]
    observed = (f"חציון {ltr(f'{med:.0f} ms')} · אחרון {ltr(f'{last:.0f} ms')}"
                f" · מקס {ltr(f'{mx:.0f} ms')} · {ltr(f'n={n}')}")
    if summ.get("timeouts"):
        observed += f" · timeouts={summ['timeouts']}"
    expected = (ltr(f"< {warn:g} ms / < {fail:g} ms") + f" ({PATH_LABEL.get(path, path)})") if warn and fail else "לא מוגדר"
    if warn is None and fail is None:
        return R(Status.UNKNOWN, "לא הוגדרו ספי RTT לנתיב הזה.", observed=observed, source=src,
                 configured=False, limitations=lim, evidence=summ)
    if fail is not None and med >= fail:
        return R(Status.FAIL, "זמן תגובה גבוה מאוד.", observed=observed, expected=expected, source=src,
                 limitations=lim, evidence=summ)
    if (warn is not None and med >= warn) or summ.get("timeouts"):
        return R(Status.WARN, "זמן תגובה גבוה או שהיו מדידות שלא נענו.", observed=observed, expected=expected,
                 source=src, limitations=lim, evidence=summ)
    return R(Status.PASS, "זמן התגובה בטווח המוגדר.", observed=observed, expected=expected, source=src,
             limitations=lim, evidence=summ)


@check("link.interruptions", "link", "ניתוקים בסשן")
def link_interruptions(ctx, cfg):
    window = num(cfg.get("window_s")) or 600.0
    warn_n = num(cfg.get("warn_count"))
    fail_n = num(cfg.get("fail_count"))
    now = ctx.snap.now_mono
    recent = [i for i in ctx.session.interruptions()
              if i.open or (i.end_mono is not None and now - i.end_mono <= window)]
    src = ctx.src("יומן הסשן (SSH / MAVLink / וידאו)")
    if ctx.snap.get("mav.link") is None and ctx.snap.get("pi.ssh") is None:
        raise Unavailable(R(Status.UNKNOWN, "עדיין אין קישור פעיל שנוטר בסשן הזה.", source=src))
    ongoing = [i for i in recent if i.open]
    observed = f"{len(recent)} ב-{window / 60:.0f} הדקות האחרונות"
    if ongoing:
        observed += " · פעיל: " + ", ".join(i.link for i in ongoing)
    ev = {"recent": [i.to_dict() for i in recent]}
    expected = f"< {warn_n:g}" if warn_n is not None else "לא מוגדר"
    if fail_n is not None and len(recent) >= fail_n:
        return R(Status.FAIL, "ניתוקים חוזרים בקישור.", observed=observed, expected=expected, source=src, evidence=ev)
    if (warn_n is not None and len(recent) >= warn_n) or ongoing:
        return R(Status.WARN, "היו ניתוקים בקישור לאחרונה.", observed=observed, expected=expected, source=src, evidence=ev)
    return R(Status.PASS, "לא נרשמו ניתוקים בחלון הזמן.", observed=observed, expected=expected, source=src, evidence=ev)


@check("link.interfaces", "link", "ממשקי רשת ונתיבים ב-Pi")
def link_interfaces(ctx, cfg):
    data, age = ctx.require_section("network")
    src = ctx.src("Pi collector · ip -j addr / ip -j route")
    ifaces = data.get("interfaces")
    if not isinstance(ifaces, list):
        raise Unavailable(R(Status.UNKNOWN, "רשימת הממשקים לא תקינה.", source=src, age_s=age))
    parts = _iface_summary(ifaces)
    routes = data.get("default_routes") or []
    route_txt = ", ".join(f"{r.get('dev')} metric {r.get('metric', 0)}" for r in routes if isinstance(r, dict))
    observed = "; ".join(parts) + (f" · default: {route_txt}" if route_txt else " · אין default route")
    selected = ctx.drone.link_path
    want_kind = {"wifi": "wifi", "lte": "lte", "tailscale": "tailscale"}.get(selected)
    have = any(i.get("kind") == want_kind and i.get("ipv4") and i.get("operstate") in ("up", "unknown") for i in ifaces)
    ev = {"interfaces": ifaces, "default_routes": routes}
    if selected == "lte":
        # LTE path normally runs over Tailscale; require both.
        ts_up = any(i.get("kind") == "tailscale" and i.get("ipv4") for i in ifaces)
        have = have and ts_up
    if not have:
        return R(Status.FAIL, f"לא נמצא ממשק פעיל עם כתובת עבור הנתיב שנבחר ({PATH_LABEL.get(selected, selected)}).",
                 observed=ltr(observed), expected=f"ממשק {PATH_LABEL.get(selected, selected)} פעיל", source=src, age_s=age, evidence=ev)
    return R(Status.PASS, "ממשק הנתיב שנבחר פעיל ויש לו כתובת.", observed=ltr(observed),
             expected=f"ממשק {PATH_LABEL.get(selected, selected)} פעיל", source=src, age_s=age, evidence=ev)


def _probe_text(p):
    if not isinstance(p, dict) or p.get("configured") is False:
        return "לא מוגדר"
    if p.get("ok"):
        rtt = num(p.get("rtt_ms"))
        return "הצליח" + (f" {ltr(f'{rtt:.0f} ms')}" if rtt is not None else "")
    return "נכשל" + (f" ({ltr(p.get('error'))})" if p.get("error") else "")


def _probes(ctx):
    data, age = ctx.require_section("net_probes")
    return data, age, data.get("probes") or {}


@check("link.lte", "link", "מודם LTE (ZTE F30): ממשק, שער, אינטרנט")
def link_lte(ctx, cfg):
    req = cfg.get("required", "auto")
    required = (ctx.drone.link_path == "lte") if req == "auto" else bool(req)
    src = ctx.src("Pi collector · USB sysfs + ip addr + ping דרך ממשק ה-LTE")
    try:
        data, age = ctx.require_section("lte")
        pdata, _, probes = _probes(ctx)
    except Unavailable as u:
        if not required:
            return R(Status.NOT_APPLICABLE, "LTE אינו נדרש בפרופיל/נתיב הנוכחי.", source=src)
        raise u
    modems = data.get("usb_modems") or []
    ifaces = data.get("interfaces") or []
    up = [i for i in ifaces if i.get("operstate") in ("up", "unknown") and i.get("ipv4")]
    gw, inet = probes.get("zte_gateway") or {}, probes.get("internet_via_lte") or {}
    parts = [f"מודם USB: {'כן' if modems else 'לא'}"]
    if modems:
        m = modems[0]
        parts[0] += f" ({ltr(m.get('vid'))}:{ltr(m.get('pid'))})"
    parts.append("ממשק: " + (", ".join(f"{ltr(i['name'])} {ltr(i['ipv4'][0])}" for i in up) if up
                              else f"{ltr(data.get('configured_interface') or '?')} לא פעיל"))
    parts.append("שער ZTE: " + _probe_text(gw))
    parts.append("אינטרנט דרך LTE: " + _probe_text(inet))
    observed = " · ".join(parts)
    ev = {"lte": data, "zte_gateway": gw, "internet_via_lte": inet}
    lim = NOT_MAVLINK + " הצלחת ping לשער המודם מוכיחה רק קישור USB תקין למודם, לא חיבור סלולרי."
    if not required:
        return R(Status.NOT_APPLICABLE, "LTE אינו נדרש בפרופיל/נתיב הנוכחי. המידע מוצג לידיעה.",
                 observed=observed, source=src, age_s=age, evidence=ev, limitations=lim)
    if not modems and not up:
        return R(Status.FAIL, "מודם ה-LTE לא זוהה (לא ב-USB ולא כממשק פעיל).", observed=observed,
                 expected="מודם + ממשק פעיל + אינטרנט דרך LTE", source=src, age_s=age, evidence=ev, limitations=lim)
    if not up:
        return R(Status.FAIL, "המודם זוהה אך ממשק ה-LTE לא פעיל או בלי כתובת.", observed=observed,
                 expected="ממשק LTE פעיל עם כתובת", source=src, age_s=age, evidence=ev, limitations=lim)
    if inet.get("configured") is False:
        return R(Status.UNKNOWN, "לא הוגדר יעד ping, לכן לא נבדק אינטרנט דרך LTE.", observed=observed,
                 expected="אינטרנט דרך LTE", source=src, age_s=age, configured=False, evidence=ev, limitations=lim)
    if not inet.get("ok"):
        return R(Status.FAIL, "אין אינטרנט דרך ממשק ה-LTE.", observed=observed, expected="אינטרנט דרך LTE",
                 source=src, age_s=age, evidence=ev, limitations=lim)
    if gw.get("configured") is False:
        return R(Status.WARN, "אינטרנט דרך LTE תקין; כתובת שער ה-ZTE לא הוגדרה ולכן לא נבדקה.", observed=observed,
                 expected="שער ZTE + אינטרנט דרך LTE", source=src, age_s=age, evidence=ev, limitations=lim)
    return R(Status.PASS, "המודם, הממשק והאינטרנט דרך LTE תקינים.", observed=observed,
             expected="מודם + ממשק + אינטרנט דרך LTE", source=src, age_s=age, evidence=ev, limitations=lim)


@check("link.internet", "link", "נתיב לאינטרנט ו-ICMP מה-Pi")
def link_internet(ctx, cfg):
    data, age, probes = _probes(ctx)
    src = ctx.src("Pi collector · ip route get + ping")
    route = data.get("internet_route") or {}
    target = (data.get("config") or {}).get("ping_target")
    if not target:
        return R(Status.NOT_APPLICABLE, "לא הוגדר יעד ping.", source=src, age_s=age, configured=False,
                 limitations=NOT_MAVLINK)
    d = probes.get("internet_default_route") or {}
    parts = [f"נתיב: {ltr(route.get('dev') or '?')}" + (f" דרך {ltr(route.get('gateway'))}" if route.get("gateway") else ""),
             f"ping {ltr(target)}: {_probe_text(d)}",
             f"Wi-Fi: {_probe_text(probes.get('internet_via_wifi'))}",
             f"LTE: {_probe_text(probes.get('internet_via_lte'))}"]
    observed = " · ".join(parts)
    ev = {"route": route, "probes": probes}
    if d.get("ok"):
        return R(Status.PASS, "יש גישה ליעד דרך הנתיב הנוכחי.", observed=observed, expected="ping מצליח",
                 source=src, age_s=age, limitations=NOT_MAVLINK, evidence=ev)
    return R(Status.WARN, "אין גישה ליעד דרך הנתיב הנוכחי.", observed=observed, expected="ping מצליח",
             source=src, age_s=age, limitations=NOT_MAVLINK, evidence=ev)


@check("link.tailscale", "link", "Tailscale ב-Pi")
def link_tailscale(ctx, cfg):
    data, age, _ = _probes(ctx)
    src = ctx.src("Pi collector · systemctl is-active tailscaled + tailscale ip -4")
    ts = data.get("tailscale") or {}
    observed = f"שירות: {ltr(ts.get('service') or '?')} · IPv4: {ltr(ts.get('ipv4') or 'אין')}"
    lim = "מקשיב הווידאו (SRT) של ה-Pi נקשר לכתובת ה-Tailscale שלו, לכן בלי Tailscale אין וידאו."
    if ts.get("service") == "active" and ts.get("ipv4"):
        return R(Status.PASS, "Tailscale פעיל ויש לו כתובת IPv4.", observed=observed,
                 expected="active + IPv4", source=src, age_s=age, evidence=ts, limitations=lim)
    if ts.get("service") is None and ts.get("ipv4") is None:
        raise Unavailable(R(Status.UNKNOWN, "מצב Tailscale לא דווח.", source=src, age_s=age, evidence=ts))
    return R(Status.FAIL, "Tailscale לא פעיל או בלי כתובת IPv4.", observed=observed, expected="active + IPv4",
             source=src, age_s=age, evidence=ts, limitations=lim)


@check("link.ground", "link", "הגעה מה-Pi לתחנת הקרקע (ICMP)")
def link_ground(ctx, cfg):
    data, age, probes = _probes(ctx)
    src = ctx.src("Pi collector · ping לכתובת תחנת הקרקע")
    g = probes.get("ground_station") or {}
    lim = NOT_MAVLINK + " זו בדיקת ICMP בלבד מה-Pi לכתובת שהוגדרה."
    if g.get("configured") is False:
        return R(Status.NOT_APPLICABLE, "לא הוגדרה כתובת תחנת קרקע להגעה מה-Pi.", source=src, age_s=age,
                 configured=False, limitations=lim)
    observed = f"ping: {_probe_text(g)}"
    if g.get("ok"):
        return R(Status.PASS, "ה-Pi מגיע לכתובת תחנת הקרקע (ICMP).", observed=observed, expected="ping מצליח",
                 source=src, age_s=age, evidence=g, limitations=lim)
    return R(Status.WARN, "ה-Pi לא מגיע לכתובת תחנת הקרקע (ICMP). ייתכן שחומת אש חוסמת ICMP.", observed=observed,
             expected="ping מצליח", source=src, age_s=age, evidence=g, limitations=lim)


@check("link.mavlink_heartbeat", "link", "HEARTBEAT מבקר הטיסה")
def link_heartbeat(ctx, cfg):
    max_age = num(cfg.get("max_age_s"))
    link = ctx.mav_link() or {}
    hb, age = ctx.require_msg("HEARTBEAT", None)
    src = ctx.src(f"MAVLink HEARTBEAT · {ctx.mav_endpoint()}")
    sysid = link.get("target_sysid")
    observed = f"לפני {ltr(fmt_age(age))} · {ltr(f'sysid {sysid}')} · דרך {ltr(link.get('endpoint') or '?')}"
    ev = {"link": link}
    if max_age is None:
        return R(Status.UNKNOWN, "לא הוגדר גיל מקסימלי ל-HEARTBEAT.", observed=observed, source=src,
                 age_s=age, configured=False, evidence=ev)
    expected = f"≤ {max_age:g}s"
    if age > max_age:
        return R(Status.FAIL, "לא התקבל HEARTBEAT מבקר הטיסה לאחרונה. הקישור ל-MAVLink אינו חי.",
                 observed=observed, expected=expected, source=src, age_s=age, limitations=NOT_MAVLINK, evidence=ev)
    if link.get("bad_packets"):
        return R(Status.WARN, f"HEARTBEAT טרי, אך התקבלו {link['bad_packets']} חבילות פגומות.",
                 observed=observed, expected=expected, source=src, age_s=age, evidence=ev)
    return R(Status.PASS, "HEARTBEAT טרי מבקר הטיסה.", observed=observed, expected=expected, source=src,
             age_s=age, evidence=ev)


@check("link.telemetry", "link", "טלמטריה נדרשת")
def link_telemetry(ctx, cfg):
    req = cfg.get("max_age_s")
    src = ctx.src(f"MAVLink · {ctx.mav_endpoint()}")
    if not isinstance(req, dict) or not req:
        return R(Status.UNKNOWN, "לא הוגדרו הודעות נדרשות בפרופיל.", source=src, configured=False)
    parts, missing, stale = [], [], []
    oldest = 0.0
    for name, max_age in req.items():
        got = ctx.msg(name)
        if got is None:
            missing.append(name)
            parts.append(f"{ltr(name)}: חסר")
            continue
        age = got[1]
        oldest = max(oldest, age)
        parts.append(f"{ltr(name)}: {fmt_age(age)}")
        if num(max_age) is not None and age > max_age:
            stale.append(name)
    observed = " · ".join(parts)
    expected = " · ".join(f"{ltr(n)} ≤ {a}s" for n, a in req.items())
    if missing or stale:
        why = []
        if missing:
            why.append("חסרות: " + ", ".join(ltr(m) for m in missing))
        if stale:
            why.append("ישנות: " + ", ".join(ltr(s) for s in stale))
        return R(Status.UNKNOWN, "; ".join(why) + ". בלי נתונים טריים הבדיקות התלויות בהן אינן יכולות לעבור.",
                 observed=observed, expected=expected, source=src, age_s=oldest or None,
                 missing=tuple(missing + [f"{s} (stale)" for s in stale]))
    return R(Status.PASS, "כל הודעות הטלמטריה הנדרשות טריות.", observed=observed, expected=expected,
             source=src, age_s=oldest)


@check("link.gcs_peer", "link", "תחנת קרקע (Mission Planner) בקישור")
def link_gcs_peer(ctx, cfg):
    src = ctx.src(f"MAVLink HEARTBEAT (type=GCS) · {ctx.mav_endpoint()}")
    peers = []
    for o in ctx.snap.with_prefix("mav.peer."):
        hb = o.value if isinstance(o.value, dict) else {}
        if P.is_gcs_heartbeat(hb):
            peers.append((o.key[len("mav.peer."):], o.age(ctx.snap.now_mono)))
    fresh = [p for p in peers if p[1] <= 5]
    lim = ("האפליקציה רואה רק תחנות קרקע שהודעותיהן מנותבות דרך אותו mavlink-router. "
           "Mission Planner שמחובר בנתיב אחר לא ייראה כאן.")
    if fresh:
        observed = ", ".join(f"{ltr(k)} ({fmt_age(a)})" for k, a in fresh)
        return R(Status.PASS, "נראה HEARTBEAT של תחנת קרקע בקישור.", observed=observed,
                 expected="HEARTBEAT מתחנת קרקע ≤ 5s", source=src, limitations=lim)
    link = ctx.mav_link() or {}
    if not link.get("connected"):
        raise Unavailable(R(Status.UNKNOWN, "אין חיבור MAVLink.", source=src, limitations=lim))
    return R(Status.WARN, "לא נראה HEARTBEAT של תחנת קרקע. ודא ש-Mission Planner מחובר - הוא האחראי על השליטה בטיסה.",
             observed="לא נראה", expected="HEARTBEAT מתחנת קרקע ≤ 5s", source=src, limitations=lim)
