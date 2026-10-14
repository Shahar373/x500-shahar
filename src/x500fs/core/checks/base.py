"""Check registry, evaluation context and helpers.

A check function receives ``(ctx, cfg)`` and returns an :class:`R`. When
required evidence is missing or stale it raises :class:`Unavailable` (via the
``ctx.require_*`` helpers) which becomes an UNKNOWN result. Any unexpected
exception (e.g. malformed data of the wrong type) also becomes UNKNOWN - a
check can never PASS by accident.
"""

from __future__ import annotations

import time

import math
from dataclasses import dataclass, field
from typing import Any, Callable

from ..observations import Snapshot
from ..profile import Profile
from ..session import SessionLog
from ..status import CheckResult, Status

CATEGORIES = {
    "pi": "Raspberry Pi",
    "link": "תקשורת",
    "fc": "Pixhawk וניווט",
    "battery": "סוללה ותצורה",
    "video": "וידאו",
}
CATEGORY_ORDER = list(CATEGORIES)


@dataclass
class DroneConfig:
    """Installation facts for this drone (from settings, not policy)."""

    name: str = "X500"
    expected_hostname: str = ""
    link_path: str = "wifi"            # wifi | lte | tailscale
    services: dict[str, str] = field(default_factory=dict)  # role -> unit name
    blackbox_globs: list[str] = field(default_factory=list)
    mav_source_sysid: int = 247
    mav_source_compid: int = 25
    mav_target_sysid: int = 0          # 0 = auto-detect
    video_active: bool = False
    pinned_hostkey: str | None = None  # fingerprint actually verified this session
    watchdog_expected_runtime: str = ""  # e.g. "15s" (systemd RuntimeWatchdogUSec text)
    blackbox_sleep_s: float = 10.0
    expected_fc_byid: str = ""
    video_backend: str = "srt"
    srt_profile: str = "degraded"
    ping_target_configured: bool = False
    zte_gateway_configured: bool = False
    ground_ip_configured: bool = False
    evaluator: str = "desktop"         # desktop (the app) | pi_local (x500-preflight on the Pi)
    zte_gateway: str = ""              # configured values, verified against what the Pi observes
    ground_ip: str = ""
    lte_interface: str = "eth1"


# Checks that measure the desktop app's own link/view. On the Pi they cannot be
# observed, so x500-preflight reports them as not applicable (and says so).
DESKTOP_ONLY = {"pi.reachable", "link.path", "link.rtt", "link.interruptions", "video.stream"}


@dataclass
class R:
    status: Status
    explanation: str
    observed: str | None = None
    expected: str | None = None
    source: str = ""
    age_s: float | None = None
    configured: bool = True
    limitations: str | None = None
    missing: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)


class Unavailable(Exception):
    def __init__(self, result: R):
        super().__init__(result.explanation)
        self.result = result


@dataclass(frozen=True)
class CheckDef:
    id: str
    category: str
    title: str
    fn: Callable[["Ctx", dict], R]


REGISTRY: list[CheckDef] = []


def check(check_id: str, category: str, title: str):
    def deco(fn):
        if any(d.id == check_id for d in REGISTRY):
            raise ValueError(f"duplicate check id {check_id}")
        REGISTRY.append(CheckDef(check_id, category, title, fn))
        return fn
    return deco


def ltr(text: Any) -> str:
    """Isolate a left-to-right token (identifier, number, IP) inside RTL text."""
    return f"⁦{text}⁩"


def fmt_age(age: float | None) -> str:
    if age is None:
        return "—"
    if age < 10:
        return f"{age:.1f}s"
    if age < 120:
        return f"{age:.0f}s"
    if age < 7200:
        return f"{age / 60:.0f}m"
    return f"{age / 3600:.1f}h"


