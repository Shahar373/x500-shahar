"""Raspberry Pi checks - evaluated from raw collector observations."""

from __future__ import annotations

import re

from ...pi_side.x500fs_collector import BLACKBOX_HEADER
from ..status import Status
from .base import R, Unavailable, check, fmt_age, ltr, num

POWER_LIMITATION = (
    "בדיקת תוכנה אינה מאשרת את הממיר (BEC/PM02), את החיווט או את המחברים, ואינה מודדת קפיצות מתח מהירות "
    "(למשל בעת האצת מנועים). הדגלים משקפים את מה שהקושחה של ה-Pi דיווחה ברגע הדגימה."
)

THROTTLE_NOW = {0: "מתח נמוך כעת", 1: "תדר מוגבל כעת", 2: "האטה (throttling) כעת", 3: "גבול טמפרטורה רך פעיל"}
THROTTLE_HIST = {16: "מתח נמוך", 17: "הגבלת תדר", 18: "האטה", 19: "גבול טמפרטורה רך"}


def _thresholds(ctx, cfg, warn_key, fail_key):
    warn, wdesc = ctx.resolve_threshold(cfg.get(warn_key))
    fail, fdesc = ctx.resolve_threshold(cfg.get(fail_key))
    return warn, fail, wdesc, fdesc


@check("pi.reachable", "pi", "חיבור ל-Pi (SSH)")
def pi_reachable(ctx, cfg):
    o = ctx.snap.get("pi.ssh")
    src = ctx.src("SSH")
    if o is None or not isinstance(o.value, dict):
        return R(Status.UNKNOWN, "לא בוצע ניסיון חיבור ל-Pi.", source=src, missing=("ssh",))
    s = o.value
    state = s.get("state")
    target = ltr(f"{s.get('user', '?')}@{s.get('host', '?')}:{s.get('port', 22)}")
    ev = {k: s.get(k) for k in ("host", "port", "user", "state", "fingerprint", "error")}
    if state == "connected":
        fp = s.get("fingerprint")
        return R(
            Status.PASS,
            "מחובר ב-SSH. מפתח השרת אומת מול רשימת המפתחות המוכרים.",
            observed=f"{target} · {ltr(fp or '?')}",
            expected="SSH מחובר עם מפתח שרת מאומת",
            source=src, age_s=o.age(ctx.snap.now_mono), evidence=ev,
        )
    if state == "hostkey_unknown":
        return R(
            Status.UNKNOWN,
            "מפתח השרת של ה-Pi אינו מוכר. החיבור נעצר עד שתאשר את טביעת האצבע (fingerprint).",
            observed=target, source=src, evidence=ev, missing=("verified host key",),
        )
    if state == "hostkey_mismatch":
        return R(
            Status.FAIL,
            "מפתח השרת של ה-Pi שונה מהמפתח השמור! ייתכן שזה מכשיר אחר או ניסיון התחזות. החיבור נחסם.",
            observed=target, source=src, evidence=ev,
        )
    if state in ("failed", "lost"):
        return R(
            Status.FAIL,
            f"אין חיבור ל-Pi: {ltr(s.get('error') or state)}",
            observed=target, expected="SSH מחובר", source=src, evidence=ev,
        )
    return R(Status.UNKNOWN, f"מצב חיבור: {ltr(state)}", observed=target, source=src, evidence=ev)


@check("pi.identity", "pi", "זהות ה-Pi ומערכת ההפעלה")
def pi_identity(ctx, cfg):
    data, age = ctx.require_section("identity")
    src = ctx.src("Pi collector · identity (/etc/os-release, device-tree)")
    hostname = data.get("hostname")
    model = data.get("model") or ""
    osr = data.get("os_release") or {}
    os_text = osr.get("PRETTY_NAME") or "?"
    observed = f"{ltr(hostname)} · {ltr(model or '?')} · {ltr(os_text)} · kernel {ltr(data.get('kernel', '?'))}"
    exp_host = ctx.drone.expected_hostname
    exp_model = cfg.get("expected_model_contains")
    expected_parts = []
    problems = []
    if exp_host:
        expected_parts.append(f"hostname={ltr(exp_host)}")
        if hostname != exp_host:
            problems.append(f"שם המארח {ltr(hostname)} שונה מהצפוי {ltr(exp_host)}")
    if exp_model:
        expected_parts.append(f"model ⊇ {ltr(exp_model)}")
        if exp_model not in model:
            problems.append(f"דגם הלוח {ltr(model or '?')} אינו {ltr(exp_model)}")
    ev = {"hostname": hostname, "model": model, "os_release": osr, "kernel": data.get("kernel"),
          "machine": data.get("machine"), "python": data.get("python")}
    if problems:
        return R(Status.FAIL, "; ".join(problems) + ". ייתכן שזה לא הרחפן הנכון.",
                 observed=observed, expected=", ".join(expected_parts), source=src, age_s=age, evidence=ev)
    if not exp_host:
        return R(Status.UNKNOWN, "לא הוגדר שם מארח צפוי בהגדרות, לכן הזהות לא אומתה.",
                 observed=observed, expected="hostname צפוי (לא מוגדר)", source=src, age_s=age,
                 configured=False, evidence=ev)
    return R(Status.PASS, "שם המארח ודגם הלוח תואמים להגדרות.", observed=observed,
             expected=", ".join(expected_parts), source=src, age_s=age, evidence=ev)


