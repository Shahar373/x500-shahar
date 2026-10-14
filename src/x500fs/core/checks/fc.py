"""Pixhawk / ArduPilot and navigation checks."""

from __future__ import annotations

from ..mav import constants as C
from ..mav import parse as P
from ..status import Status
from .base import R, Unavailable, check, fmt_age, ltr, num

NO_PREARM_TEXT = ("היעדר הודעת PreArm אחרונה אינו הוכחה שהבדיקות עברו (ArduPilot שולח אותן כל 30 שניות בערך). "
                  "התוצאה מבוססת על ביט PREARM_CHECK ב-SYS_STATUS (ArduPilot 4.1 ומעלה). כשהרחפן חמוש הביט תמיד 'תקין'.")


def _hb(ctx, cfg, default_age=3.0):
    max_age = num(cfg.get("max_age_s")) or default_age
    return ctx.require_msg("HEARTBEAT", max_age)


def _sys_status(ctx, cfg):
    return ctx.require_msg("SYS_STATUS", num(cfg.get("max_age_s")) or 5.0)


@check("fc.identity", "fc", "זיהוי בקר הטיסה וקושחה")
def fc_identity(ctx, cfg):
    hb, age = ctx.require_msg("HEARTBEAT", 10.0)
    src = ctx.src(f"MAVLink HEARTBEAT + AUTOPILOT_VERSION · {ctx.mav_endpoint()}")
    ap = C.MAV_AUTOPILOT_NAMES.get(hb.get("autopilot"), f"autopilot {hb.get('autopilot')}")
    vt = C.MAV_TYPE_NAMES.get(hb.get("type"), f"type {hb.get('type')}")
    link = ctx.mav_link() or {}
    av = ctx.msg("AUTOPILOT_VERSION")
    fw = None
    ev = {"heartbeat": {k: hb.get(k) for k in ("type", "autopilot", "mavlink_version", "system_status")},
          "sysid": link.get("target_sysid")}
    if av:
        info = P.autopilot_version(av[0])
        fw = info.get("flight_sw")
        ev["autopilot_version"] = info
    tech = f"{ap} · {vt} · sysid {link.get('target_sysid')}"
    if fw:
        tech += f" · ArduPilot {fw['text']}"
        if ev["autopilot_version"].get("git_hash"):
            tech += f" ({ev['autopilot_version']['git_hash']})"
        observed = ltr(tech)
    else:
        observed = ltr(tech) + " · גרסת קושחה לא התקבלה"
    exp_ap = cfg.get("expected_autopilot")
    exp_types = cfg.get("expected_vehicle_types")
    expected = ltr(f"{exp_ap} · {', '.join(exp_types or [])}")
    if exp_ap and ap != exp_ap:
        return R(Status.FAIL, "סוג הטייס האוטומטי אינו כצפוי.", observed=observed, expected=expected,
                 source=src, age_s=age, evidence=ev)
    if exp_types and vt not in exp_types:
        return R(Status.FAIL, "סוג כלי הטיס אינו כצפוי (ייתכן FRAME_CLASS שגוי או מכשיר אחר).",
                 observed=observed, expected=expected, source=src, age_s=age, evidence=ev)
    min_fw = cfg.get("min_firmware")
    if min_fw:
        if not fw:
            return R(Status.UNKNOWN, "נדרשת גרסת קושחה מינימלית אך הגרסה לא התקבלה.", observed=observed,
                     expected=f"≥ {ltr(min_fw)}", source=src, age_s=age, evidence=ev, missing=("AUTOPILOT_VERSION",))
        try:
            want = tuple(int(x) for x in str(min_fw).split("."))
        except ValueError:
            return R(Status.UNKNOWN, "ערך min_firmware בפרופיל אינו תקין.", observed=observed, source=src,
                     age_s=age, configured=False, evidence=ev)
        have = (fw["major"], fw["minor"], fw["patch"])
        if have < want:
            return R(Status.FAIL, "גרסת הקושחה ישנה מהמינימום שהוגדר.", observed=observed,
                     expected=f"≥ {ltr(min_fw)}", source=src, age_s=age, evidence=ev)
    others = sorted({o.key[len("mav.peer."):].split(".")[0] for o in ctx.snap.with_prefix("mav.peer.")
                     if isinstance(o.value, dict) and P.is_autopilot_heartbeat(o.value)
                     and o.age(ctx.snap.now_mono) <= 10})
    ev["other_autopilots_sysid"] = others
    expl = "בקר הטיסה מזוהה כ-ArduPilot מסוג הכלי הצפוי."
    if not fw:
        expl += " גרסת הקושחה לא התקבלה (AUTOPILOT_VERSION)."
    if others:
        observed += " · עוד בקרי טיסה בקישור: sysid " + ltr(", ".join(others))
        if not ctx.drone.mav_target_sysid:
            return R(Status.WARN, "בקישור נראים כמה בקרי טיסה, והאפליקציה בחרה את הראשון שנראה. "
                     "קבע 'sysid של הרחפן' בהגדרות כדי לבחור במפורש. נתונים משאר בקרי הטיסה מסוננים.",
                     observed=observed, expected="בקר טיסה אחד, או sysid שנקבע בהגדרות", source=src,
                     age_s=age, evidence=ev)
        expl += " נראים גם בקרי טיסה אחרים; הנתונים שלהם מסוננים (sysid נקבע בהגדרות)."
    return R(Status.PASS, expl, observed=observed, expected=expected, source=src, age_s=age, evidence=ev)


