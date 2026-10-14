"""Explicit control of the existing Pi video service (x500-video@<profile>).

Only two privileged commands ever run, and only for the three existing
profiles::

    sudo -n /usr/bin/systemctl start x500-video@<high|normal|degraded>.service
    sudo -n /usr/bin/systemctl stop  x500-video@<high|normal|degraded>.service

``sudo -n`` never prompts: when the Pi requires a password the command fails
and the app explains the options (start it on the Pi by hand, or install the
narrowly scoped sudoers rule from ``pi/deploy/install_video_sudoers.sh``).
Reading the unit state uses ``systemctl show`` without sudo. Nothing here
touches mavlink-router, the black box, the watchdog or any other service.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

PROFILES = ("high", "normal", "degraded")
UNIT_RE = re.compile(r"^x500-video@(high|normal|degraded)\.service$")
SYSTEMCTL = "/usr/bin/systemctl"
SHOW_PROPS = "ActiveState,SubState,MainPID,NRestarts,Result,ExecMainStatus"
PASSWORD_HINTS = ("a password is required", "a terminal is required", "no tty present", "askpass")


class NotAllowed(ValueError):
    pass


def unit_for(profile: str, template: str = "x500-video@{profile}.service") -> str:
    """Unit name for a profile, validated against the strict allowlist."""
    if profile not in PROFILES:
        raise NotAllowed(f"unknown video profile: {profile!r}")
    unit = template.format(profile=profile)
    if not UNIT_RE.match(unit):
        raise NotAllowed(f"unit {unit!r} is not in the allowlist (x500-video@high|normal|degraded.service)")
    return unit


def control_command(action: str, profile: str, template: str = "x500-video@{profile}.service") -> str:
    if action not in ("start", "stop"):
        raise NotAllowed(f"action {action!r} is not allowed")
    return f"sudo -n {SYSTEMCTL} {action} {shlex.quote(unit_for(profile, template))}"


@dataclass
class ControlResult:
    ok: bool
    action: str
    profile: str
    command: str
    needs_password: bool = False
    exit_code: int | None = None
    message: str = ""
    details: dict = field(default_factory=dict)


def parse_show(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class PiVideoControl:
    """Runs the allowlisted commands over an established, host-key-verified SSH session."""

    def __init__(self, session, template: str = "x500-video@{profile}.service", timeout_s: float = 15.0):
        self.session = session
        self.template = template
        self.timeout_s = timeout_s
        self.log: list[str] = []

    def _exec(self, cmd: str):
        self.log.append(cmd)
        return self.session.exec(cmd, timeout=self.timeout_s)

    def state(self, profile: str) -> dict:
        unit = unit_for(profile, self.template)
        res = self._exec(f"{SYSTEMCTL} show {shlex.quote(unit)} --property={SHOW_PROPS} --no-pager")
        if res.exit_code != 0:
            return {"error": (res.stderr.decode("utf-8", "replace").strip() or f"exit {res.exit_code}")[:200]}
        return parse_show(res.stdout.decode("utf-8", "replace"))

    def states(self) -> dict[str, dict]:
        return {p: self.state(p) for p in PROFILES}

    @staticmethod
    def is_running(st: dict) -> bool:
        return st.get("ActiveState") in ("active", "activating", "reloading")

    def _control(self, action: str, profile: str) -> ControlResult:
        cmd = control_command(action, profile, self.template)
        res = self._exec(cmd)
        err = res.stderr.decode("utf-8", "replace").strip()
        if res.exit_code == 0:
            return ControlResult(True, action, profile, cmd, exit_code=0)
        needs_pw = any(h in err.lower() for h in PASSWORD_HINTS)
        return ControlResult(False, action, profile, cmd, needs_password=needs_pw, exit_code=res.exit_code,
                             message=err[:300] or f"exit {res.exit_code}")

    def start(self, profile: str) -> ControlResult:
        return self._control("start", profile)

    def stop(self, profile: str) -> ControlResult:
        return self._control("stop", profile)


def password_help(profile: str) -> str:
    return ("ה-Pi דורש סיסמה כדי להפעיל או לעצור את שירות הווידאו (sudo), והאפליקציה לא מקלידה סיסמאות. אפשרויות: "
            f"(1) להפעיל ידנית ב-Pi: x500-video {profile}  ואז ללחוץ שוב 'הפעל וידאו'. "
            "(2) להתקין הרשאה מצומצמת שמתירה רק start/stop של x500-video@high|normal|degraded: "
            "pi/deploy/install_video_sudoers.sh (ראו docs/video.md). "
            "(3) לבטל בהגדרות את 'הפעלה/עצירה של שירות הווידאו ב-Pi' ולהפעיל את השירות בעצמך.")
