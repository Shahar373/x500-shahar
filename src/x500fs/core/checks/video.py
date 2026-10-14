"""Video stream check (separate from telemetry; a warning unless required)."""

from __future__ import annotations

from ..status import Status
from .base import R, Unavailable, check, fmt_age, ltr, num

PROGRESS_LIMIT = ("עדכון קובץ ה-progress של ffmpeg ב-Pi מעיד שהמקודד מייצר פלט (ובמצב listener של SRT - שמחובר caller). "
                  "זו אינה הוכחה שהמחשב מציג תמונה חיה; זה נבדק בנפרד לפי פריימים שפוענחו במחשב. "
                  "ה-latency של SRT הוא הגדרת חוצץ, לא השהיה שנמדדה.")


@check("video.stream", "video", "זרם וידאו חי")
def video_stream(ctx, cfg):
    required = ctx.profile.video_required
    bad = Status.FAIL if required else Status.WARN
    o = ctx.snap.get("video.state")
    v = o.value if o and isinstance(o.value, dict) else {}
    src = ctx.src(f"וידאו · {v.get('backend', 'PyAV/FFmpeg')} · {v.get('url_redacted', 'RTSP')}")
    state = v.get("state", "stopped")
    if state in ("stopped", None):
        if required:
            return R(Status.UNKNOWN, "הפרופיל דורש וידאו, אך הווידאו לא הופעל.", expected="וידאו חי",
                     source=src, missing=("video",))
        return R(Status.NOT_APPLICABLE, "הווידאו לא הופעל (אינו נדרש בפרופיל).", source=src)
    last = num(v.get("last_frame_mono"))
    frame_age = None if last is None else max(0.0, ctx.snap.now_mono - last)
    stale_after = num(v.get("stale_after_s")) or 2.0
    fps = num(v.get("fps"))
    observed = f"מצב {state}"
    if fps is not None:
        observed += f" · {fps:.1f} FPS"
    if v.get("width"):
        observed += f" · {v['width']}x{v['height']}"
    observed += f" · פריים אחרון לפני {fmt_age(frame_age)}"
    if v.get("reconnects"):
        observed += f" · התחברויות מחדש {v['reconnects']}"
    ev = {k: v.get(k) for k in ("state", "fps", "width", "height", "codec", "reconnects", "error", "frames")}
    lim = "השהיית הווידאו (latency) אינה נמדדת - אין חותמת זמן משותפת בין ה-Pi למחשב."
    if state == "failed":
        return R(bad, f"הווידאו נכשל: {v.get('error') or 'לא ידוע'}", observed=observed, expected="וידאו חי",
                 source=src, age_s=frame_age, limitations=lim, evidence=ev)
    if frame_age is None or frame_age > stale_after:
        return R(bad, "אין פריימים חדשים - התמונה אינה חיה.", observed=observed,
                 expected=f"פריים אחרון ≤ {stale_after:g}s", source=src, age_s=frame_age, limitations=lim, evidence=ev)
    min_fps = num(cfg.get("min_fps"))
    if min_fps is not None and (fps is None or fps < min_fps):
        return R(bad, "קצב הפריימים נמוך מהמינימום שהוגדר.", observed=observed, expected=f"≥ {min_fps:g} FPS",
                 source=src, age_s=frame_age, limitations=lim, evidence=ev)
    return R(Status.PASS, "מתקבלים פריימים חיים.", observed=observed,
             expected=f"פריים אחרון ≤ {stale_after:g}s" + (f", ≥ {min_fps:g} FPS" if min_fps else ""),
             source=src, age_s=frame_age, limitations=lim, evidence=ev)