@check("fc.armed", "fc", "מצב חימוש")
def fc_armed(ctx, cfg):
    hb, age = _hb(ctx, cfg)
    src = ctx.src("MAVLink HEARTBEAT.base_mode")
    armed = P.is_armed(hb)
    if armed is None:
        raise Unavailable(R(Status.UNKNOWN, "שדה base_mode לא תקין.", source=src, age_s=age))
    if armed:
        return R(Status.FAIL, "הרחפן חמוש (ARMED). בדיקות לפני טיסה מיועדות למצב לא חמוש.",
                 observed="ARMED", expected="DISARMED", source=src, age_s=age)
    return R(Status.PASS, "הרחפן אינו חמוש.", observed="DISARMED", expected="DISARMED", source=src, age_s=age)


@check("fc.mode", "fc", "מצב טיסה")
def fc_mode(ctx, cfg):
    hb, age = _hb(ctx, cfg)
    src = ctx.src("MAVLink HEARTBEAT.custom_mode")
    mode = P.copter_mode_name(hb)
    if mode is None:
        raise Unavailable(R(Status.UNKNOWN, "מצב הטיסה לא פוענח.", source=src, age_s=age))
    expected = cfg.get("expected_modes")
    if not expected:
        return R(Status.NOT_APPLICABLE, "לא הוגדרו מצבי טיסה צפויים בפרופיל. הערך מוצג לידיעה.",
                 observed=ltr(mode), source=src, age_s=age)
    if mode in expected:
        return R(Status.PASS, "מצב הטיסה ברשימה המותרת.", observed=ltr(mode),
                 expected=ltr(", ".join(expected)), source=src, age_s=age)
    return R(Status.WARN, "מצב הטיסה אינו ברשימה המותרת לפני המראה.", observed=ltr(mode),
             expected=ltr(", ".join(expected)), source=src, age_s=age)


def _recent_prearm(ctx, window=60.0):
    o = ctx.snap.get("mav.prearm_texts")
    if o is None or not isinstance(o.value, list):
        return []
    now = ctx.snap.now_mono
    out = []
    for item in o.value:
        if isinstance(item, dict) and now - float(item.get("t_mono", 0)) <= window:
            out.append(item.get("text", ""))
    return list(dict.fromkeys(out))


def _arming_param(ctx):
    """Return (kind, value, age) for ARMING_SKIPCHK (newer) or ARMING_CHECK."""
    skip = ctx.param("ARMING_SKIPCHK")
    if skip is not None:
        return "ARMING_SKIPCHK", int(skip[0]), skip[1]
    chk = ctx.param("ARMING_CHECK")
    if chk is not None:
        return "ARMING_CHECK", int(chk[0]), chk[1]
    return None, None, None


def _skipped_checks(kind, value):
    """Names of arming checks that are disabled, or ['ALL'] if all are."""
    if kind == "ARMING_CHECK":
        d = P.arming_check_decode(value)
        if d["disabled"]:
            return ["ALL"]
        if d["all"]:
            return []
        enabled = set(d["enabled_bits"])
        # Only bits that exist for Copter in ARMING_CHECK firmware (<= 4.6):
        # bit 9 (airspeed) is Plane-only and bit 20 (OSD) postdates ARMING_CHECK.
        return [n for b, n in C.ARMING_CHECK_BITS.items()
                if b and b not in C.ARMING_CHECK_NOT_COPTER and n not in enabled]
    # ARMING_SKIPCHK: bit set = check skipped; -1 / all bits = everything skipped
    if value == -1 or value & 1:
        return ["ALL"]
    return [n for b, n in C.ARMING_CHECK_BITS.items() if b and value & (1 << b)]