@check("pi.uptime", "pi", "זמן פעילות ואתחולים")
def pi_uptime(ctx, cfg):
    data, age = ctx.require_section("uptime")
    up = num(data.get("uptime_s"))
    if up is None:
        raise Unavailable(R(Status.UNKNOWN, "זמן הפעילות לא דווח.", source=ctx.src("Pi collector · /proc/uptime")))
    hist = ctx.snap.get("pi.boot_history")
    h = hist.value if hist and isinstance(hist.value, dict) else {}
    boots = h.get("boot_ids_this_session") or []
    prev = h.get("previous_boot_id")
    cur = h.get("current_boot_id")
    observed = f"uptime {fmt_age(up)}"
    src = ctx.src("Pi collector · /proc/uptime, boot_id")
    lim = "אין ראיה ישירה לסיבת אתחול. הזיהוי מבוסס על שינוי boot_id ועל זמן הפעילות."
    ev = {"uptime_s": up, "boot_ids_this_session": boots, "previous_boot_id": prev}
    if len(boots) > 1:
        return R(Status.WARN, "ה-Pi אותחל במהלך הסשן הנוכחי. בדוק אספקת מתח ולוגים.",
                 observed=observed, expected="ללא אתחול במהלך הסשן", source=src, age_s=age,
                 limitations=lim, evidence=ev)
    note = ""
    if prev and cur and prev != cur:
        note = " ה-Pi אותחל מאז הסשן הקודם (יכול להיות רגיל אחרי חיבור סוללה)."
    return R(Status.PASS, "לא זוהה אתחול במהלך הסשן." + note, observed=observed,
             expected="ללא אתחול במהלך הסשן", source=src, age_s=age, limitations=lim, evidence=ev)


@check("pi.temperature", "pi", "טמפרטורת מעבד ה-Pi")
def pi_temperature(ctx, cfg):
    data, age = ctx.require_section("thermal")
    src_kind = data.get("cpu_temp_source")
    src = ctx.src("Pi collector · " + ({"thermal_zone": "/sys/class/thermal", "vcgencmd": "vcgencmd measure_temp"}
                                      .get(src_kind, "thermal_zone / vcgencmd")))
    t = num(data.get("cpu_temp_c"))
    ev = {k: data.get(k) for k in ("zone_temp_c", "vcgencmd_temp_c", "vcgencmd_error", "cpu_temp_source")}
    if t is None:
        raise Unavailable(R(Status.UNKNOWN, "הטמפרטורה לא זמינה משום מקור (גם לא vcgencmd). לא מוצג 0.",
                            source=src, age_s=age, evidence=ev, missing=("cpu temperature",)))
    warn, fail, wd, fd = _thresholds(ctx, cfg, "warn_c", "fail_c")
    observed = f"{t:.1f}°C"
    other = num(data.get("vcgencmd_temp_c")) if src_kind == "thermal_zone" else None
    if other is not None:
        observed += f" (vcgencmd {other:.1f}°C)"
    expected = f"< {wd}°C (אזהרה), < {fd}°C (כישלון)"
    if warn is None and fail is None:
        return R(Status.UNKNOWN, "לא הוגדרו ספי טמפרטורה בפרופיל.", observed=observed, source=src,
                 age_s=age, configured=False, evidence=ev)
    if fail is not None and t >= fail:
        return R(Status.FAIL, "טמפרטורה קריטית.", observed=observed,
                 expected=expected, source=src, age_s=age, evidence=ev)
    if warn is not None and t >= warn:
        return R(Status.WARN, "טמפרטורה גבוהה.", observed=observed, expected=expected,
                 source=src, age_s=age, evidence=ev)
    return R(Status.PASS, "הטמפרטורה בטווח המוגדר.", observed=observed, expected=expected,
             source=src, age_s=age, evidence=ev)


@check("pi.cpu", "pi", "עומס מעבד ה-Pi")
def pi_cpu(ctx, cfg):
    data, age = ctx.require_section("cpu")
    u = num(data.get("usage_pct"))
    src = ctx.src("Pi collector · /proc/stat (דגימה קצרה)")
    if u is None:
        raise Unavailable(R(Status.UNKNOWN, "עומס המעבד לא דווח.", source=src, age_s=age))
    load = data.get("loadavg")
    warn, fail, wd, fd = _thresholds(ctx, cfg, "warn_pct", "fail_pct")
    observed = f"{u:.0f}%" + (f" · load {ltr(' '.join(str(x) for x in load))}" if isinstance(load, list) else "")
    expected = f"< {wd}% / < {fd}%"
    lim = "דגימה של חצי שנייה בלבד - מדד לרגע הבדיקה, לא ממוצע ארוך."
    if warn is None and fail is None:
        return R(Status.UNKNOWN, "לא הוגדרו ספים.", observed=observed, source=src, age_s=age, configured=False)
    if fail is not None and u >= fail:
        return R(Status.FAIL, "עומס מעבד גבוה מאוד.", observed=observed, expected=expected, source=src, age_s=age, limitations=lim)
    if warn is not None and u >= warn:
        return R(Status.WARN, "עומס מעבד גבוה.", observed=observed, expected=expected, source=src, age_s=age, limitations=lim)
    return R(Status.PASS, "עומס המעבד תקין.", observed=observed, expected=expected, source=src, age_s=age, limitations=lim)