@check("video.pi_service", "video", "שירות הווידאו ב-Pi (x500-video) ומקודד")
def video_pi_service(ctx, cfg):
    required = ctx.profile.video_required
    want = ctx.drone.video_active or required
    src = ctx.src("Pi collector · systemctl show x500-video@* + /run/x500-video-*.progress")
    try:
        data, age = ctx.require_section("video")
    except Unavailable as u:
        if not want:
            return R(Status.NOT_APPLICABLE, "הווידאו לא הופעל ואינו נדרש.", source=src)
        raise u
    active = data.get("active_profile")
    units = data.get("units") or {}
    prog = data.get("progress") or {}
    parsed = prog.get("parsed") or {}
    rate = num(prog.get("frame_rate_between_polls"))
    port = (data.get("config") or {}).get("PORT")
    listening = data.get("udp_listening")
    parts = []
    if active:
        u = units.get(active) or {}
        parts.append(f"שירות {ltr(active)}: {ltr(u.get('ActiveState'))}/{ltr(u.get('SubState'))}"
                     + (f" restarts={ltr(u.get('NRestarts'))}" if u.get("NRestarts") not in (None, "", "0") else ""))
    else:
        parts.append("שירות: לא פעיל")
    if prog:
        if prog.get("exists") is False:
            parts.append("progress: אין קובץ (ממתין ל-caller?)")
        else:
            parts.append(f"progress עודכן לפני {ltr(fmt_age(num(prog.get('mtime_age_s'))))}"
                         + (f" · frame={ltr(parsed.get('frame'))}" if parsed.get("frame") is not None else "")
                         + (f" · קצב מקודד בין דגימות {ltr(f'{rate:.1f} fps')}" if rate is not None else ""))
    if port:
        parts.append(f"UDP {ltr(port)}: " + ("מאזין" if listening else ("לא מאזין" if listening == [] else "לא ידוע")))
    observed = " · ".join(parts)
    ev = {k: data.get(k) for k in ("active_profile", "units", "progress", "config", "udp_listening")}
    max_prog_age = num(cfg.get("progress_max_age_s")) or 3.0
    if not active:
        if required:
            return R(Status.FAIL, "הפרופיל דורש וידאו, ושירות הווידאו ב-Pi אינו פעיל.", observed=observed,
                     expected="x500-video@<profile> פעיל", source=src, age_s=age, evidence=ev, limitations=PROGRESS_LIMIT)
        if ctx.drone.video_active:
            return R(Status.WARN, "הווידאו הופעל באפליקציה, אבל שירות הווידאו ב-Pi אינו פעיל.", observed=observed,
                     expected="x500-video@<profile> פעיל", source=src, age_s=age, evidence=ev, limitations=PROGRESS_LIMIT)
        return R(Status.NOT_APPLICABLE, "שירות הווידאו ב-Pi כבוי (אינו נדרש).", observed=observed, source=src,
                 age_s=age, evidence=ev)
    bad = Status.FAIL if required else Status.WARN
    if active != ctx.drone.srt_profile and ctx.drone.video_active:
        observed += f" · פרופיל מוגדר באפליקציה: {ltr(ctx.drone.srt_profile)}"
    pa = num(prog.get("mtime_age_s"))
    if pa is None or pa > max_prog_age or parsed.get("progress") == "end":
        return R(bad if ctx.drone.video_active or required else Status.WARN,
                 "השירות פעיל אבל המקודד לא מתקדם (אין caller מחובר, או שהצינור נתקע).", observed=observed,
                 expected=f"progress עודכן ב-{max_prog_age:g}s האחרונות", source=src, age_s=age, evidence=ev,
                 limitations=PROGRESS_LIMIT)
    if rate is not None and rate <= 0:
        return R(bad, "קובץ ה-progress מתעדכן אבל מספר הפריימים לא עלה בין דגימות.", observed=observed,
                 expected="frame עולה", source=src, age_s=age, evidence=ev, limitations=PROGRESS_LIMIT)
    return R(Status.PASS, "שירות הווידאו פעיל והמקודד ב-Pi מתקדם.", observed=observed,
             expected=f"שירות פעיל + progress ≤ {max_prog_age:g}s", source=src, age_s=age, evidence=ev,
             limitations=PROGRESS_LIMIT)