@check("fc.arming_checks", "fc", "בדיקות החימוש של ArduPilot מופעלות")
def fc_arming_checks(ctx, cfg):
    kind, value, age = _arming_param(ctx)
    src = ctx.src("MAVLink PARAM_VALUE (קריאה בלבד)")
    if kind is None:
        raise Unavailable(R(Status.UNKNOWN, "הפרמטרים ARMING_CHECK / ARMING_SKIPCHK לא נקראו מבקר הטיסה.",
                            source=src, missing=("ARMING_CHECK",)))
    skipped = _skipped_checks(kind, value)
    allowed = set(cfg.get("allowed_skipped") or [])
    observed = f"{ltr(kind)}={ltr(value)}" + (f" · מושבתות: {ltr(', '.join(skipped))}" if skipped else "")
    expected = "כל הבדיקות מופעלות" + (f" (מותר לדלג: {ltr(', '.join(sorted(allowed)))})" if allowed else "")
    ev = {"param": kind, "value": value, "skipped": skipped, "allowed_skipped": sorted(allowed)}
    if "ALL" in skipped:
        return R(Status.FAIL, "בדיקות החימוש של ArduPilot מושבתות לגמרי. אין להסתמך על מצב Pre-arm.",
                 observed=observed, expected=expected, source=src, age_s=age, evidence=ev)
    not_allowed = [s for s in skipped if s not in allowed]
    if not_allowed:
        return R(Status.FAIL, "בדיקות חימוש מושבתות שלא הותרו במפורש בפרופיל: " + ltr(", ".join(not_allowed)),
                 observed=observed, expected=expected, source=src, age_s=age, evidence=ev)
    if skipped:
        return R(Status.WARN, "חלק מבדיקות החימוש מושבתות (הותר במפורש בפרופיל).", observed=observed,
                 expected=expected, source=src, age_s=age, evidence=ev)
    return R(Status.PASS, "כל בדיקות החימוש של ArduPilot מופעלות.", observed=observed, expected=expected,
             source=src, age_s=age, evidence=ev)


DIAG_STATE_HE = {
    "sent": "הבקשה נשלחה, ממתין לתשובה מבקר הטיסה",
    "accepted": "בקר הטיסה אישר את הבקשה, ממתין ל-SYS_STATUS טרי",
    "no_ack": "בקר הטיסה לא ענה לבקשה (אין COMMAND_ACK מה-sysid של הרחפן)",
    "no_fresh_sys_status": "בקר הטיסה אישר, אבל לא הגיע SYS_STATUS טרי אחרי האישור",
}
DIAG_RESULT_HE = {
    "TEMPORARILY_REJECTED": "בקר הטיסה דחה זמנית (למשל כי הוא חמוש)",
    "UNSUPPORTED": "הקושחה לא תומכת בפקודה",
    "DENIED": "בקר הטיסה סירב",
    "FAILED": "בקר הטיסה דיווח כישלון בביצוע",
}
DIAG_LIMIT = ("האבחון הוא MAV_CMD_RUN_PREARM_CHECKS (קריאה בלבד): ArduPilot מריץ את בדיקות ה-Pre-arm ושולח הודעות PreArm "
              "על כל כשל. אישור (ACCEPTED) פירושו רק שהבדיקות הורצו - לא שעברו. ההחלטה מבוססת על ביט PREARM_CHECK "
              "ב-SYS_STATUS שהגיע אחרי האישור ועל הודעות PreArm. האפליקציה לעולם לא מנסה לחמש כדי לבדוק מוכנות.")


def _prearm_diag(ctx, cfg):
    """Return (diag dict | None, age_s | None) for the explicit pre-arm diagnostic."""
    o = ctx.snap.get("mav.prearm_diag")
    if o is None or not isinstance(o.value, dict):
        return None, None
    age = ctx.snap.now_mono - o.t_mono
    if age > float(cfg.get("diagnostic_max_age_s", 300)):
        return None, age
    return o.value, age


