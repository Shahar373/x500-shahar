"""Preflight report: JSON (machine-readable) + standalone RTL HTML.

Everything passes through :func:`redact` before it is written.
"""

from __future__ import annotations

import html
import json
import time
from pathlib import Path
from typing import Any

from .. import APP_NAME, __version__
from .checks.base import CATEGORIES, CATEGORY_ORDER, fmt_age
from .mav import parse as P
from .observations import Snapshot
from .redact import redact
from .status import CheckResult, OverallResult

SCHEMA = "x500fs.report/1"

DISCLAIMER = (
    "הדוח מסכם בדיקות אוטומטיות ואישורים ידניים בנקודת זמן מסוימת. הוא אינו מבטיח טיסה בטוחה ואינו מחליף "
    "את שיקול הדעת של המטיס, את Mission Planner או את בדיקות ה-Pre-arm של ArduPilot."
)

OVERALL_TEXT = {
    "NOT_RUN": "הבדיקות לא הורצו",
    "PASS": "בדיקות אוטומטיות עברו",
    "PASS_WITH_WARNINGS": "בדיקות אוטומטיות עברו עם אזהרות",
    "INCOMPLETE": "לא הושלם - חסרים נתונים או הגדרות בבדיקה קריטית",
    "FAIL": "בדיקה קריטית נכשלה",
}

STATUS_TEXT = {"PASS": "עבר", "WARN": "אזהרה", "FAIL": "נכשל", "UNKNOWN": "לא ידוע", "NOT_APPLICABLE": "לא רלוונטי"}


def hardware_identifiers(snap: Snapshot) -> dict[str, Any]:
    hw: dict[str, Any] = {}
    o = snap.get("pi.collector")
    if o and isinstance(o.value, dict):
        try:
            ident = o.value["sections"]["identity"]["data"] or {}
            hw["pi"] = {k: ident.get(k) for k in ("hostname", "model", "kernel", "machine", "os_release", "boot_id")}
        except (KeyError, TypeError):
            pass
        try:
            hw["camera"] = o.value["sections"]["camera"]["data"].get("cameras")
        except (KeyError, TypeError, AttributeError):
            pass
        try:
            mr = o.value["sections"]["mavlink_router"]["data"]
            hw["mavlink_router_endpoints"] = mr.get("endpoints")
        except (KeyError, TypeError, AttributeError):
            pass
        try:
            hw["usb"] = o.value["sections"]["usb"]["data"].get("devices")
        except (KeyError, TypeError, AttributeError):
            pass
    hb = snap.get("mav.msg.HEARTBEAT")
    if hb:
        hw["autopilot_heartbeat"] = {k: hb.value.get(k) for k in ("type", "autopilot", "mavlink_version")}
    av = snap.get("mav.msg.AUTOPILOT_VERSION")
    if av:
        hw["autopilot_version"] = P.autopilot_version(av.value)
    link = snap.get("mav.link")
    if link:
        hw["mavlink_link"] = {k: link.value.get(k) for k in ("endpoint", "target_sysid", "my_sysid", "my_compid")}
    ssh = snap.get("pi.ssh")
    if ssh:
        hw["ssh_host_key"] = ssh.value.get("fingerprint")
    return hw


def build_report(*, snap: Snapshot, results: list[CheckResult], overall: OverallResult, profile_summary: dict,
                 profile_error: str | None, settings_summary: dict, manual: list[dict], manual_done: int,
                 manual_total: int, interruptions: list[dict], events: list[dict], statustext: list[dict],
                 video: dict | None, session_id: str, preflight_started: float | None,
                 extra_secrets: list[str] | None = None) -> dict[str, Any]:
    missing = []
    for r in results:
        for m in r.missing:
            missing.append({"check": r.check_id, "missing": m})
    warnings = [
        {"check": r.check_id, "status": r.status.value, "critical": r.critical, "text": r.explanation}
        for r in results if r.status.value in ("WARN", "FAIL", "UNKNOWN")
    ]
    if profile_error:
        warnings.insert(0, {"check": "profile", "status": "WARN", "critical": True,
                            "text": f"קובץ הפרופיל אינו תקין ונעשה שימוש בברירת המחדל: {profile_error}"})
    doc = {
        "schema": SCHEMA,
        "app": {"name": APP_NAME, "version": __version__},
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "simulation": snap.simulated,
        "session": {
            "id": session_id,
            "preflight_started_at": None if preflight_started is None
            else time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(preflight_started)),
        },
        "profile": profile_summary,
        "connection": settings_summary,
        "hardware": hardware_identifiers(snap),
        "automatic_checks": {
            "overall": overall.to_dict(),
            "overall_text": OVERALL_TEXT[overall.status.value],
            "results": [r.to_dict() for r in results],
        },
        "manual_checklist": {
            "completed": manual_done == manual_total,
            "done": manual_done,
            "total": manual_total,
            "items": manual,
        },
        "missing_evidence": missing,
        "warnings": warnings,
        "interruptions": interruptions,
        "events": events[-200:],
        "autopilot_messages": statustext[-100:],
        "video": video,
        "disclaimer": DISCLAIMER,
    }
    return redact(doc, extra_secrets)


