"""Four separate, honest video indicators (Qt-free so it can be tested).

1. Pi service   - systemd state of x500-video@<profile> (Pi collector).
2. Encoder      - ffmpeg -progress file on the Pi (updates = encoder output,
                  and with an SRT listener: a caller is connected).
3. Transport    - the desktop FFmpeg caller's connection state.
4. Decoded      - age of the last frame decoded on this computer.

Only (4) shows that a live picture reaches the screen; (1)-(3) never imply it.
"""

from __future__ import annotations

from ..core.checks.base import fmt_age

OK, WARN, BAD, UNKNOWN, OFF = "ok", "warn", "bad", "unknown", "off"
PI_SECTION_MAX_AGE_S = 35.0
PROGRESS_MAX_AGE_S = 3.0
TRANSPORT_HE = {"connecting": "מתחבר", "reconnecting": "מתחבר מחדש", "playing": "מחובר", "failed": "נכשל",
                "stopped": "כבוי"}


def _ltr(x) -> str:
    return f"⁦{x}⁩"


def _video_section(snap, now):
    col = snap.get("pi.collector")
    if col is None or not isinstance(col.value, dict):
        return None, None
    sec = (col.value.get("sections") or {}).get("video")
    if not isinstance(sec, dict):
        return None, None
    rec = sec.get("_received_mono")
    age = now - float(rec) if isinstance(rec, (int, float)) else now - col.t_mono
    if not sec.get("ok") or not isinstance(sec.get("data"), dict):
        return None, age
    return sec["data"], age


def pipeline_summary(snap, worker_status: dict | None, simulated: bool = False) -> dict[str, tuple[str, str]]:
    now = snap.now_mono
    out: dict[str, tuple[str, str]] = {}
    data, age = _video_section(snap, now)
    active = None
    if data is None or age is None or age > PI_SECTION_MAX_AGE_S:
        why = "אין נתוני Pi" if age is None else f"נתוני Pi ישנים ({fmt_age(age)})"
        out["service"] = (f"לא ידוע · {why}", UNKNOWN)
        out["encoder"] = ("לא ידוע", UNKNOWN)
    else:
        active = data.get("active_profile")
        if not active:
            out["service"] = (f"כבוי · {fmt_age(age)}", OFF)
            out["encoder"] = ("—", OFF)
        else:
            u = (data.get("units") or {}).get(active) or {}
            st = f"{u.get('ActiveState')}/{u.get('SubState')}"
            restarts = u.get("NRestarts")
            text = f"{_ltr('x500-video@' + active)}: {_ltr(st)}"
            if restarts not in (None, "", "0"):
                text += f" · הפעלות מחדש {_ltr(restarts)}"
            out["service"] = (text + f" · {fmt_age(age)}", OK if u.get("SubState") == "running" else WARN)
            prog = data.get("progress") or {}
            pa = prog.get("mtime_age_s")
            rate = prog.get("frame_rate_between_polls")
            frame = (prog.get("parsed") or {}).get("frame")
            if not prog or prog.get("exists") is False:
                out["encoder"] = ("אין קובץ progress (ממתין ל-caller)", WARN)
            elif not isinstance(pa, (int, float)) or pa > PROGRESS_MAX_AGE_S:
                out["encoder"] = (f"לא מתקדם · progress עודכן לפני {fmt_age(pa)}", BAD)
            elif isinstance(rate, (int, float)) and rate <= 0:
                out["encoder"] = ("progress מתעדכן אבל מספר הפריימים לא עולה", BAD)
            else:
                text = f"מתקדם · frame={_ltr(frame)}" if frame is not None else "מתקדם"
                if isinstance(rate, (int, float)):
                    text += f" · {_ltr(f'{rate:.1f} fps')} בין דגימות"
                out["encoder"] = (text, OK)
    if simulated:
        out["service"] = ("SIM · " + out["service"][0], out["service"][1])
    w = worker_status or {}
    state = w.get("state") or "stopped"
    if state == "stopped":
        out["transport"] = ("כבוי", OFF)
    else:
        kind = {"srt": "SRT caller", "rtsp": "RTSP", "file": "קובץ"}.get(w.get("transport") or "", w.get("backend") or "")
        text = f"{_ltr(kind)} · {TRANSPORT_HE.get(state, state)}"
        if w.get("reconnects"):
            text += f" · התחברויות מחדש {w['reconnects']}"
        if state != "playing" and w.get("error"):
            text += f" · {w['error'][:140]}"
        out["transport"] = (text, OK if state == "playing" else BAD if state == "failed" else WARN)
    last = w.get("last_frame_mono")
    if state == "stopped" or not isinstance(last, (int, float)):
        out["decoded"] = ("אין פריימים" if state != "stopped" else "—", OFF if state == "stopped" else WARN)
    else:
        fage = max(0.0, now - last)
        stale = float(w.get("stale_after_s") or 2.0)
        fps = w.get("fps")
        text = f"פריים אחרון לפני {fmt_age(fage)}"
        if isinstance(fps, (int, float)) and fage <= stale:
            text += f" · {_ltr(f'{fps:.1f} FPS')}"
        if w.get("source_size"):
            text += f" · מקור {_ltr(w['source_size'])}"
        out["decoded"] = (text + (" · חי" if fage <= stale else " · לא חי"), OK if fage <= stale else BAD)
    return out
