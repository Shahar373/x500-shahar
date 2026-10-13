"""Raspberry Pi checks - evaluated from raw collector observations."""

from __future__ import annotations

from ..status import Status
from .base import R, Unavailable, check, fmt_age, ltr, num

POWER_LIMITATION = (
    "בדיקת תוכנה אינה מאשרת את תקינות ספק הכוח ואינה מודדת קפיצות מתח מהירות. "
    "הדגלים משקפים את מה שהקושחה של ה-Pi דיווחה ברגע הדגימה."
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
    t = num(data.get("cpu_temp_c"))
    src = ctx.src("Pi collector · /sys/class/thermal")
    if t is None:
        raise Unavailable(R(Status.UNKNOWN, "הטמפרטורה לא דווחה.", source=src, age_s=age))
    warn, fail, wd, fd = _thresholds(ctx, cfg, "warn_c", "fail_c")
    observed = f"{t:.1f}°C"
    expected = f"< {wd}°C (אזהרה), < {fd}°C (כישלון)"
    if warn is None and fail is None:
        return R(Status.UNKNOWN, "לא הוגדרו ספי טמפרטורה בפרופיל.", observed=observed, source=src,
                 age_s=age, configured=False)
    if fail is not None and t >= fail:
        return R(Status.FAIL, "טמפרטורה גבוהה - צפויה האטה של המעבד.", observed=observed,
                 expected=expected, source=src, age_s=age)
    if warn is not None and t >= warn:
        return R(Status.WARN, "טמפרטורה קרובה לגבול ההאטה.", observed=observed, expected=expected,
                 source=src, age_s=age)
    return R(Status.PASS, "הטמפרטורה בטווח המוגדר.", observed=observed, expected=expected,
             source=src, age_s=age)


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


@check("pi.storage", "pi", "מקום פנוי באחסון")
def pi_storage(ctx, cfg):
    data, age = ctx.require_section("storage")
    src = ctx.src("Pi collector · statvfs")
    mounts = data.get("mounts")
    if not isinstance(mounts, list) or not mounts:
        raise Unavailable(R(Status.UNKNOWN, "לא דווחו נתוני אחסון.", source=src, age_s=age))
    warn, fail, wd, fd = _thresholds(ctx, cfg, "warn_free_mb", "fail_free_mb")
    worst = None
    parts = []
    for m in mounts:
        free = num(m.get("free_mb"))
        if free is None:
            continue
        parts.append(f"{ltr(m.get('path'))}: {free / 1024:.1f} GB")
        if worst is None or free < worst[0]:
            worst = (free, m.get("path"))
    if worst is None:
        raise Unavailable(R(Status.UNKNOWN, "נתוני האחסון אינם תקינים.", source=src, age_s=age))
    observed = ", ".join(parts)
    expected = f"> {wd} MB / > {fd} MB"
    if warn is None and fail is None:
        return R(Status.UNKNOWN, "לא הוגדרו ספים.", observed=observed, source=src, age_s=age, configured=False)
    if fail is not None and worst[0] <= fail:
        return R(Status.FAIL, f"מעט מאוד מקום פנוי ב-{ltr(worst[1])}.", observed=observed, expected=expected, source=src, age_s=age)
    if warn is not None and worst[0] <= warn:
        return R(Status.WARN, f"מקום פנוי נמוך ב-{ltr(worst[1])}.", observed=observed, expected=expected, source=src, age_s=age)
    return R(Status.PASS, "יש מספיק מקום פנוי.", observed=observed, expected=expected, source=src, age_s=age)


def _throttled(ctx):
    data, age = ctx.require_section("power")
    src = ctx.src("Pi collector · vcgencmd get_throttled")
    val = data.get("throttled")
    if isinstance(val, bool) or not isinstance(val, int):
        alarm = data.get("hwmon_undervoltage_alarm")
        return None, data, age, src, alarm
    return val, data, age, src, None


@check("pi.power_now", "pi", "מתח ה-Pi כעת (undervoltage/throttling)")
def pi_power_now(ctx, cfg):
    val, data, age, src, alarm = _throttled(ctx)
    ext5v = num(data.get("ext5v_v"))
    extra = f" · EXT5V {ext5v:.2f}V" if ext5v is not None else ""
    if val is None:
        if isinstance(alarm, int):
            src = ctx.src("Pi collector · hwmon rpi_volt in0_lcrit_alarm")
            if alarm:
                return R(Status.FAIL, "התראת מתח נמוך פעילה (hwmon).", observed=f"alarm={alarm}{extra}",
                         expected="alarm=0", source=src, age_s=age, limitations=POWER_LIMITATION)
            return R(Status.PASS, "אין התראת מתח נמוך פעילה (hwmon).", observed=f"alarm=0{extra}",
                     expected="alarm=0", source=src, age_s=age, limitations=POWER_LIMITATION)
        raise Unavailable(R(Status.UNKNOWN, "לא ניתן לקרוא את מצב המתח (vcgencmd לא זמין?).",
                            source=src, age_s=age, limitations=POWER_LIMITATION,
                            evidence={"error": data.get("vcgencmd_error")}))
    active = [txt for bit, txt in THROTTLE_NOW.items() if val & (1 << bit)]
    observed = f"{ltr(hex(val))}{extra}"
    ev = {"throttled": val, "ext5v_v": ext5v}
    if val & 1:
        return R(Status.FAIL, "ה-Pi מדווח על מתח נמוך כעת.", observed=observed, expected="ביטים 0-3 כבויים",
                 source=src, age_s=age, limitations=POWER_LIMITATION, evidence=ev)
    if active:
        return R(Status.WARN, "פעיל כעת: " + ", ".join(active) + ".", observed=observed, expected="ביטים 0-3 כבויים",
                 source=src, age_s=age, limitations=POWER_LIMITATION, evidence=ev)
    return R(Status.PASS, "אין מתח נמוך או האטה כרגע.", observed=observed, expected="ביטים 0-3 כבויים",
             source=src, age_s=age, limitations=POWER_LIMITATION, evidence=ev)


@check("pi.power_history", "pi", "אירועי מתח מאז האתחול")
def pi_power_history(ctx, cfg):
    val, data, age, src, alarm = _throttled(ctx)
    if val is None:
        raise Unavailable(R(Status.UNKNOWN, "היסטוריית המתח אינה זמינה (נדרש vcgencmd).",
                            source=src, age_s=age, limitations=POWER_LIMITATION))
    hist = [txt for bit, txt in THROTTLE_HIST.items() if val & (1 << bit)]
    observed = ltr(hex(val))
    if val & (1 << 16):
        return R(Status.WARN, "מאז האתחול היה מתח נמוך לפחות פעם אחת: " + ", ".join(hist) + ".",
                 observed=observed, expected="ביטים 16-19 כבויים", source=src, age_s=age,
                 limitations=POWER_LIMITATION)
    if hist:
        return R(Status.WARN, "מאז האתחול: " + ", ".join(hist) + ".", observed=observed,
                 expected="ביטים 16-19 כבויים", source=src, age_s=age, limitations=POWER_LIMITATION)
    return R(Status.PASS, "לא דווחו אירועי מתח נמוך או האטה מאז האתחול.", observed=observed,
             expected="ביטים 16-19 כבויים", source=src, age_s=age, limitations=POWER_LIMITATION)


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


@check("pi.watchdog", "pi", "Watchdog")
def pi_watchdog(ctx, cfg):
    r = _service_result(ctx, "watchdog", "ה-watchdog")
    try:
        wd, _ = ctx.require_section("watchdog")
        r.evidence["hardware"] = wd
        if isinstance(wd, dict) and wd.get("dev_watchdog_exists") is False:
            r.explanation += " (לא נמצא /dev/watchdog)"
    except Unavailable:
        pass
    return r


@check("pi.blackbox", "pi", "קופסה שחורה (רישום)")
def pi_blackbox(ctx, cfg):
    unit_r = _service_result(ctx, "blackbox", "הקופסה השחורה")
    globs = ctx.drone.blackbox_globs
    if not globs:
        if unit_r.status is Status.PASS:
            unit_r.explanation += " לא הוגדר נתיב לקבצי הלוג, לכן הטריות לא נבדקה."
            unit_r.status = Status.UNKNOWN
            unit_r.configured = False
        return unit_r
    data, age = ctx.require_section("blackbox")
    src = ctx.src("Pi collector · mtime של קבצי הלוג")
    newest = data.get("newest") if isinstance(data, dict) else None
    max_age = num(cfg.get("max_age_s"))
    if not isinstance(newest, dict):
        return R(Status.FAIL, "לא נמצאו קבצי לוג בנתיבים שהוגדרו.", observed="0 קבצים",
                 expected=f"קובץ שהשתנה ב-{max_age or '?'}s האחרונות", source=src, age_s=age,
                 evidence={"globs": globs, "service": unit_r.observed})
    fage = num(newest.get("mtime_age_s"))
    observed = f"{ltr(newest.get('path'))} · עודכן לפני {fmt_age(fage)}"
    if unit_r.observed:
        observed = unit_r.observed + " · " + observed
    ev = {"newest": newest, "file_count": data.get("file_count"), "service": unit_r.evidence}
    if max_age is None:
        return R(Status.UNKNOWN, "לא הוגדר גיל מקסימלי לקובץ הלוג.", observed=observed, source=src,
                 age_s=age, configured=False, evidence=ev)
    if fage is None:
        raise Unavailable(R(Status.UNKNOWN, "זמן העדכון של קובץ הלוג לא דווח.", source=src, age_s=age))
    if fage > max_age:
        return R(Status.FAIL, "קובץ הלוג החדש ביותר לא התעדכן לאחרונה - ייתכן שהרישום נעצר.",
                 observed=observed, expected=f"עדכון ב-{max_age:g}s האחרונות", source=src, age_s=age, evidence=ev)
    if unit_r.status in (Status.FAIL, Status.WARN):
        unit_r.observed = observed
        unit_r.evidence.update(ev)
        return unit_r
    status = Status.PASS if unit_r.status is Status.PASS or not unit_r.configured else unit_r.status
    expl = "קובץ הלוג מתעדכן."
    if not unit_r.configured:
        expl += " (שם השירות לא הוגדר - נבדקה רק טריות הקבצים.)"
    return R(status, expl, observed=observed, expected=f"עדכון ב-{max_age:g}s האחרונות",
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
    except Unavailable:
        pass
    return r


@check("pi.camera", "pi", "זיהוי מצלמה")
def pi_camera(ctx, cfg):
    data, age = ctx.require_section("camera")
    src = ctx.src(f"Pi collector · {data.get('tool') or 'rpicam-hello'} --list-cameras")
    bad = Status.FAIL if ctx.profile.video_required else Status.WARN
    cams = data.get("cameras")
    if data.get("tool") is None:
        return R(Status.UNKNOWN, "לא נמצא כלי rpicam/libcamera ב-Pi, לא ניתן לבדוק מצלמה.",
                 source=src, age_s=age, evidence=data)
    if not isinstance(cams, list):
        raise Unavailable(R(Status.UNKNOWN, "פלט רשימת המצלמות לא תקין.", source=src, age_s=age, evidence=data))
    if not cams:
        busy = data.get("note")
        return R(bad, "לא זוהתה מצלמה." + (f" ({ltr(busy)})" if busy else ""), observed="0 מצלמות",
                 expected="IMX219 מזוהה", source=src, age_s=age, evidence=data)
    names = ", ".join(str(c.get("sensor") or c.get("desc") or "?") for c in cams)
    if any("imx219" in str(c.get("sensor", "")).lower() for c in cams):
        return R(Status.PASS, "מצלמה IMX219 (Camera Module v2) זוהתה.", observed=ltr(names),
                 expected="IMX219 מזוהה", source=src, age_s=age, evidence=data)
    return R(bad, "זוהתה מצלמה אך לא IMX219.", observed=ltr(names), expected="IMX219 מזוהה",
             source=src, age_s=age, evidence=data)


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