class Ctx:
    def __init__(
        self,
        snap: Snapshot,
        profile: Profile,
        drone: DroneConfig,
        session: SessionLog | None = None,
    ):
        self.snap = snap
        self.profile = profile
        self.drone = drone
        self.session = session or SessionLog()
        self.sim_prefix = "SIM · " if snap.simulated else ""

    # -- sources -------------------------------------------------------------
    def src(self, text: str) -> str:
        return self.sim_prefix + text

    # -- MAVLink ---------------------------------------------------------------
    def mav_link(self) -> dict | None:
        o = self.snap.get("mav.link")
        return o.value if o else None

    def mav_endpoint(self) -> str:
        link = self.mav_link() or {}
        return link.get("endpoint") or "MAVLink"

    def msg(self, name: str) -> tuple[dict, float] | None:
        o = self.snap.get(f"mav.msg.{name}")
        if o is None or not isinstance(o.value, dict):
            return None
        return o.value, o.age(self.snap.now_mono)

    def unsupported(self, name: str) -> str | None:
        o = self.snap.get(f"mav.unsupported.{name}")
        return None if o is None else str(o.value)

    def require_msg(self, name: str, max_age: float | None) -> tuple[dict, float]:
        src = self.src(f"MAVLink {name} · {self.mav_endpoint()}")
        got = self.msg(name)
        if got is None:
            why = self.unsupported(name)
            if why:
                raise Unavailable(R(
                    Status.UNKNOWN,
                    f"בקר הטיסה דחה את הבקשה ל-{ltr(name)} ({ltr(why)}). אין נתון - לא ניתן לאשר.",
                    source=src, missing=(name,),
                ))
            link = self.mav_link()
            if not link or not link.get("connected"):
                expl = f"אין חיבור MAVLink, לכן לא התקבלה הודעת {ltr(name)}."
            else:
                expl = (f"הודעת {ltr(name)} לא התקבלה. ייתכן שקצב השידור שלה 0 "
                        f"ושאין בקשות פעילות. לא ניתן לאשר.")
            raise Unavailable(R(Status.UNKNOWN, expl, source=src, missing=(name,)))
        fields, age = got
        if max_age is not None and age > max_age:
            raise Unavailable(R(
                Status.UNKNOWN,
                f"הנתון מ-{ltr(name)} ישן ({fmt_age(age)}, מותר עד {max_age:g}s). נתון ישן אינו נחשב תקין.",
                source=src, age_s=age, missing=(f"{name} (stale)",),
            ))
        return fields, age

    def param(self, name: str) -> tuple[float | int, float] | None:
        o = self.snap.get(f"mav.param.{name}")
        if o is None:
            return None
        v = o.value.get("value") if isinstance(o.value, dict) else None
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            return None
        return v, o.age(self.snap.now_mono)

    def param_missing(self, name: str) -> bool:
        o = self.snap.get(f"mav.param_missing.{name}")
        return o is not None

    def resolve_threshold(self, value: Any) -> tuple[float | None, str]:
        """Resolve a profile threshold. Returns (number or None, description)."""
        if value is None:
            return None, "לא מוגדר"
        if isinstance(value, str) and value.startswith("fc:"):
            pname = value[3:]
            got = self.param(pname)
            if got is None:
                return None, f"{ltr(pname)} (לא נקרא מבקר הטיסה)"
            return float(got[0]), f"{ltr(pname)}={ltr(got[0])}"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value), ltr(value)
        return None, f"ערך לא תקין {ltr(value)}"

    # -- Pi collector ----------------------------------------------------------
    def pi(self) -> tuple[dict, float] | None:
        o = self.snap.get("pi.collector")
        if o is None or not isinstance(o.value, dict):
            return None
        return o.value, o.age(self.snap.now_mono)

    def _interval(self, key: str, default: float) -> float:
        o = self.snap.get(key)
        return float(o.value) if o and isinstance(o.value, (int, float)) and not isinstance(o.value, bool) else default

    def pi_max_age(self, cadence: str = "fast") -> float:
        if cadence == "slow":
            return max(180.0, self._interval("pi.slow_poll_interval_s", 60.0) * 3.5)
        return max(30.0, self._interval("pi.poll_interval_s", 10.0) * 3.5)

    def require_section(self, name: str) -> tuple[Any, float]:
        src = self.src(f"Pi collector · {name}")
        got = self.pi()
        if got is None:
            err = self.snap.get("pi.collector_error")
            why = f" ({err.value})" if err else ""
            raise Unavailable(R(
                Status.UNKNOWN,
                f"אין נתונים מה-Pi{why}. לא ניתן לאשר.",
                source=src, missing=("pi.collector",),
            ))
        doc, age = got
        sections = doc.get("sections")
        if not isinstance(sections, dict) or name not in sections:
            raise Unavailable(R(
                Status.UNKNOWN,
                f"הסעיף {ltr(name)} עוד לא התקבל מה-collector (נאסף במחזור האיטי, או גרסה לא תואמת).",
                source=src, age_s=age, missing=(f"collector.{name}",),
            ))
        sec = sections[name]
        if isinstance(sec, dict) and isinstance(sec.get("_received_mono"), (int, float)):
            # Each section carries its own receive time (fast and slow sections differ).
            age = max(0.0, self.snap.now_mono - float(sec["_received_mono"]))
        cadence = sec.get("_cadence", "fast") if isinstance(sec, dict) else "fast"
        if age > self.pi_max_age(cadence):
            raise Unavailable(R(
                Status.UNKNOWN,
                f"נתוני ה-Pi ({ltr(name)}) ישנים ({fmt_age(age)}). נתון ישן אינו נחשב תקין.",
                source=src, age_s=age, missing=(f"collector.{name} (stale)",),
            ))
        if not isinstance(sec, dict) or not sec.get("ok"):
            err = sec.get("error") if isinstance(sec, dict) else "malformed"
            raise Unavailable(R(
                Status.UNKNOWN,
                f"ה-collector לא הצליח לאסוף {ltr(name)}: {ltr(err)}",
                source=src, age_s=age, missing=(f"collector.{name}",),
                evidence={"error": err},
            ))
        return sec.get("data"), age


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