@check("pi.memory", "pi", "זיכרון RAM פנוי")
def pi_memory(ctx, cfg):
    data, age = ctx.require_section("memory")
    pct = num(data.get("available_pct"))
    src = ctx.src("Pi collector · /proc/meminfo")
    if pct is None:
        raise Unavailable(R(Status.UNKNOWN, "נתוני זיכרון לא דווחו.", source=src, age_s=age))
    total = num(data.get("total_kb"))
    observed = f"{pct:.0f}% פנוי" + (f" מתוך {total / 1024:.0f} MB" if total else "")
    warn, fail, wd, fd = _thresholds(ctx, cfg, "warn_available_pct", "fail_available_pct")
    expected = f"> {wd}% / > {fd}%"
    if warn is None and fail is None:
        return R(Status.UNKNOWN, "לא הוגדרו ספים.", observed=observed, source=src, age_s=age, configured=False)
    if fail is not None and pct <= fail:
        return R(Status.FAIL, "כמעט אין זיכרון פנוי.", observed=observed, expected=expected, source=src, age_s=age)
    if warn is not None and pct <= warn:
        return R(Status.WARN, "זיכרון פנוי נמוך.", observed=observed, expected=expected, source=src, age_s=age)
    return R(Status.PASS, "יש מספיק זיכרון פנוי.", observed=observed, expected=expected, source=src, age_s=age)


# Small fixed-size partitions (the Pi's ~512 MB FAT boot partition) can never meet a free-space
# floor sized for logs, so only the percentage thresholds apply to them.
PERCENT_ONLY_PATHS = ("/boot/firmware", "/boot")


@check("pi.storage", "pi", "מקום פנוי באחסון")
def pi_storage(ctx, cfg):
    data, age = ctx.require_section("storage")
    src = ctx.src("Pi collector · statvfs")
    mounts = data.get("mounts")
    if not isinstance(mounts, list) or not mounts:
        raise Unavailable(R(Status.UNKNOWN, "לא דווחו נתוני אחסון.", source=src, age_s=age))
    warn, fail, wd, fd = _thresholds(ctx, cfg, "warn_free_mb", "fail_free_mb")
    warn_pct, fail_pct = num(cfg.get("warn_used_pct")), num(cfg.get("fail_used_pct"))
    pct_only = cfg.get("percent_only_paths")
    if not isinstance(pct_only, list):
        pct_only = list(PERCENT_ONLY_PATHS)
    status, reasons, parts, exempt = Status.PASS, [], [], []
    for m in mounts:
        free, total = num(m.get("free_mb")), num(m.get("total_mb"))
        if free is None or not total:
            continue
        used = 100.0 * (1 - free / total)
        size = f"{free / 1024:.1f} GB" if free >= 1024 else f"{free:.0f} MB"
        parts.append(f"{ltr(m.get('path'))}: {size} פנוי ({used:.0f}% בשימוש)")
        mb_warn, mb_fail = warn, fail
        if m.get("path") in pct_only and (warn_pct is not None or fail_pct is not None):
            mb_warn = mb_fail = None
            exempt.append(ltr(m.get("path")))
        level = Status.PASS
        if (mb_fail is not None and free <= mb_fail) or (fail_pct is not None and used >= fail_pct):
            level = Status.FAIL
        elif (mb_warn is not None and free <= mb_warn) or (warn_pct is not None and used >= warn_pct):
            level = Status.WARN
        if level is not Status.PASS:
            reasons.append(ltr(m.get("path")))
            if level is Status.FAIL or status is Status.PASS:
                status = level
    if not parts:
        raise Unavailable(R(Status.UNKNOWN, "נתוני האחסון אינם תקינים.", source=src, age_s=age))
    observed = ", ".join(parts)
    expected = f"פנוי > {wd} MB / > {fd} MB; בשימוש < {warn_pct:g}% / < {fail_pct:g}%" if warn_pct and fail_pct \
        else f"> {wd} MB / > {fd} MB"
    if exempt:
        expected += f" (ב-{', '.join(exempt)} - רק אחוז השימוש, כי המחיצה קטנה מטבעה)"
    if warn is None and fail is None and warn_pct is None and fail_pct is None:
        return R(Status.UNKNOWN, "לא הוגדרו ספים.", observed=observed, source=src, age_s=age, configured=False)
    expl = {Status.PASS: "יש מספיק מקום פנוי.", Status.WARN: "מקום פנוי נמוך ב-" + ", ".join(reasons) + ".",
            Status.FAIL: "מעט מאוד מקום פנוי ב-" + ", ".join(reasons) + "."}[status]
    return R(status, expl, observed=observed, expected=expected, source=src, age_s=age)


def _throttled(ctx):
    data, age = ctx.require_section("power")
    src = ctx.src("Pi collector · vcgencmd get_throttled")
    val = data.get("throttled")
    if isinstance(val, bool) or not isinstance(val, int):
        return None, data, age, src, data.get("hwmon_undervoltage_alarm")
    return val, data, age, src, None


def _power_ev(data, val):
    return {"throttled": val, "throttled_raw": data.get("throttled_raw"),
            "decoded": data.get("throttled_decoded"), "vcgencmd_error": data.get("vcgencmd_error"),
            "ext5v_v": data.get("ext5v_v"), "hwmon_undervoltage_alarm": data.get("hwmon_undervoltage_alarm")}


