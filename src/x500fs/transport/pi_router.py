"""Explicit restart of mavlink-router on the Pi - only when systemd reports it is down.

While mavlink-router runs, the app never touches it: it carries Mission Planner's link
and restarting it would cut that link. When it has failed or stopped there is no link to
cut, so the user may ask the app to start it again. Only these privileged commands ever
run, with fixed arguments::

    sudo -n /usr/bin/systemctl reset-failed mavlink-router.service   (only when failed)
    sudo -n /usr/bin/systemctl start mavlink-router.service

The state is re-read (``systemctl show``, no sudo) right before sending anything. With the
router down there is no HEARTBEAT, so the disarmed state cannot be verified; the down
state is the gate instead. ``sudo -n`` never prompts (see install_video_sudoers.sh --router).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .pi_video import PASSWORD_HINTS, SHOW_PROPS, SYSTEMCTL, parse_show

UNIT = "mavlink-router.service"
DOWN_STATES = ("failed", "inactive")


@dataclass
class RouterResult:
    ok: bool
    message: str
    needs_password: bool = False
    commands: tuple[str, ...] = ()
    state_before: dict | None = None
    state_after: dict | None = None


def is_down(st: dict | None) -> bool:
    return bool(st) and "error" not in st and st.get("ActiveState") in DOWN_STATES


class RouterControl:
    def __init__(self, session, timeout_s: float = 15.0):
        self.session = session
        self.timeout_s = timeout_s
        self.log: list[str] = []

    def _exec(self, cmd: str):
        self.log.append(cmd)
        return self.session.exec(cmd, timeout=self.timeout_s)

    def state(self) -> dict:
        res = self._exec(f"{SYSTEMCTL} show {UNIT} --property={SHOW_PROPS} --no-pager")
        if res.exit_code != 0:
            return {"error": (res.stderr.decode("utf-8", "replace").strip() or f"exit {res.exit_code}")[:200]}
        return parse_show(res.stdout.decode("utf-8", "replace"))

    def _sudo(self, action: str):
        cmd = f"sudo -n {SYSTEMCTL} {action} {UNIT}"
        res = self._exec(cmd)
        err = res.stderr.decode("utf-8", "replace").strip()
        return cmd, res.exit_code, err, any(h in err.lower() for h in PASSWORD_HINTS)

    def start_if_down(self, wait_s: float = 6.0) -> RouterResult:
        before = self.state()
        if not is_down(before):
            why = before.get("error") or f"{before.get('ActiveState')}/{before.get('SubState')}"
            return RouterResult(False, f"mavlink-router אינו במצב נפול ({why}) - לא נשלחה פקודה.",
                                state_before=before)
        cmds = []
        if before.get("ActiveState") == "failed":
            cmd, rc, err, pw = self._sudo("reset-failed")  # clears a start-limit hit after repeated crashes
            cmds.append(cmd)
            if rc != 0 and pw:
                return RouterResult(False, err[:300], needs_password=True, commands=tuple(cmds), state_before=before)
        cmd, rc, err, pw = self._sudo("start")
        cmds.append(cmd)
        if rc != 0:
            return RouterResult(False, err[:300] or f"exit {rc}", needs_password=pw, commands=tuple(cmds),
                                state_before=before)
        after = before
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            after = self.state()
            if after.get("ActiveState") == "active":
                return RouterResult(True, "mavlink-router הופעל מחדש.", commands=tuple(cmds),
                                    state_before=before, state_after=after)
            time.sleep(0.5)
        return RouterResult(False, f"mavlink-router לא הגיע למצב פעיל ({after.get('ActiveState')}/"
                            f"{after.get('SubState')}). בדוק: journalctl -u mavlink-router",
                            commands=tuple(cmds), state_before=before, state_after=after)


def password_help() -> str:
    return ("ה-Pi דורש סיסמה כדי להפעיל את mavlink-router (sudo), והאפליקציה לא מקלידה סיסמאות. אפשרויות: "
            "(1) ב-Pi: sudo systemctl start mavlink-router. "
            "(2) להתקין הרשאה מצומצמת: sudo bash install_video_sudoers.sh <user> --router --apply "
            "(מתירה רק reset-failed/start של mavlink-router, בנוסף לשירות הווידאו).")