def _diag_text(diag):
    st = diag.get("state")
    if st == "not_sent":
        return "האבחון לא נשלח: " + {
            "vehicle armed": "הרחפן חמוש",
            "passive mode (requests disabled)": "מצב פסיבי (בקשות כבויות בהגדרות)",
        }.get(diag.get("reason", ""), "אין HEARTBEAT טרי מהרחפן, לכן לא אומת שהוא לא חמוש")
    if st == "rejected":
        name = diag.get("result_name", "?")
        return f"ArduPilot ענה {ltr(name)}: " + DIAG_RESULT_HE.get(name, "הבקשה לא בוצעה")
    if st == "complete":
        return "האבחון הושלם"
    return DIAG_STATE_HE.get(st, f"מצב לא מוכר: {ltr(st)}")


@check("fc.prearm", "fc", "מצב Pre-arm של ArduPilot")
def fc_prearm(ctx, cfg):
    ss, age = _sys_status(ctx, cfg)
    src = ctx.src("MAVLink SYS_STATUS (PREARM_CHECK) + STATUSTEXT + MAV_CMD_RUN_PREARM_CHECKS")
    sens = P.sensors(ss)
    if sens is None:
        raise Unavailable(R(Status.UNKNOWN, "שדות החיישנים ב-SYS_STATUS אינם תקינים.", source=src, age_s=age))
    pa = sens["prearm_check"]
    texts = _recent_prearm(ctx)
    require = bool(cfg.get("require_diagnostic", False))
    diag, diag_age = _prearm_diag(ctx, cfg)
    diag_texts = list((diag or {}).get("texts") or []) if (diag or {}).get("state") in ("accepted", "complete") else []
    all_texts = list(dict.fromkeys(diag_texts + texts))
    ev = {"prearm_bit": pa, "recent_prearm_messages": texts, "require_diagnostic": require,
          "diagnostic": diag, "diagnostic_age_s": None if diag_age is None else round(diag_age, 1)}
    limits = NO_PREARM_TEXT + " " + DIAG_LIMIT
    kind, value, _ = _arming_param(ctx)
    if kind is not None and "ALL" in _skipped_checks(kind, value):
        return R(Status.UNKNOWN, "בדיקות החימוש מושבתות, לכן ביט ה-Pre-arm אינו מעיד על דבר.",
                 observed=f"{ltr(kind)}={ltr(value)}", expected="בדיקות מופעלות ו-Pre-arm תקין",
                 source=src, age_s=age, limitations=limits, evidence=ev)
    hb = ctx.msg("HEARTBEAT")
    if hb and P.is_armed(hb[0]):
        return R(Status.UNKNOWN, "הרחפן חמוש: ArduPilot מדווח את ביט ה-Pre-arm כתקין תמיד במצב חמוש, לכן אין כאן מידע.",
                 observed="ARMED", expected="DISARMED + PREARM_CHECK תקין", source=src, age_s=age,
                 limitations=limits, evidence=ev)
    if not pa["present"]:
        return R(Status.UNKNOWN, "הקושחה אינה מדווחת מצב Pre-arm ב-SYS_STATUS. היעדר הודעות PreArm אינו הוכחה.",
                 observed="PREARM_CHECK לא מדווח", expected="PREARM_CHECK תקין", source=src, age_s=age,
                 limitations=limits, evidence=ev)
    if not pa["enabled"]:
        return R(Status.UNKNOWN, "ArduPilot מדווח שבדיקות ה-Pre-arm אינן מופעלות (enabled=0), לכן אין משמעות למצב התקינות.",
                 observed="PREARM_CHECK: לא מופעל", expected="PREARM_CHECK מופעל ותקין", source=src, age_s=age,
                 limitations=limits, evidence=ev)
    diag_bit = (diag or {}).get("prearm_bit") if (diag or {}).get("state") == "complete" else None
    diag_failed = isinstance(diag_bit, dict) and diag_bit.get("present") and diag_bit.get("enabled") \
        and not diag_bit.get("healthy")
    if not pa["healthy"] or diag_failed or diag_texts:
        observed = "PREARM_CHECK: " + ("נכשל" if not pa["healthy"] or diag_failed else "תקין")
        if all_texts:
            observed += " · " + " | ".join(ltr(t) for t in all_texts[:4])
        if pa["healthy"] and not diag_failed:
            expl = "באבחון שהורץ ArduPilot שלח הודעות כשל PreArm."
        else:
            expl = "ArduPilot מדווח שבדיקות Pre-arm נכשלות."
        if not all_texts:
            expl += " טרם התקבלה הודעת PreArm עם הסיבה. לחץ 'הרץ בדיקה' כדי לבקש אבחון (או ראה Mission Planner)."
        return R(Status.FAIL, expl, observed=observed, expected="PREARM_CHECK תקין, ללא הודעות PreArm",
                 source=src, age_s=age, limitations=limits, evidence=ev)
    # Bit healthy and no failure text.
    note = " (לא נקרא ARMING_CHECK - לא ידוע אם חלק מהבדיקות מושבתות.)" if kind is None else ""
    if diag is not None and diag.get("state") == "complete":
        if not (isinstance(diag_bit, dict) and diag_bit.get("present") and diag_bit.get("enabled")):
            return R(Status.UNKNOWN, "האבחון הושלם, אבל ה-SYS_STATUS שאחריו לא דיווח ביט Pre-arm מופעל.",
                     observed="אבחון: ביט PREARM_CHECK חסר", expected="PREARM_CHECK מופעל ותקין",
                     source=src, age_s=age, limitations=limits, evidence=ev)
        return R(Status.PASS, "ArduPilot הריץ את בדיקות ה-Pre-arm לפי בקשה ומדווח שהן עוברות, בלי הודעות כשל." + note,
                 observed=f"PREARM_CHECK: תקין · אבחון הושלם (ACK {ltr(diag.get('result_name', ''))})",
                 expected="PREARM_CHECK תקין", source=src, age_s=age, limitations=limits, evidence=ev)
    if require:
        if diag is None:
            why = "האבחון עוד לא הורץ (או שהתוצאה ישנה). לחץ 'הרץ בדיקה' כשהרחפן לא חמוש."
            obs = "PREARM_CHECK: תקין · אבחון: לא הורץ"
        else:
            why = _diag_text(diag) + "."
            obs = f"PREARM_CHECK: תקין · אבחון: {ltr(diag.get('state', '?'))}"
        return R(Status.UNKNOWN, "הפרופיל דורש אבחון Pre-arm מפורש. " + why,
                 observed=obs, expected="אבחון הושלם + PREARM_CHECK תקין", source=src, age_s=age,
                 limitations=limits, evidence=ev)
    expl = "ArduPilot מדווח שבדיקות ה-Pre-arm עוברות." + note
    if diag is not None:
        expl += f" (אבחון מפורש: {_diag_text(diag)}.)"
    return R(Status.PASS, expl, observed="PREARM_CHECK: תקין", expected="PREARM_CHECK תקין",
             source=src, age_s=age, limitations=limits, evidence=ev)