@check("pi.power_now", "pi", "מתח ה-Pi כעת (undervoltage/throttling)")
def pi_power_now(ctx, cfg):
    val, data, age, src, alarm = _throttled(ctx)
    ext5v = num(data.get("ext5v_v"))
    extra = f" · EXT5V {ext5v:.2f}V" if ext5v is not None else ""
    ev = _power_ev(data, val)
    if val is None:
        if isinstance(alarm, int) and not isinstance(alarm, bool):
            src = ctx.src("Pi collector · hwmon rpi_volt in0_lcrit_alarm")
            if alarm:
                return R(Status.FAIL, "התראת מתח נמוך פעילה (hwmon).", observed=f"alarm={alarm}{extra}",
                         expected="alarm=0", source=src, age_s=age, limitations=POWER_LIMITATION, evidence=ev)
            return R(Status.PASS, "אין התראת מתח נמוך פעילה לפי hwmon. דגלי vcgencmd לא היו זמינים: "
                     + ltr(data.get("vcgencmd_error") or "?"), observed=f"alarm=0{extra}",
                     expected="alarm=0", source=src, age_s=age, limitations=POWER_LIMITATION, evidence=ev)
        raise Unavailable(R(Status.UNKNOWN, "לא ניתן לקרוא את דגלי המתח (vcgencmd נכשל: "
                            + ltr(data.get("vcgencmd_error") or "?") + "). הערך לא מוחלף ב-0x0.",
                            source=src, age_s=age, limitations=POWER_LIMITATION, evidence=ev,
                            missing=("get_throttled",)))
    active = [txt for bit, txt in THROTTLE_NOW.items() if val & (1 << bit)]
    observed = f"{ltr(hex(val))}{extra}"
    if val & 1:
        return R(Status.FAIL, "ה-Pi מדווח על מתח נמוך כעת.", observed=observed, expected="ביטים 0-3 כבויים",
                 source=src, age_s=age, limitations=POWER_LIMITATION, evidence=ev)
    if active:
        return R(Status.WARN, "פעיל כעת: " + ", ".join(active) + ".", observed=observed, expected="ביטים 0-3 כבויים",
                 source=src, age_s=age, limitations=POWER_LIMITATION, evidence=ev)
    return R(Status.PASS, "אין מתח נמוך או האטה כרגע (דגלים נוכחיים). דגלים היסטוריים נבדקים בנפרד.",
             observed=observed, expected="ביטים 0-3 כבויים", source=src, age_s=age,
             limitations=POWER_LIMITATION, evidence=ev)


@check("pi.power_history", "pi", "אירועי מתח מאז האתחול")
def pi_power_history(ctx, cfg):
    val, data, age, src, alarm = _throttled(ctx)
    ev = _power_ev(data, val)
    if val is None:
        raise Unavailable(R(Status.UNKNOWN, "היסטוריית המתח אינה זמינה (vcgencmd נכשל: "
                            + ltr(data.get("vcgencmd_error") or "?") + ").",
                            source=src, age_s=age, limitations=POWER_LIMITATION, evidence=ev))
    hist = [txt for bit, txt in THROTTLE_HIST.items() if val & (1 << bit)]
    observed = ltr(hex(val))
    if val & (1 << 16):
        return R(Status.WARN, "מאז האתחול היה מתח נמוך לפחות פעם אחת: " + ", ".join(hist) + ".",
                 observed=observed, expected="ביטים 16-19 כבויים", source=src, age_s=age,
                 limitations=POWER_LIMITATION, evidence=ev)
    if hist:
        return R(Status.WARN, "מאז האתחול: " + ", ".join(hist) + ".", observed=observed,
                 expected="ביטים 16-19 כבויים", source=src, age_s=age, limitations=POWER_LIMITATION, evidence=ev)
    return R(Status.PASS, "לא דווחו אירועי מתח נמוך או האטה מאז האתחול.", observed=observed,
             expected="ביטים 16-19 כבויים", source=src, age_s=age, limitations=POWER_LIMITATION, evidence=ev)


def _unit_state(ctx, role):
    """Return (unit_name, unit_dict, age) or raise Unavailable/return unconfigured."""
    unit = (ctx.drone.services or {}).get(role) or ""
    if not unit:
        return None, None, None
    data, age = ctx.require_section("services")
    units = data.get("units") if isinstance(data, dict) else None
    info = units.get(unit) if isinstance(units, dict) else None
    return unit, info, age


def _discovered(ctx):
    got = ctx.pi()
    try:
        return got[0]["sections"]["services"]["data"].get("discovered", [])
    except Exception:
        return []


def _service_result(ctx, role, label, extra_ok=None):
    unit, info, age = _unit_state(ctx, role)
    src = ctx.src(f"Pi collector · systemctl show {unit or '?'}")
    if unit is None:
        disc = _discovered(ctx)
        hint = f" שירותים שזוהו ב-Pi: {ltr(', '.join(disc))}" if disc else ""
        return R(Status.UNKNOWN, f"לא הוגדר בהגדרות שם השירות של {label}.{hint}",
                 expected="שם שירות מוגדר", source=src, configured=False, evidence={"discovered": disc})
    if not isinstance(info, dict):
        raise Unavailable(R(Status.UNKNOWN, f"לא התקבל מצב עבור {ltr(unit)}.", source=src, age_s=age))
    if info.get("error") or not info.get("ActiveState"):
        raise Unavailable(R(Status.UNKNOWN, f"לא ניתן לקרוא את מצב {ltr(unit)}: {ltr(info.get('error') or 'פלט ריק')}",
                            source=src, age_s=age, evidence=dict(info)))
    load = info.get("LoadState")
    active = info.get("ActiveState")
    sub = info.get("SubState")
    restarts = info.get("NRestarts")
    observed = ltr(f"{unit}: {active}/{sub}" + (f" · restarts={restarts}" if restarts not in (None, "", "0") else ""))
    ev = dict(info)
    if load == "not-found":
        return R(Status.FAIL, f"השירות {ltr(unit)} לא קיים ב-Pi.", observed=observed,
                 expected="active/running", source=src, age_s=age, evidence=ev)
    if active != "active":
        return R(Status.FAIL, f"השירות {ltr(unit)} אינו פעיל.", observed=observed,
                 expected="active/running", source=src, age_s=age, evidence=ev)
    try:
        if restarts is not None and int(restarts) > 0:
            return R(Status.WARN, f"השירות פעיל אך הופעל מחדש {ltr(restarts)} פעמים מאז שעלה.",
                     observed=observed, expected="active, ללא הפעלות מחדש", source=src, age_s=age, evidence=ev)
    except (TypeError, ValueError):
        pass
    return R(Status.PASS, f"השירות {ltr(unit)} פעיל.", observed=observed, expected="active/running",
             source=src, age_s=age, evidence=ev)


