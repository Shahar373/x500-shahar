"""Battery and read-only configuration comparison checks."""

from __future__ import annotations

from ..mav import parse as P
from ..params import PARAM_GROUPS
from ..profile import rule_matches, rule_text
from ..status import Status
from .base import R, Unavailable, check, ltr, num

# Plausible per-cell voltage ranges (resting, any state of charge). Used only to
# flag an implausible reading - e.g. wrong cell count or monitor calibration -
# never to declare a battery "good".
CELL_RANGE = {"LiPo": (3.0, 4.25), "LiPoHV": (3.0, 4.4), "LiIon": (2.5, 4.25)}


def _batt(ctx, cfg):
    ss, age = ctx.require_msg("SYS_STATUS", num(cfg.get("max_age_s")) or 5.0)
    return P.battery_from_sys_status(ss), age


@check("batt.cells_config", "battery", "סוג סוללה ומספר תאים (בפרופיל)")
def batt_cells_config(ctx, cfg):
    b = ctx.profile.battery or {}
    chem, cells = b.get("chemistry"), b.get("cells")
    src = "profile"
    observed = f"{ltr(chem or 'לא הוגדר')} · {ltr(cells or 'לא הוגדר')}S"
    if not chem or not cells:
        return R(Status.UNKNOWN, "סוג הסוללה ו/או מספר התאים לא הוגדרו בפרופיל. האפליקציה לא מנחשת אותם.",
                 observed=observed, expected="chemistry + cells מוגדרים", source=src, configured=False)
    return R(Status.PASS, "סוג הסוללה ומספר התאים מוגדרים בפרופיל (לא נמדדים).", observed=observed,
             expected="chemistry + cells מוגדרים", source=src,
             limitations="ערכים אלה נלקחים מהפרופיל בלבד. ודא שהם תואמים לסוללה המחוברת.")


@check("batt.voltage", "battery", "מתח סוללה")
def batt_voltage(ctx, cfg):
    b, age = _batt(ctx, cfg)
    src = ctx.src("MAVLink SYS_STATUS.voltage_battery")
    v = b["voltage_v"]
    if v is None:
        raise Unavailable(R(Status.UNKNOWN, "מתח הסוללה לא ידוע (UINT16_MAX / לא נשלח).", source=src, age_s=age,
                            missing=("voltage_battery",)))
    batt = ctx.profile.battery or {}
    cells = batt.get("cells")
    chem = batt.get("chemistry")
    observed = f"{v:.2f} V"
    if cells:
        observed += f" ({v / cells:.2f} V/תא)"
    if v < 5.0:
        return R(Status.UNKNOWN, "המתח מתחת ל-5V - זו לא קריאה של סוללת טיסה (למשל הזנה מ-USB בלבד, או חיישן לא מכויל).",
                 observed=observed, expected="קריאת סוללת טיסה תקפה", source=src, age_s=age)
    warn, wdesc = ctx.resolve_threshold(cfg.get("warn_v"))
    fail, fdesc = ctx.resolve_threshold(cfg.get("fail_v"))
    # ArduPilot uses 0 to mean "disabled" for BATT_LOW_VOLT / BATT_CRT_VOLT.
    if warn is not None and warn <= 0:
        warn, wdesc = None, wdesc + " (0 = מושבת)"
    if fail is not None and fail <= 0:
        fail, fdesc = None, fdesc + " (0 = מושבת)"
    pre = num(batt.get("preflight_min_cell_v"))
    expected = f"> {wdesc} (אזהרה) · > {fdesc} (קריטי)"
    if pre and cells:
        expected += f" · ≥ {pre:g} V/תא לפני המראה"
    ev = {"voltage_v": v, "cells": cells, "chemistry": chem, "warn_v": warn, "fail_v": fail}
    if cells and chem in CELL_RANGE:
        lo, hi = CELL_RANGE[chem]
        per = v / cells
        if not lo <= per <= hi:
            return R(Status.FAIL, f"מתח לתא ({per:.2f}V) מחוץ לטווח הסביר ל-{ltr(chem)}. ייתכן מספר תאים שגוי בפרופיל או כיול שגוי של מד המתח.",
                     observed=observed, expected=f"{lo}-{hi} V/תא", source=src, age_s=age, evidence=ev)
    if fail is not None and v <= fail:
        return R(Status.FAIL, "המתח בגבול הקריטי של בקר הטיסה.", observed=observed, expected=expected,
                 source=src, age_s=age, evidence=ev)
    if warn is not None and v <= warn:
        return R(Status.WARN, "המתח בגבול הנמוך של בקר הטיסה.", observed=observed, expected=expected,
                 source=src, age_s=age, evidence=ev)
    if pre and cells and v / cells < pre:
        return R(Status.WARN, "המתח לתא נמוך מהמינימום שהוגדר להמראה.", observed=observed, expected=expected,
                 source=src, age_s=age, evidence=ev)
    if warn is None and fail is None and not (pre and cells):
        return R(Status.UNKNOWN, "אין ספי מתח מוגדרים (BATT_LOW_VOLT/BATT_CRT_VOLT מושבתים ולא הוגדר מינימום בפרופיל).",
                 observed=observed, expected=expected, source=src, age_s=age, configured=False, evidence=ev)
    return R(Status.PASS, "המתח מעל הספים המוגדרים.", observed=observed, expected=expected, source=src,
             age_s=age, evidence=ev,
             limitations="מתח במנוחה אינו מעיד על קיבולת או על מצב הסוללה תחת עומס. הקריאה תלויה בכיול מד הסוללה "
                         "(BATT_VOLT_MULT) - ראו 'כיול מד הסוללה מול פרופיל צפוי'. בדיקת תוכנה אינה מאשרת את מודול "
                         "הכוח, החיווט או המחברים, ואינה רואה צניחות מתח מהירות בין דגימות הטלמטריה.")