_SEPARATE = {"prearm_check", "logging", "battery"}


@check("fc.sensors", "fc", "חיישנים: קיום, הפעלה ותקינות")
def fc_sensors(ctx, cfg):
    ss, age = _sys_status(ctx, cfg)
    src = ctx.src("MAVLink SYS_STATUS (present/enabled/health)")
    sens = P.sensors(ss)
    if sens is None:
        raise Unavailable(R(Status.UNKNOWN, "שדות החיישנים ב-SYS_STATUS אינם תקינים.", source=src, age_s=age))
    required = cfg.get("required") or []
    fails, warns = [], []
    for name in required:
        s = sens.get(name)
        label = C.SENSOR_LABELS_HE.get(name, name)
        if s is None:
            fails.append(f"{label}: שם לא מוכר")
        elif not s["present"]:
            fails.append(f"{label}: לא קיים")
        elif not s["enabled"]:
            fails.append(f"{label}: מושבת")
        elif not s["healthy"]:
            fails.append(f"{label}: לא תקין")
    for name, s in sens.items():
        if name in required or name in _SEPARATE:
            continue
        if s["present"] and s["enabled"] and not s["healthy"]:
            warns.append(f"{C.SENSOR_LABELS_HE.get(name, name)}: לא תקין")
    ok_list = [C.SENSOR_LABELS_HE.get(n, n) for n in required if n in sens and all(sens[n].values())]
    ev = {"sensors": {k: v for k, v in sens.items() if v["present"]}}
    observed = ("תקינים: " + ", ".join(ok_list)) if ok_list else ""
    if fails or warns:
        observed = "; ".join(fails + warns) + (" · " + observed if observed else "")
    expected = "קיים+מופעל+תקין: " + ", ".join(C.SENSOR_LABELS_HE.get(n, n) for n in required)
    if not required:
        return R(Status.UNKNOWN, "לא הוגדרו חיישנים נדרשים בפרופיל.", observed=observed, source=src,
                 age_s=age, configured=False, evidence=ev)
    if fails:
        return R(Status.FAIL, "חיישן נדרש חסר, מושבת או לא תקין.", observed=observed, expected=expected,
                 source=src, age_s=age, evidence=ev)
    if warns:
        return R(Status.WARN, "החיישנים הנדרשים תקינים, אך חיישן נוסף מדווח כלא תקין.", observed=observed,
                 expected=expected, source=src, age_s=age, evidence=ev)
    return R(Status.PASS, "כל החיישנים הנדרשים קיימים, מופעלים ותקינים.", observed=observed,
             expected=expected, source=src, age_s=age, evidence=ev)