def _usec_text_to_s(text):
    """systemd prints durations like '15s', '2min', '1min 30s', 'infinity', '0'."""
    if text is None:
        return None
    t = str(text).strip()
    if t in ("", "0"):
        return 0.0
    if t == "infinity":
        return float("inf")
    total = 0.0
    for val, unit in re.findall(r"([0-9.]+)\s*(us|ms|s|min|h)?", t):
        mult = {"us": 1e-6, "ms": 1e-3, "s": 1.0, "min": 60.0, "h": 3600.0, None: 1e-6, "": 1e-6}[unit or None]
        total += float(val) * mult
    return total


@check("pi.watchdog", "pi", "Watchdog: מוגדר מול פעיל")
def pi_watchdog(ctx, cfg):
    data, age = ctx.require_section("watchdog")
    src = ctx.src("Pi collector · systemctl show (manager) + /sys/class/watchdog")
    sd = data.get("systemd") or {}
    runtime = sd.get("RuntimeWatchdogUSec")
    runtime_s = _usec_text_to_s(runtime)
    expected_txt = ctx.drone.watchdog_expected_runtime
    expected_s = _usec_text_to_s(expected_txt) if expected_txt else None
    ping_age = num(data.get("last_ping_age_s"))
    devices = data.get("devices") or []
    dev_states = [d.get("state") for d in devices if d.get("state")]
    ev = {"systemd": sd, "configured_in_files": data.get("configured_in_files"), "devices": devices,
          "last_ping_age_s": ping_age, "expected_runtime": expected_txt}
    if not sd:
        raise Unavailable(R(Status.UNKNOWN, "לא ניתן לקרוא את הגדרות ה-watchdog של systemd: "
                            + ltr(data.get("systemd_error") or "?"), source=src, age_s=age, evidence=ev))
    observed = ltr(f"RuntimeWatchdogUSec={runtime} RebootWatchdogUSec={sd.get('RebootWatchdogUSec')}")
    observed += " · " + (f"ping אחרון לפני {ltr(f'{ping_age:.1f}s')}" if ping_age is not None
                         else "אין חותמת ping (WatchdogLastPingTimestamp)")
    if dev_states:
        observed += " · /sys state: " + ltr(",".join(dev_states))
    expected = f"מוגדר ({ltr(expected_txt or '> 0')}) ופעיל (ping טרי)"
    if not runtime_s:
        return R(Status.FAIL, "ה-watchdog של systemd אינו מוגדר (RuntimeWatchdogSec=0).", observed=observed,
                 expected=expected, source=src, age_s=age, evidence=ev)
    if expected_s is not None and abs(runtime_s - expected_s) > 0.5:
        return R(Status.WARN, f"ה-watchdog מוגדר לערך שונה מהצפוי ({ltr(runtime)} במקום {ltr(expected_txt)}).",
                 observed=observed, expected=expected, source=src, age_s=age, evidence=ev)
    fresh_limit = max(2.0 * runtime_s, 5.0) if runtime_s != float("inf") else None
    proof_ping = ping_age is not None and fresh_limit is not None and ping_age <= fresh_limit
    proof_sys = "active" in dev_states
    if proof_ping:
        return R(Status.PASS, "ה-watchdog מוגדר, ו-systemd מדווח על ping עדכני להתקן החומרה.",
                 observed=observed, expected=expected, source=src, age_s=age, evidence=ev)
    if proof_sys:
        return R(Status.PASS, "ה-watchdog מוגדר, וה-kernel מדווח שהתקן ה-watchdog פעיל (/sys state=active).",
                 observed=observed, expected=expected, source=src, age_s=age, evidence=ev,
                 limitations="אין חותמת ping מ-systemd; ההוכחה היא מצב ההתקן ב-/sys בלבד.")
    return R(Status.WARN, "ה-watchdog מוגדר, אבל אין הוכחת ריצה (אין ping עדכני ואין state=active). "
             "הגדרה בקובץ אינה הוכחה שהוא פעיל.", observed=observed, expected=expected, source=src,
             age_s=age, evidence=ev, missing=("watchdog runtime proof",))