@check("batt.monitor", "battery", "הגדרת מד הסוללה (BATT_MONITOR)")
def batt_monitor(ctx, cfg):
    src = ctx.src("MAVLink PARAM_VALUE (קריאה בלבד)")
    got = ctx.param("BATT_MONITOR")
    if got is None:
        raise Unavailable(R(Status.UNKNOWN, "BATT_MONITOR לא נקרא מבקר הטיסה.", source=src, missing=("BATT_MONITOR",)))
    val, age = got
    others = {n: (ctx.param(n) or (None,))[0] for n in ("BATT_CAPACITY", "BATT_VOLT_MULT", "BATT_AMP_PERVLT", "BATT_ARM_VOLT")}
    observed = f"BATT_MONITOR={val}" + "".join(f" · {n}={v}" for n, v in others.items() if v is not None)
    observed = ltr(observed)
    exp = cfg.get("expected_monitor")
    ev = {"BATT_MONITOR": val, **others}
    if int(val) == 0:
        return R(Status.FAIL, "מד הסוללה מושבת - אין קריאת מתח/זרם אמינה.", observed=observed,
                 expected="BATT_MONITOR ≠ 0", source=src, age_s=age, evidence=ev)
    if exp is not None and num(exp) is not None and int(val) != int(exp):
        return R(Status.FAIL, "סוג מד הסוללה שונה מהמוגדר בפרופיל.", observed=observed,
                 expected=f"BATT_MONITOR={exp}", source=src, age_s=age, evidence=ev)
    expl = "מד הסוללה מופעל."
    if exp is None:
        expl += " (סוג המד הצפוי לא הוגדר בפרופיל - נבדק רק שהוא מופעל.)"
    return R(Status.PASS, expl, observed=observed,
             expected=f"BATT_MONITOR={exp}" if exp is not None else "BATT_MONITOR ≠ 0",
             source=src, age_s=age, evidence=ev,
             limitations="נבדקת ההגדרה בלבד; כיול החיישן (מכפילי מתח/זרם) אינו מאומת כאן.")


