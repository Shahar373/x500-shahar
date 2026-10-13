"""Video stream check (separate from telemetry; a warning unless required)."""

from __future__ import annotations

from ..status import Status
from .base import R, check, fmt_age, num


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