@check("fc.logging", "fc", "רישום לוגים בבקר הטיסה")
def fc_logging(ctx, cfg):
    ss, age = _sys_status(ctx, cfg)
    src = ctx.src("MAVLink SYS_STATUS (LOGGING) + פרמטרי LOG_*")
    sens = P.sensors(ss)
    if sens is None:
        raise Unavailable(R(Status.UNKNOWN, "שדות SYS_STATUS אינם תקינים.", source=src, age_s=age))
    lg = sens["logging"]
    params = {n: (ctx.param(n) or (None,))[0] for n in ("LOG_BACKEND_TYPE", "LOG_DISARMED")}
    observed = f"present={lg['present']} enabled={lg['enabled']} healthy={lg['healthy']}"
    observed += "".join(f" · {n}={v}" for n, v in params.items() if v is not None)
    observed = ltr(observed)
    ev = {"logging_bit": lg, "params": params}
    if params.get("LOG_BACKEND_TYPE") == 0:
        return R(Status.FAIL, "רישום הלוגים מושבת (LOG_BACKEND_TYPE=0).", observed=observed,
                 expected="רישום פעיל ותקין", source=src, age_s=age, evidence=ev)
    if not lg["present"]:
        return R(Status.UNKNOWN, "הקושחה אינה מדווחת את מצב הרישום ב-SYS_STATUS.", observed=observed,
                 expected="רישום פעיל ותקין", source=src, age_s=age, evidence=ev)
    if not lg["healthy"]:
        return R(Status.FAIL, "ArduPilot מדווח על תקלה ברישום (למשל כרטיס SD).", observed=observed,
                 expected="רישום פעיל ותקין", source=src, age_s=age, evidence=ev)
    return R(Status.PASS, "ArduPilot מדווח שהרישום תקין.", observed=observed, expected="רישום פעיל ותקין",
             source=src, age_s=age, evidence=ev)


@check("fc.load", "fc", "עומס מעבד ושגיאות תקשורת בבקר הטיסה")
def fc_load(ctx, cfg):
    ss, age = _sys_status(ctx, cfg)
    src = ctx.src("MAVLink SYS_STATUS.load / errors_comm")
    load = num(ss.get("load"))
    errs = num(ss.get("errors_comm"))
    if load is None:
        raise Unavailable(R(Status.UNKNOWN, "שדה load לא תקין.", source=src, age_s=age))
    pct = load / 10.0  # SYS_STATUS.load is in d% (0..1000)
    warn, fail = num(cfg.get("warn_pct")), num(cfg.get("fail_pct"))
    observed = f"{pct:.1f}%" + (f" · errors_comm={int(errs)}" if errs is not None else "")
    expected = f"< {warn:g}% / < {fail:g}%" if warn is not None and fail is not None else "לא מוגדר"
    if warn is None and fail is None:
        return R(Status.UNKNOWN, "לא הוגדרו ספים.", observed=observed, source=src, age_s=age, configured=False)
    if fail is not None and pct >= fail:
        return R(Status.FAIL, "עומס מעבד קריטי בבקר הטיסה.", observed=observed, expected=expected, source=src, age_s=age)
    if warn is not None and pct >= warn:
        return R(Status.WARN, "עומס מעבד גבוה בבקר הטיסה.", observed=observed, expected=expected, source=src, age_s=age)
    if errs:
        return R(Status.WARN, "העומס תקין, אבל יש שגיאות תקשורת שדווחו (errors_comm).", observed=observed,
                 expected=expected + " · errors_comm=0", source=src, age_s=age)
    return R(Status.PASS, "העומס בבקר הטיסה תקין.", observed=observed, expected=expected, source=src, age_s=age)


def _gps(ctx, cfg):
    g, age = ctx.require_msg("GPS_RAW_INT", num(cfg.get("max_age_s")) or 5.0)
    return P.gps_raw(g), age