@check("batt.current", "battery", "זרם סוללה")
def batt_current(ctx, cfg):
    b, age = _batt(ctx, cfg)
    src = ctx.src("MAVLink SYS_STATUS.current_battery")
    a = b["current_a"]
    if a is None:
        raise Unavailable(R(Status.UNKNOWN, "הזרם לא נמדד (-1).", source=src, age_s=age, missing=("current_battery",)))
    observed = f"{a:.2f} A"
    if a < -0.5:
        return R(Status.WARN, "זרם שלילי - ייתכן כיול שגוי של חיישן הזרם.", observed=observed,
                 expected="≥ 0 A", source=src, age_s=age)
    return R(Status.PASS, "קריאת זרם זמינה.", observed=observed, expected="קריאה זמינה, ≥ 0 A", source=src, age_s=age)


@check("batt.remaining", "battery", "קיבולת נותרת (הערכה)")
def batt_remaining(ctx, cfg):
    b, age = _batt(ctx, cfg)
    src = ctx.src("MAVLink SYS_STATUS.battery_remaining")
    lim = ("זו הערכה של ArduPilot על בסיס צריכה (mAh) ו-BATT_CAPACITY. אחרי הדלקה היא מתחילה בדרך כלל מ-100% "
           "גם אם הסוללה אינה מלאה.")
    pct = b["remaining_pct"]
    if pct is None:
        raise Unavailable(R(Status.UNKNOWN, "הקיבולת הנותרת לא ידועה (-1).", source=src, age_s=age, limitations=lim))
    thr = num(cfg.get("warn_pct"))
    observed = f"{pct}%"
    if thr is None:
        return R(Status.NOT_APPLICABLE, "לא הוגדר סף בפרופיל. הערך מוצג לידיעה.", observed=observed,
                 source=src, age_s=age, limitations=lim)
    if pct < thr:
        return R(Status.WARN, "קיבולת נותרת נמוכה מהסף.", observed=observed, expected=f"≥ {thr:g}%",
                 source=src, age_s=age, limitations=lim)
    return R(Status.PASS, "הקיבולת המוערכת מעל הסף.", observed=observed, expected=f"≥ {thr:g}%",
             source=src, age_s=age, limitations=lim)


def _param_group(ctx, group: str, title: str):
    rules = (ctx.profile.expected_params or {}).get(group) or {}
    src = ctx.src("MAVLink PARAM_VALUE (קריאה בלבד) מול פרופיל צפוי")
    current = {}
    for n in PARAM_GROUPS.get(group, []):
        got = ctx.param(n)
        if got is not None:
            current[n] = got[0]
    if not rules:
        observed = ltr(", ".join(f"{k}={v}" for k, v in current.items())) if current else "לא נקראו פרמטרים"
        return R(Status.UNKNOWN,
                 f"לא הוגדר פרופיל צפוי עבור {title}. הערכים הנוכחיים מוצגים אך לא אומתו.",
                 observed=observed, expected="פרופיל צפוי מוגדר", source=src, configured=False,
                 evidence={"current": current})
    mismatches, missing, ok = [], [], []
    ages = []
    for pname, rule in rules.items():
        got = ctx.param(pname)
        if got is None:
            missing.append(pname)
            continue
        val, age = got
        ages.append(age)
        (ok if rule_matches(rule, val) else mismatches).append(f"{pname}={val} (צפוי {rule_text(rule)})")
    ev = {"rules": rules, "current": current, "mismatches": mismatches, "missing": missing}
    observed = ltr("; ".join(mismatches + ok)) if (mismatches or ok) else "—"
    expected = ltr("; ".join(f"{p} {rule_text(r)}" for p, r in rules.items()))
    age = max(ages) if ages else None
    if mismatches:
        return R(Status.FAIL, f"ערכי {title} שונים מהפרופיל הצפוי.", observed=observed, expected=expected,
                 source=src, age_s=age, evidence=ev)
    if missing:
        return R(Status.UNKNOWN, "חלק מהפרמטרים לא נקראו: " + ltr(", ".join(missing)), observed=observed,
                 expected=expected, source=src, age_s=age, missing=tuple(missing), evidence=ev)
    return R(Status.PASS, f"ערכי {title} תואמים לפרופיל הצפוי.", observed=observed, expected=expected,
             source=src, age_s=age, evidence=ev,
             limitations="השוואה לקריאה בלבד. האפליקציה אינה משנה פרמטרים.")