@check("pi.blackbox", "pi", "קופסה שחורה: שירות ודגימות עדכניות")
def pi_blackbox(ctx, cfg):
    unit_r = _service_result(ctx, "blackbox", "הקופסה השחורה")
    data, age = ctx.require_section("blackbox")
    src = ctx.src("Pi collector · systemctl + CSV של x500-blackbox")
    max_age = num(cfg.get("max_age_s"))
    samples = data.get("samples") if isinstance(data, dict) else None
    newest = data.get("newest") if isinstance(data, dict) else None
    ev = {"service": unit_r.evidence, "file_count": data.get("file_count"), "oldest_file": data.get("oldest_file"),
          "newest": newest, "samples": samples, "configured_sleep_s": ctx.drone.blackbox_sleep_s}
    lim = ("הקצב בפועל הוא זמן הדגימה + השינה (sleep), לא 10 שניות מדויקות. שמירה (retention) לא מאומתת - "
           "הלוגר הקיים לא מוחק קבצים ולא נמצא timer לניקוי במקור.")
    if not unit_r.configured:
        unit_r.limitations = lim
        unit_r.evidence.update(ev)
        return unit_r
    if newest is None:
        return R(Status.FAIL, "לא נמצאו קבצי CSV של הקופסה השחורה.", observed=unit_r.observed,
                 expected="שירות פעיל + דגימה תקינה עדכנית", source=src, age_s=age, evidence=ev, limitations=lim)
    if not isinstance(samples, dict) or not samples.get("valid_rows"):
        bad = (samples or {}).get("invalid_examples")
        return R(Status.FAIL, "אין דגימות תקינות בקובץ האחרון" + (f" ({ltr(bad)})" if bad else "") + ".",
                 observed=unit_r.observed, expected="שירות פעיל + דגימה תקינה עדכנית", source=src, age_s=age,
                 evidence=ev, limitations=lim)
    last_age = num(samples.get("last_valid_age_s"))
    interval = (samples.get("interval_s") or {}).get("median")
    observed = (f"{unit_r.observed} · דגימה תקינה אחרונה לפני {ltr(fmt_age(last_age))}"
                + (f" · קצב בפועל ~{ltr(f'{interval:g}s')}" if interval else "")
                + f" · {data.get('file_count')} קבצים")
    expected = f"שירות פעיל + דגימה תקינה ב-{max_age:g}s האחרונות" if max_age else "לא מוגדר"
    if max_age is None:
        return R(Status.UNKNOWN, "לא הוגדר גיל מקסימלי לדגימה.", observed=observed, source=src, age_s=age,
                 configured=False, evidence=ev, limitations=lim)
    if last_age is None or last_age > max_age:
        return R(Status.FAIL, "הדגימה התקינה האחרונה ישנה - ייתכן שהרישום נעצר.", observed=observed,
                 expected=expected, source=src, age_s=age, evidence=ev, limitations=lim)
    if unit_r.status is not Status.PASS:
        unit_r.observed, unit_r.expected, unit_r.limitations = observed, expected, lim
        unit_r.evidence.update(ev)
        return unit_r
    if samples.get("header_ok") is False:
        return R(Status.WARN, "הדגימות עדכניות, אבל כותרת ה-CSV שונה מהצפוי." + _header_diff(samples.get("header_seen")),
                 observed=observed, expected=expected, source=src, age_s=age, evidence=ev, limitations=lim)
    return R(Status.PASS, "השירות פעיל ויש דגימה תקינה עדכנית.", observed=observed, expected=expected,
             source=src, age_s=age, evidence=ev, limitations=lim)


def _header_diff(seen) -> str:
    """Say how the first line of the CSV differs from the expected header (collector >= this version)."""
    if not isinstance(seen, list) or not seen:
        return ""
    seen = [str(c)[:40] for c in seen]
    if seen[0] != "timestamp":
        return " השורה הראשונה בקובץ אינה כותרת: " + ltr(",".join(seen)[:120])
    missing = [c for c in BLACKBOX_HEADER if c not in seen]
    extra = [c for c in seen if c not in BLACKBOX_HEADER]
    parts = []
    if missing:
        parts.append("חסרות עמודות: " + ltr(", ".join(missing)))
    if extra:
        parts.append("עמודות נוספות: " + ltr(", ".join(extra)))
    if not parts:
        parts.append("אותן עמודות בסדר אחר: " + ltr(",".join(seen)[:160]))
    return " " + "; ".join(parts) + "."


@check("pi.monitors", "pi", "שירותי ניטור (health / net)")
def pi_monitors(ctx, cfg):
    results = [_service_result(ctx, role, label) for role, label in
               (("health_monitor", "ניטור הבריאות"), ("net_monitor", "ניטור הרשת"))]
    configured = [r for r in results if r.configured]
    src = ctx.src("Pi collector · systemctl show")
    if not configured:
        return R(Status.UNKNOWN, "לא הוגדרו שמות שירותי הניטור.", source=src, configured=False)
    order = [Status.FAIL, Status.UNKNOWN, Status.WARN, Status.PASS]
    worst = min((r.status for r in configured), key=order.index)
    observed = " · ".join(r.observed or "—" for r in configured)
    expl = {Status.PASS: "שירותי הניטור פעילים.", Status.WARN: "שירות ניטור הופעל מחדש לאחרונה.",
            Status.FAIL: "שירות ניטור אינו פעיל.", Status.UNKNOWN: "מצב שירות ניטור אינו ידוע."}[worst]
    return R(worst, expl, observed=observed, expected="active/running", source=src,
             evidence={r.observed: r.evidence for r in configured})