@check("gps.fix", "fc", "GPS: סוג קיבוע")
def gps_fix(ctx, cfg):
    g, age = _gps(ctx, cfg)
    src = ctx.src("MAVLink GPS_RAW_INT.fix_type")
    if g["fix_type"] is None:
        raise Unavailable(R(Status.UNKNOWN, "fix_type לא תקין.", source=src, age_s=age))
    want = num(cfg.get("min_fix_type"))
    observed = f"{ltr(g['fix_name'])} · לוויינים {ltr(g['satellites'] if g['satellites'] is not None else '?')}"
    if want is None:
        return R(Status.UNKNOWN, "לא הוגדר סוג קיבוע מינימלי.", observed=observed, source=src, age_s=age,
                 configured=False)
    expected = f"≥ {ltr(C.GPS_FIX_NAMES.get(int(want), want))}"
    if g["fix_type"] >= want:
        return R(Status.PASS, "קיבוע GPS מספק.", observed=observed, expected=expected, source=src, age_s=age, evidence=g)
    return R(Status.FAIL, "אין קיבוע GPS מספק.", observed=observed, expected=expected, source=src, age_s=age, evidence=g)


@check("gps.hdop", "fc", "GPS: HDOP")
def gps_hdop(ctx, cfg):
    g, age = _gps(ctx, cfg)
    src = ctx.src("MAVLink GPS_RAW_INT.eph")
    raw = cfg.get("max_hdop")
    thr, desc = ctx.resolve_threshold(raw)
    if isinstance(raw, str) and raw.upper() == "FC:GPS_HDOP_GOOD" and thr is not None:
        thr = thr / 100.0  # GPS_HDOP_GOOD is HDOP * 100
        desc += f" → {thr:g}"
    if g["hdop"] is None:
        raise Unavailable(R(Status.UNKNOWN, "HDOP לא ידוע (ערך 65535 / לא נשלח).", source=src, age_s=age,
                            expected=f"≤ {desc}"))
    observed = f"{g['hdop']:.2f}"
    if thr is None:
        return R(Status.UNKNOWN, "סף HDOP לא זמין (לא הוגדר או לא נקרא מבקר הטיסה).", observed=observed,
                 expected=desc, source=src, age_s=age, configured=False)
    if g["hdop"] <= thr:
        return R(Status.PASS, "HDOP בתוך הסף.", observed=observed, expected=f"≤ {desc}", source=src, age_s=age)
    return R(Status.FAIL, "HDOP גבוה מהסף.", observed=observed, expected=f"≤ {desc}", source=src, age_s=age)


@check("gps.satellites", "fc", "GPS: מספר לוויינים")
def gps_sats(ctx, cfg):
    g, age = _gps(ctx, cfg)
    src = ctx.src("MAVLink GPS_RAW_INT.satellites_visible")
    if g["satellites"] is None:
        raise Unavailable(R(Status.UNKNOWN, "מספר הלוויינים לא ידוע (255).", source=src, age_s=age))
    thr = num(cfg.get("min_sats"))
    observed = str(g["satellites"])
    if thr is None:
        return R(Status.UNKNOWN, "לא הוגדר סף לוויינים בפרופיל. אין סף אוניברסלי - הגדר אחד רק אם יש הצדקה.",
                 observed=observed, source=src, age_s=age, configured=False)
    if g["satellites"] >= thr:
        return R(Status.PASS, "מספר הלוויינים בסף.", observed=observed, expected=f"≥ {thr:g}", source=src, age_s=age)
    return R(Status.FAIL, "מעט מדי לוויינים.", observed=observed, expected=f"≥ {thr:g}", source=src, age_s=age)


@check("gps.accuracy", "fc", "GPS: דיוק אופקי מדווח")
def gps_accuracy(ctx, cfg):
    g, age = _gps(ctx, cfg)
    src = ctx.src("MAVLink GPS_RAW_INT.h_acc")
    if g["h_acc_m"] is None:
        raise Unavailable(R(Status.UNKNOWN, "הדיוק האופקי לא דווח (h_acc=0 או חסר).", source=src, age_s=age))
    thr = num(cfg.get("max_h_acc_m"))
    observed = f"{g['h_acc_m']:.2f} m"
    if thr is None:
        return R(Status.UNKNOWN, "לא הוגדר סף דיוק.", observed=observed, source=src, age_s=age, configured=False)
    if g["h_acc_m"] <= thr:
        return R(Status.PASS, "הדיוק המדווח בתוך הסף.", observed=observed, expected=f"≤ {thr:g} m", source=src, age_s=age)
    return R(Status.FAIL, "הדיוק המדווח גרוע מהסף.", observed=observed, expected=f"≤ {thr:g} m", source=src, age_s=age)