@check("cfg.battery_monitor", "battery", "כיול מד הסוללה מול פרופיל צפוי")
def cfg_battery_monitor(ctx, cfg):
    return _param_group(ctx, "battery_monitor", "מד הסוללה (BATT_MONITOR/VOLT_MULT/AMP_PERVLT/CAPACITY)")


@check("cfg.failsafe", "battery", "הגדרות Failsafe מול פרופיל צפוי")
def cfg_failsafe(ctx, cfg):
    return _param_group(ctx, "failsafe", "Failsafe")


@check("cfg.rtl", "battery", "הגדרות RTL מול פרופיל צפוי")
def cfg_rtl(ctx, cfg):
    return _param_group(ctx, "rtl", "RTL")


@check("cfg.fence", "battery", "הגדרות גדר (Fence) מול פרופיל צפוי")
def cfg_fence(ctx, cfg):
    return _param_group(ctx, "fence", "Fence")


@check("cfg.gcs_identity", "battery", "זהות האפליקציה מול תחנת הקרקע")
def cfg_gcs_identity(ctx, cfg):
    src = ctx.src("MAVLink PARAM_VALUE: MAV_GCS_SYSID(_HI) / SYSID_MYGCS, MAV_OPTIONS / SYSID_ENFORCE")
    mine = ctx.drone.mav_source_sysid
    gcs = ctx.param("MAV_GCS_SYSID") or ctx.param("SYSID_MYGCS")
    hi = ctx.param("MAV_GCS_SYSID_HI")
    enforce = ctx.param("SYSID_ENFORCE")
    options = ctx.param("MAV_OPTIONS")
    lim = ("האפליקציה לעולם אינה שולחת HEARTBEAT, RC_CHANNELS_OVERRIDE או MANUAL_CONTROL - ההודעות היחידות "
           "שמאפסות את טיימר ה-GCS failsafe ב-ArduPilot. לכן היא אינה יכולה להיחשב כתחנת הקרקע ואינה מסתירה "
           "ניתוק של Mission Planner.")
    if gcs is None:
        raise Unavailable(R(Status.UNKNOWN, "MAV_GCS_SYSID / SYSID_MYGCS לא נקראו.", source=src, limitations=lim))
    lo = int(gcs[0])
    top = int(hi[0]) if hi is not None and int(hi[0]) >= lo else lo
    enforced = (enforce is not None and int(enforce[0]) == 1) or (options is not None and int(options[0]) & 1)
    observed = f"GCS sysid={lo}" + (f"..{top}" if top != lo else "") + f" · app sysid={mine}"
    observed += f" · enforce={'on' if enforced else 'off'}"
    observed = ltr(observed)
    ev = {"gcs_sysid": lo, "gcs_sysid_hi": top, "app_sysid": mine, "enforce": bool(enforced)}
    if lo <= mine <= top:
        return R(Status.WARN, "ה-sysid של האפליקציה נמצא בטווח של תחנת הקרקע. שנה את sysid האפליקציה בהגדרות.",
                 observed=observed, expected="sysid מחוץ לטווח תחנת הקרקע", source=src, age_s=gcs[1], limitations=lim, evidence=ev)
    if enforced:
        return R(Status.WARN, "בקר הטיסה מקבל רק הודעות מתחנת הקרקע (SYSID_ENFORCE / MAV_OPTIONS bit 0). "
                 "בקשות הקריאה של האפליקציה יידחו ונתונים יופיעו כחסרים.",
                 observed=observed, expected="sysid מחוץ לטווח תחנת הקרקע", source=src, age_s=gcs[1], limitations=lim, evidence=ev)
    return R(Status.PASS, "האפליקציה משתמשת ב-sysid נפרד מתחנת הקרקע.", observed=observed,
             expected="sysid מחוץ לטווח תחנת הקרקע", source=src, age_s=gcs[1], limitations=lim, evidence=ev)