@check("pi.fc_usb", "pi", "Pixhawk ב-USB (by-id)")
def pi_fc_usb(ctx, cfg):
    data, age = ctx.require_section("fc_usb")
    src = ctx.src("Pi collector · /dev/serial/by-id (לא נפתח)")
    matches = data.get("matches") or []
    names = [m.get("by_id", "") for m in matches]
    expected_name = ctx.drone.expected_fc_byid
    ev = {"glob": data.get("glob"), "matches": matches, "tty_acm": data.get("tty_acm")}
    try:
        mr, _ = ctx.require_section("mavlink_router")
        uart = [e.get("Device") for e in (mr.get("endpoints") or []) if str(e.get("type", "")).lower() == "uartendpoint"]
        ev["router_uart_devices"] = uart
    except Unavailable:
        uart = None
    observed = ltr(", ".join(n.rsplit("/", 1)[-1] for n in names)) if names else "לא נמצא"
    if not matches:
        return R(Status.FAIL, "לא נמצא התקן Pixhawk לפי התבנית ב-/dev/serial/by-id.", observed=observed,
                 expected=ltr(data.get("glob") or "?"), source=src, age_s=age, evidence=ev)
    if expected_name:
        if not any(n.endswith("/" + expected_name) or n == expected_name for n in names):
            return R(Status.FAIL, "נמצא Pixhawk, אבל לא ההתקן המוגדר (מספר סידורי שונה?).", observed=observed,
                     expected=ltr(expected_name), source=src, age_s=age, evidence=ev)
    if uart and not any(any(u == n or u.endswith(n.rsplit("/", 1)[-1]) for n in names) for u in uart if u):
        return R(Status.WARN, "ה-UartEndpoint ב-mavlink-router מצביע על התקן אחר מזה שנמצא.", observed=observed,
                 expected="התאמה ל-UartEndpoint", source=src, age_s=age, evidence=ev)
    expl = "התקן ה-Pixhawk קיים ב-USB." + ("" if expected_name else
                                            " (לא הוגדר by-id צפוי - הזהות לא אומתה מול מספר סידורי.)")
    return R(Status.PASS, expl, observed=observed, expected=ltr(expected_name or data.get("glob") or "?"),
             source=src, age_s=age, evidence=ev)


@check("pi.mavlink_router", "pi", "שירות mavlink-router")
def pi_mavlink_router(ctx, cfg):
    r = _service_result(ctx, "mavlink_router", "mavlink-router")
    try:
        mr, _ = ctx.require_section("mavlink_router")
        if isinstance(mr, dict):
            r.evidence["config"] = {"files": mr.get("config_files"), "endpoints": mr.get("endpoints"),
                                    "general": mr.get("general"), "listening": mr.get("listening")}
            eps = mr.get("endpoints") or []
            if eps:
                r.observed = (r.observed or "") + f" · {len(eps)} endpoints"
            tcp = (mr.get("general") or {}).get("TcpServerPort")
            if tcp:
                r.observed = (r.observed or "") + f" · {ltr(f'TCP {tcp}')}"
            placeholders = [f"{e.get('type')} {e.get('name')}" for e in eps
                            if any("NOT_CONFIGURED" in str(v) for v in e.values())]
            if placeholders and r.status is Status.PASS:
                r.status = Status.WARN
                r.explanation += (" בקובץ ההגדרות יש ערכי סימון (NOT_CONFIGURED) ב-"
                                  + ltr(", ".join(placeholders)) + " - כנראה הועתק מה-snapshot בלי התאמה.")
    except Unavailable:
        pass
    return r


@check("pi.camera", "pi", "זיהוי מצלמה")
def pi_camera(ctx, cfg):
    data, age = ctx.require_section("camera")
    bad = Status.FAIL if ctx.profile.video_required else Status.WARN
    mode, chosen = _camera_mode(ctx)
    usb = data.get("usb_cameras")
    if mode in ("auto", "usb") and isinstance(usb, list):
        src = ctx.src("Pi collector · /sys/class/video4linux (USB) · CAMERA=" + mode)
        expected = "מצלמת USB מחוברת" if mode == "usb" else "מצלמת USB או IMX219 מזוהה"
        if usb:
            names = ", ".join(str(c.get("name") or c.get("device") or "?") for c in usb)
            note = ""
            if chosen and chosen.get("camera") == "csi":
                note = " שירות הווידאו הנוכחי הופעל לפני שהמצלמה חוברה ומשתמש במצלמת ה-Pi - הפעל מחדש את הווידאו."
            return R(Status.PASS, "מצלמת USB זוהתה; שירות הווידאו ישתמש בה." + note, observed=ltr(names),
                     expected=expected, source=src, age_s=age, evidence=data)
        if mode == "usb":
            return R(bad, "הוגדר CAMERA=usb אבל לא נמצאה מצלמת USB (UVC). בדוק את החיבור ואת lsusb.",
                     observed="0 מצלמות USB", expected=expected, source=src, age_s=age, evidence=data)
    elif mode == "usb":
        raise Unavailable(R(Status.UNKNOWN, "הוגדר CAMERA=usb, אבל ה-collector לא דיווח על מצלמות USB.",
                            source=ctx.src("Pi collector"), age_s=age, evidence=data))
    src = ctx.src(f"Pi collector · {data.get('tool') or 'rpicam-hello'} --list-cameras")
    cams = data.get("cameras")
    if data.get("tool") is None:
        return R(Status.UNKNOWN, "לא נמצא כלי rpicam/libcamera ב-Pi, לא ניתן לבדוק מצלמה.",
                 source=src, age_s=age, evidence=data)
    if not isinstance(cams, list):
        raise Unavailable(R(Status.UNKNOWN, "פלט רשימת המצלמות לא תקין.", source=src, age_s=age, evidence=data))
    if not cams:
        busy = data.get("note")
        video_active = None
        try:
            vdata, _ = ctx.require_section("video")
            prof = vdata.get("active_profile")
            unit = (vdata.get("units") or {}).get(prof) or {}
            # Only a running encoder can hold the camera; a unit in auto-restart is failing, not holding it.
            if prof and unit.get("ActiveState") == "active" and unit.get("SubState") == "running":
                video_active = prof
        except Unavailable:
            pass
        k = data.get("kernel_sensor")
        if isinstance(k, dict) and not k.get("devices"):
            video_active = None  # the kernel sees no sensor, so nothing can be holding it
        if video_active:
            return R(Status.UNKNOWN, f"לא זוהתה מצלמה בזמן ששירות הווידאו ({ltr(video_active)}) פעיל - "
                     "ייתכן שהמצלמה תפוסה. אין ראיה לכאן או לכאן.", observed="0 מצלמות", expected="IMX219 מזוהה",
                     source=src, age_s=age, evidence=data)
        return R(bad, "לא זוהתה מצלמה." + (f" ({ltr(busy)})" if busy else "") + _camera_hint(data),
                 observed="0 מצלמות", expected="IMX219 מזוהה", source=src, age_s=age, evidence=data)
    names = ", ".join(str(c.get("sensor") or c.get("desc") or "?") for c in cams)
    if any("imx219" in str(c.get("sensor", "")).lower() for c in cams):
        return R(Status.PASS, "מצלמה IMX219 (Camera Module v2) זוהתה.", observed=ltr(names),
                 expected="IMX219 מזוהה", source=src, age_s=age, evidence=data)
    return R(bad, "זוהתה מצלמה אך לא IMX219.", observed=ltr(names), expected="IMX219 מזוהה",
             source=src, age_s=age, evidence=data)