@check("ekf.status", "fc", "EKF: מצב ושונויות")
def ekf_status(ctx, cfg):
    e, age = ctx.require_msg("EKF_STATUS_REPORT", num(cfg.get("max_age_s")) or 5.0)
    src = ctx.src("MAVLink EKF_STATUS_REPORT")
    st = P.ekf_status(e)
    if st is None:
        raise Unavailable(R(Status.UNKNOWN, "דגלי EKF אינם תקינים.", source=src, age_s=age))
    required = cfg.get("required_flags") or []
    forbidden = cfg.get("forbidden_flags") or []
    missing = [f for f in required if not st["flags"].get(f)]
    bad = [f for f in forbidden if st["flags"].get(f)]
    warn_v, wdesc = ctx.resolve_threshold(cfg.get("variance_warn"))
    fail_v, fdesc = ctx.resolve_threshold(cfg.get("variance_fail"))
    var = {k: v for k, v in st["variances"].items() if v is not None and k != "terrain_alt_variance"}
    worst = max(var.items(), key=lambda kv: kv[1]) if var else None
    observed = "דגלים: " + ltr(", ".join(k for k, v in st["flags"].items() if v) or "אין")
    if worst:
        observed += f" · שונות מקס' {ltr(worst[0])}={worst[1]:.2f}"
    expected = "נדרש: " + ltr(", ".join(required)) + f" · שונות < {wdesc} / < {fdesc}"
    ev = st
    if missing or bad:
        parts = []
        if missing:
            parts.append("חסרים: " + ltr(", ".join(missing)))
        if bad:
            parts.append("פעילים ולא רצויים: " + ltr(", ".join(bad)))
        return R(Status.FAIL, "מצב EKF לא מספק: " + "; ".join(parts), observed=observed, expected=expected,
                 source=src, age_s=age, evidence=ev)
    if worst is None:
        return R(Status.UNKNOWN, "ערכי השונות לא דווחו.", observed=observed, expected=expected, source=src,
                 age_s=age, evidence=ev)
    if fail_v is not None and worst[1] >= fail_v:
        return R(Status.FAIL, "שונות EKF מעל סף הכשל של בקר הטיסה.", observed=observed, expected=expected,
                 source=src, age_s=age, evidence=ev)
    if warn_v is not None and worst[1] >= warn_v:
        return R(Status.WARN, "שונות EKF גבוהה.", observed=observed, expected=expected, source=src,
                 age_s=age, evidence=ev)
    if fail_v is None and warn_v is None:
        return R(Status.UNKNOWN, "ספי השונות לא זמינים.", observed=observed, expected=expected, source=src,
                 age_s=age, configured=False, evidence=ev)
    return R(Status.PASS, "דגלי EKF הנדרשים פעילים והשונויות נמוכות.", observed=observed, expected=expected,
             source=src, age_s=age, evidence=ev)


@check("nav.home", "fc", "נקודת בית (Home)")
def nav_home(ctx, cfg):
    src = ctx.src("MAVLink HOME_POSITION (MAV_CMD_REQUEST_MESSAGE)")
    got = ctx.msg("HOME_POSITION")
    max_age = num(cfg.get("max_age_s"))
    if got is None:
        why = ctx.unsupported("HOME_POSITION")
        expl = ("נקודת הבית לא התקבלה. ArduPilot שולח HOME_POSITION רק אחרי שהבית נקבע (בדרך כלל אחרי קיבוע GPS)."
                if not why else f"הבקשה ל-HOME_POSITION נדחתה ({ltr(why)}).")
        raise Unavailable(R(Status.UNKNOWN, expl, source=src, expected="Home מוגדר", missing=("HOME_POSITION",)))
    h, age = got
    if max_age is not None and age > max_age:
        raise Unavailable(R(Status.UNKNOWN, f"נתון הבית ישן ({fmt_age(age)}).", source=src, age_s=age))
    home = P.home_position(h)
    if home is None:
        return R(Status.FAIL, "HOME_POSITION התקבל אך ללא מיקום תקין.", observed="0,0", expected="Home מוגדר",
                 source=src, age_s=age)
    return R(Status.PASS, "נקודת הבית מוגדרת.", observed=ltr(f"{home['lat']:.6f}, {home['lon']:.6f}"),
             expected="Home מוגדר", source=src, age_s=age, evidence=home)