num = _num


def evaluate(ctx: Ctx) -> list[CheckResult]:
    # Import side-effect registers all checks.
    from . import catalog  # noqa: F401

    results: list[CheckResult] = []
    wall = ctx.snap.now_wall
    evaluated_at = _iso(wall)
    for d in REGISTRY:
        cfg = ctx.profile.check(d.id)
        enabled = cfg.get("enabled", True)
        critical = bool(cfg.get("critical", False))
        if not enabled:
            results.append(CheckResult(
                check_id=d.id, category=d.category, title=d.title,
                status=Status.NOT_APPLICABLE, critical=False,
                explanation="הבדיקה מושבתת בפרופיל הנוכחי.",
                source="profile", configured=True,
            ))
            continue
        if ctx.drone.evaluator == "pi_local" and d.id in DESKTOP_ONLY:
            results.append(CheckResult(
                check_id=d.id, category=d.category, title=d.title,
                status=Status.NOT_APPLICABLE, critical=False,
                explanation="Measured only by the desktop app (its own SSH/MAVLink/video link); "
                            "not observable from the Pi. / נמדד רק מהאפליקציה במחשב.",
                source="x500-preflight (Pi)", configured=True,
            ))
            continue
        try:
            r = d.fn(ctx, cfg)
        except Unavailable as u:
            r = u.result
        except Exception as e:  # malformed data must never look like PASS
            r = R(
                Status.UNKNOWN,
                f"שגיאה בעיבוד הנתונים ({ltr(type(e).__name__)}: {ltr(e)}). הנתון אינו תקין - לא ניתן לאשר.",
                source=ctx.src("evaluator"),
                missing=("valid data",),
            )
        if r.status is Status.PASS and not r.configured:
            r.status = Status.UNKNOWN
        if cfg.get("_missing_from_profile") and r.status is Status.PASS:
            r.explanation += " (הבדיקה לא מוגדרת בפרופיל.)"
        results.append(CheckResult(
            check_id=d.id, category=d.category, title=d.title,
            status=r.status, critical=critical, explanation=r.explanation,
            observed=r.observed, expected=r.expected, source=r.source,
            age_s=r.age_s, configured=r.configured, limitations=r.limitations,
            missing=tuple(r.missing), evidence=_jsonable(r.evidence),
            # Ages come from monotonic receive times on this computer; the timestamp is derived
            # from them, so a different clock on the Pi cannot skew it.
            observed_at=None if r.age_s is None else _iso(wall - r.age_s), evaluated_at=evaluated_at,
        ))
    return results


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t)) + f".{int((t % 1) * 1000):03d}"


def _jsonable(obj: Any, depth: int = 0) -> Any:
    if depth > 6:
        return str(obj)
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else str(obj)
    if isinstance(obj, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v, depth + 1) for v in obj]
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj).hex()
    return str(obj)