def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def render_html(doc: dict) -> str:
    sim = doc.get("simulation")
    overall = doc["automatic_checks"]["overall"]["status"]
    manual = doc["manual_checklist"]
    rows_by_cat: dict[str, list[dict]] = {}
    for r in doc["automatic_checks"]["results"]:
        rows_by_cat.setdefault(r["category"], []).append(r)
    parts = []
    for cat in CATEGORY_ORDER:
        rows = rows_by_cat.get(cat)
        if not rows:
            continue
        parts.append(f"<h3>{_e(CATEGORIES[cat])}</h3><table><thead><tr><th>סטטוס</th><th>בדיקה</th>"
                     "<th>הסבר</th><th>נצפה</th><th>צפוי</th><th>מקור</th><th>גיל</th></tr></thead><tbody>")
        for r in rows:
            crit = " <span class='crit'>קריטי</span>" if r["critical"] else ""
            lim = f"<div class='lim'>מגבלה: {_e(r['limitations'])}</div>" if r.get("limitations") else ""
            parts.append(
                f"<tr><td><span class='st st-{_e(r['status'])}'>{_e(r['status'])}</span>"
                f"<div class='sth'>{_e(STATUS_TEXT.get(r['status']))}</div></td>"
                f"<td>{_e(r['title'])}{crit}<div class='id'>{_e(r['id'])}</div></td>"
                f"<td>{_e(r['explanation'])}{lim}</td><td>{_e(r['observed'])}</td><td>{_e(r['expected'])}</td>"
                f"<td class='src'>{_e(r['source'])}</td><td>{_e(fmt_age(r['age_s']))}</td></tr>")
        parts.append("</tbody></table>")
    manual_rows = "".join(
        f"<tr><td>{'✔' if i['confirmed'] else '✘'}</td><td>{_e(i['title'])}</td><td>{_e(i['detail'])}</td>"
        f"<td>{_e(i['confirmed_at'] or '')}</td></tr>" for i in manual["items"])
    intr = "".join(
        f"<tr><td>{_e(i['link'])}</td><td>{_e(i['start'])}</td><td>{_e(i['end'] or 'נמשך')}</td>"
        f"<td>{_e(i['duration_s'])}s</td><td>{_e(i['reason'])}</td></tr>" for i in doc["interruptions"]) \
        or "<tr><td colspan='5'>לא נרשמו ניתוקים</td></tr>"
    missing = "".join(f"<li><code>{_e(m['check'])}</code>: {_e(m['missing'])}</li>" for m in doc["missing_evidence"]) \
        or "<li>אין</li>"
    msgs = "".join(f"<tr><td>{_e(time.strftime('%H:%M:%S', time.localtime(m.get('t_wall', 0))))}</td>"
                   f"<td>{_e(m.get('severity_name'))}</td><td dir='ltr'>{_e(m.get('text'))}</td></tr>"
                   for m in doc["autopilot_messages"]) or "<tr><td colspan='3'>אין</td></tr>"
    hw = _e(json.dumps(doc["hardware"], ensure_ascii=False, indent=2))
    prof = doc["profile"]
    conn = doc["connection"]
    sim_banner = ("<div class='simbanner'>סימולציה - הנתונים בדוח זה מדומים ואינם מהרחפן</div>" if sim else "")
    return f"""<!doctype html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{'[סימולציה] ' if sim else ''}דוח בדיקות לפני טיסה - {_e(doc['app']['name'])}</title>
<style>
body{{font-family:"Segoe UI",Arial,sans-serif;margin:24px;color:#1d2330;background:#fff}}
h1{{margin:0 0 4px}} h2{{margin-top:28px;border-bottom:2px solid #dde3ea;padding-bottom:4px}}
table{{border-collapse:collapse;width:100%;margin:8px 0 16px;font-size:13px}}
th,td{{border:1px solid #dde3ea;padding:6px 8px;vertical-align:top;text-align:right}}
th{{background:#f3f6f9}} .id,.src,.lim{{color:#5f6b7a;font-size:11px}} .lim{{margin-top:4px}}
.st{{display:inline-block;padding:2px 6px;border-radius:4px;color:#fff;font-weight:600;font-size:11px;direction:ltr}}
.st-PASS{{background:#1b7f3b}} .st-WARN{{background:#b26a00}} .st-FAIL{{background:#c62828}}
.st-UNKNOWN{{background:#5f6b7a}} .st-NOT_APPLICABLE{{background:#9aa0a6}}
.sth{{font-size:11px;color:#5f6b7a}} .crit{{color:#c62828;font-size:11px;font-weight:600}}
.summary{{display:flex;gap:16px;flex-wrap:wrap}} .card{{border:1px solid #dde3ea;border-radius:8px;padding:12px 16px;min-width:240px}}
.big{{font-size:18px;font-weight:700}} .ov-PASS{{color:#1b7f3b}} .ov-PASS_WITH_WARNINGS{{color:#b26a00}}
.ov-INCOMPLETE{{color:#5f6b7a}} .ov-FAIL{{color:#c62828}}
.simbanner{{background:repeating-linear-gradient(45deg,#ffb300,#ffb300 12px,#ffd54f 12px,#ffd54f 24px);
padding:12px;font-weight:700;font-size:18px;text-align:center;margin-bottom:16px;border:2px solid #e65100}}
.disclaimer{{background:#fff8e1;border:1px solid #ffe082;padding:10px;border-radius:6px}}
pre{{direction:ltr;text-align:left;background:#f6f8fa;padding:8px;overflow:auto;font-size:12px}}
code{{direction:ltr;unicode-bidi:isolate}}
</style></head><body>
{sim_banner}
<h1>דוח בדיקות לפני טיסה</h1>
<div>{_e(doc['app']['name'])} גרסה {_e(doc['app']['version'])} · נוצר {_e(doc['generated_at'])} · סשן {_e(doc['session']['id'])}</div>
<p class="disclaimer">{_e(doc['disclaimer'])}</p>
<div class="summary">
<div class="card"><div>בדיקות אוטומטיות</div><div class="big ov-{_e(overall)}">{_e(doc['automatic_checks']['overall_text'])}</div>
<div>{_e(json.dumps(doc['automatic_checks']['overall']['counts']))}</div></div>
<div class="card"><div>רשימת בדיקה ידנית</div><div class="big">{'הושלמה' if manual['completed'] else 'לא הושלמה'} ({manual['done']}/{manual['total']})</div>
<div>בדיקות אוטומטיות ורשימה ידנית הן שני דברים נפרדים.</div></div>
<div class="card"><div>פרופיל</div><div class="big">{_e(prof.get('name'))}</div>
<div>מצב: {_e(prof.get('mode'))} · נתיב: {_e(conn.get('link_path'))}</div>
<div class="id">{_e(prof.get('source'))} · sha256 {_e((prof.get('sha256') or '')[:16])}</div></div>
</div>
<h2>בדיקות אוטומטיות</h2>
{''.join(parts)}
<h2>רשימת בדיקה ידנית</h2>
<table><thead><tr><th></th><th>פריט</th><th>פירוט</th><th>אושר ב-</th></tr></thead><tbody>{manual_rows}</tbody></table>
<h2>ראיות חסרות</h2><ul>{missing}</ul>
<h2>ניתוקים בסשן</h2>
<table><thead><tr><th>קישור</th><th>התחלה</th><th>סיום</th><th>משך</th><th>סיבה</th></tr></thead><tbody>{intr}</tbody></table>
<h2>הודעות מבקר הטיסה (STATUSTEXT)</h2>
<table><thead><tr><th>שעה</th><th>חומרה</th><th>טקסט</th></tr></thead><tbody>{msgs}</tbody></table>
<h2>מזהי חומרה וקושחה</h2><pre>{hw}</pre>
<h2>חיבור</h2><pre>{_e(json.dumps(conn, ensure_ascii=False, indent=2))}</pre>
</body></html>
"""


def save_report(doc: dict, directory: Path) -> tuple[Path, Path]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = f"x500fs-report-{stamp}" + ("-SIMULATION" if doc.get("simulation") else "")
    jp = directory / f"{base}.json"
    hp = directory / f"{base}.html"
    jp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", "utf-8")
    hp.write_text(render_html(doc), "utf-8")
    return jp, hp