def _camera_hint(data: dict) -> str:
    """Narrow down why rpicam lists no camera, from the read-only evidence the collector adds."""
    if data.get("permission_denied") or data.get("in_video_group") is False or data.get("media_access") is False:
        return (" למשתמש ב-Pi אין הרשאה למכשירי המצלמה (לא בקבוצה video או אין גישה ל-/dev/media*), ולכן "
                "rpicam לא רואה מצלמה גם אם היא מחוברת. שירות הווידאו רץ כ-root ועשוי לעבוד בכל זאת.")
    k = data.get("kernel_sensor")
    if not isinstance(k, dict):
        return ""
    if k.get("devices"):
        return (" הקרנל זיהה את החיישן (" + ltr(", ".join(k["devices"])) + "), אבל rpicam לא - "
                "ייתכן שהמצלמה תפוסה על ידי תהליך אחר.")
    if k.get("driver_loaded"):
        return " מנהל ההתקן imx219 טעון אבל החיישן לא נמצא - בדוק את הכבל ואת החיבור."
    return (" הקרנל לא טען את מנהל ההתקן imx219, כלומר המצלמה לא זוהתה באתחול. בדוק את הכבל ואת כיוון החיבור "
            "(ב-Pi 5 נדרש כבל עם מחבר 22 פינים בצד ה-Pi), חבר רק כשה-Pi כבוי, ובדוק camera_auto_detect ב-config.txt.")


def _camera_mode(ctx) -> tuple[str, dict | None]:
    """CAMERA= from /etc/x500/video.conf (auto when unknown) and what the running service picked."""
    try:
        vdata, _ = ctx.require_section("video")
    except Unavailable:
        return "auto", None
    mode = str(((vdata.get("config") or {}).get("CAMERA")) or "auto").lower()
    source = vdata.get("source") if isinstance(vdata.get("source"), dict) else None
    return (mode if mode in ("auto", "csi", "usb") else "auto"), source


@check("pi.tools", "pi", "סקריפטים קיימים ב-Pi")
def pi_tools(ctx, cfg):
    data, age = ctx.require_section("tools")
    src = ctx.src("Pi collector · סקריפטים מוגדרים")
    if not isinstance(data, dict) or not data:
        return R(Status.NOT_APPLICABLE, "לא הוגדרו סקריפטים קיימים להרצה (x500-status / x500-preflight).",
                 source=src, age_s=age)
    worst = Status.PASS
    parts = []
    for name, res in data.items():
        if not isinstance(res, dict):
            worst = Status.UNKNOWN
            continue
        code = res.get("exit_code")
        if res.get("error"):
            parts.append(f"{ltr(name)}: {ltr(res['error'])}")
            if worst is Status.PASS:
                worst = Status.UNKNOWN
        elif code == 0:
            parts.append(f"{ltr(name)}: exit 0")
        else:
            parts.append(f"{ltr(name)}: exit {ltr(code)}")
            worst = Status.WARN
    expl = {
        Status.PASS: "כל הסקריפטים המוגדרים הסתיימו בהצלחה (exit 0). הפלט המלא נשמר בדוח.",
        Status.WARN: "לפחות סקריפט אחד החזיר קוד שגיאה. ראה את הפלט בפרטים.",
        Status.UNKNOWN: "לא ניתן היה להריץ לפחות סקריפט אחד.",
    }[worst]
    return R(worst, expl, observed="; ".join(parts), expected="exit 0", source=src, age_s=age, evidence=data)
