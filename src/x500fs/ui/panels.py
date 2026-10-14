"""Telemetry, manual checklist and messages panels."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QHeaderView, QLabel, QScrollArea, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ..core.checks.base import fmt_age
from ..core.manual import ITEMS
from ..core.mav import constants as C
from ..core.mav import parse as P
from ..core.observations import Snapshot

LRI, PDI = "⁦", "⁩"


def _ltr(v) -> str:
    return f"{LRI}{v}{PDI}"


class TelemetryPanel(QWidget):
    """Live values. Every value shows its age; stale values are greyed out."""

    FIELDS = [
        ("link", "קישור MAVLink"),
        ("hb", "HEARTBEAT אחרון"),
        ("armed", "חימוש"),
        ("mode", "מצב טיסה"),
        ("gps", "GPS"),
        ("ekf", "EKF"),
        ("batt", "סוללה"),
        ("att", "גלגול / עלרוד / כיוון"),
        ("hud", "כיוון / מהירות קרקע / גובה יחסי"),
        ("gcs", "תחנת קרקע בקישור"),
        ("rtt", "RTT לאפליקציה (SSH)"),
        ("pi", "Pi: טמפ' / CPU / RAM"),
        ("pi_age", "נתוני Pi אחרונים"),
        ("video", "וידאו"),
    ]
    STALE_S = 5.0

    def __init__(self, parent=None):
        super().__init__(parent)
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        self._vals: dict[str, QLabel] = {}
        for row, (key, title) in enumerate(self.FIELDS):
            t = QLabel(title)
            t.setStyleSheet("color:#5f6b7a")
            v = QLabel("—")
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            # Values often start with an LTR token; pin them next to their (right-hand) title.
            v.setAlignment(Qt.AlignAbsolute | Qt.AlignRight | Qt.AlignVCenter)
            v.setWordWrap(True)
            v.setStyleSheet("font-weight:600")
            grid.addWidget(t, row, 0)
            grid.addWidget(v, row, 1)
            self._vals[key] = v
        note = QLabel("ערכים ישנים מסומנים באפור. האפליקציה רק מציגה - Mission Planner אחראי על השליטה בטיסה.")
        note.setObjectName("disclaimer")
        note.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.addLayout(grid)
        lay.addStretch(1)
        lay.addWidget(note)

    def _set(self, key: str, text: str, age: float | None = None, stale_s: float | None = None) -> None:
        lbl = self._vals[key]
        stale = age is not None and age > (stale_s or self.STALE_S)
        if age is not None:
            text = f"{text}   ({fmt_age(age)})"
        lbl.setText(text)
        lbl.setStyleSheet("font-weight:600;color:%s" % ("#9aa0a6" if stale else "#1d2330"))

    def update_snapshot(self, snap: Snapshot, rtt: dict, video: dict | None) -> None:
        now = snap.now_mono
        link = snap.get("mav.link")
        if link:
            v = link.value
            self._set("link", f"{v.get('state')} · {_ltr(v.get('endpoint'))}" +
                      (f" · שגיאה: {v.get('error')}" if v.get("error") else ""))
        else:
            self._set("link", "לא מחובר")
        hb = snap.get("mav.msg.HEARTBEAT")
        if hb:
            age = hb.age(now)
            sysid = link.value.get("target_sysid") if link else None
            self._set("hb", f"sysid {_ltr(sysid if sysid is not None else '?')}", age, 3.0)
            armed = P.is_armed(hb.value)
            self._set("armed", "חמוש (ARMED)" if armed else "לא חמוש (DISARMED)", age, 3.0)
            self._set("mode", _ltr(P.copter_mode_name(hb.value) or "?"), age, 3.0)
        else:
            for k in ("hb", "armed", "mode"):
                self._set(k, "—")
        g = snap.get("mav.msg.GPS_RAW_INT")
        if g:
            d = P.gps_raw(g.value)
            self._set("gps", f"{_ltr(d['fix_name'])} · לוויינים {_ltr(d['satellites'] if d['satellites'] is not None else '?')}"
                      f" · HDOP {_ltr(d['hdop'] if d['hdop'] is not None else '?')}", g.age(now))
        else:
            self._set("gps", "—")
        e = snap.get("mav.msg.EKF_STATUS_REPORT")
        if e:
            st = P.ekf_status(e.value) or {}
            flags = [k for k, on in (st.get("flags") or {}).items() if on]
            var = st.get("variances") or {}
            worst = max((x for x in var.values() if x is not None), default=None)
            self._set("ekf", _ltr(", ".join(flags) or "—") + (f" · שונות מקס' {worst:.2f}" if worst is not None else ""),
                      e.age(now))
        else:
            self._set("ekf", "—")
        ss = snap.get("mav.msg.SYS_STATUS")
        if ss:
            b = P.battery_from_sys_status(ss.value)
            parts = [_ltr(f"{b['voltage_v']:.2f}V") if b["voltage_v"] is not None else "מתח לא ידוע",
                     _ltr(f"{b['current_a']:.1f}A") if b["current_a"] is not None else "זרם לא ידוע",
                     _ltr(f"{b['remaining_pct']}%") if b["remaining_pct"] is not None else "קיבולת לא ידועה"]
            self._set("batt", " · ".join(parts), ss.age(now))
        else:
            self._set("batt", "—")
        a = snap.get("mav.msg.ATTITUDE")
        if a:
            import math
            v = a.value
            try:
                self._set("att", _ltr(f"{math.degrees(v['roll']):.1f}° / {math.degrees(v['pitch']):.1f}° / "
                                      f"{(math.degrees(v['yaw']) + 360) % 360:.0f}°"), a.age(now))
            except (KeyError, TypeError):
                self._set("att", "נתון לא תקין")
        else:
            self._set("att", "—")
        hud = snap.get("mav.msg.VFR_HUD")
        gpi = snap.get("mav.msg.GLOBAL_POSITION_INT")
        parts, ages = [], []
        if hud:
            h, gs = hud.value.get("heading"), hud.value.get("groundspeed")
            parts.append(_ltr(f"{h}°") if isinstance(h, int) and 0 <= h <= 360 else "כיוון ?")
            parts.append(_ltr(f"{gs:.1f} m/s") if isinstance(gs, (int, float)) else "מהירות ?")
            ages.append(hud.age(now))
        else:
            parts += ["כיוון —", "מהירות —"]
        ra = gpi.value.get("relative_alt") if gpi else None
        if isinstance(ra, int):
            parts.append(_ltr(f"{ra / 1000:.1f} m"))
            ages.append(gpi.age(now))
        else:
            parts.append("גובה —")
        self._set("hud", " · ".join(parts), max(ages) if ages else None)
        gcs = [o for o in snap.with_prefix("mav.peer.") if P.is_gcs_heartbeat(o.value) and o.age(now) < 5]
        self._set("gcs", ", ".join(_ltr(o.key[9:]) for o in gcs) if gcs else "לא נראתה")
        if rtt.get("count"):
            self._set("rtt", f"{rtt['last_ms']:.0f}ms (חציון {rtt['median_ms']:.0f}ms" +
                      (f", timeouts {rtt['timeouts']}" if rtt.get("timeouts") else "") + ")")
        else:
            self._set("rtt", "—")
        col = snap.get("pi.collector")
        if col:
            try:
                s = col.value["sections"]
                t = (s.get("thermal") or {}).get("data") or {}
                c = (s.get("cpu") or {}).get("data") or {}
                m = (s.get("memory") or {}).get("data") or {}
                temp = t.get("cpu_temp_c")
                self._set("pi", f"{temp:.1f}°C" if temp is not None else "?", None)
                self._vals["pi"].setText(
                    f"{(f'{temp:.1f}°C') if temp is not None else '?'} · CPU {c.get('usage_pct', '?')}% · "
                    f"RAM פנוי {m.get('available_pct', '?')}%")
            except (KeyError, TypeError, AttributeError):
                self._set("pi", "נתון לא תקין")
            self._set("pi_age", "התקבלו", col.age(now), 35)
        else:
            err = snap.get("pi.collector_error")
            self._set("pi", "—")
            self._set("pi_age", f"שגיאה: {err.value}" if err else "—")
        if video and video.get("state") not in (None, "stopped"):
            fps = video.get("fps")
            self._set("video", f"{video.get('state')} · " + (f"{fps:.1f} FPS" if fps else "FPS —"))
        else:
            self._set("video", "כבוי")


class ManualPanel(QWidget):
    changed = Signal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        intro = QLabel("אישורים ידניים - יש לסמן כל פריט רק אחרי בדיקה פיזית. הרשימה מתאפסת בכל סשן בדיקה חדש.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self._boxes: dict[str, QCheckBox] = {}
        self._stamps: dict[str, QLabel] = {}
        for item_id, title, detail in ITEMS:
            frame = QFrame()
            frame.setObjectName("card")
            fl = QVBoxLayout(frame)
            cb = QCheckBox(title)
            cb.setStyleSheet("font-weight:600;font-size:14px")
            cb.toggled.connect(lambda on, i=item_id: self.changed.emit(i, on))
            d = QLabel(detail)
            d.setWordWrap(True)
            d.setStyleSheet("color:#5f6b7a")
            stamp = QLabel("")
            stamp.setStyleSheet("color:#1b7f3b;font-size:11px")
            fl.addWidget(cb)
            fl.addWidget(d)
            fl.addWidget(stamp)
            lay.addWidget(frame)
            self._boxes[item_id] = cb
            self._stamps[item_id] = stamp
        self.summary = QLabel("")
        self.summary.setStyleSheet("font-weight:700")
        lay.addWidget(self.summary)
        lay.addStretch(1)

    def sync(self, items: list[dict]) -> None:
        done = 0
        for it in items:
            cb = self._boxes[it["id"]]
            cb.blockSignals(True)
            cb.setChecked(it["confirmed"])
            cb.blockSignals(False)
            self._stamps[it["id"]].setText(f"אושר ב-{it['confirmed_at']}" if it["confirmed_at"] else "")
            done += 1 if it["confirmed"] else 0
        total = len(items)
        self.summary.setText(f"הושלמו {done}/{total}" + (" - רשימה ידנית הושלמה" if done == total else ""))


def scrollable(widget: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setWidget(widget)
    return sa


class MessagesPanel(QWidget):
    COLS = ["שעה", "מקור", "חומרה", "הודעה"]
    SEV_COLORS = {"EMERGENCY": "#c62828", "ALERT": "#c62828", "CRITICAL": "#c62828", "ERROR": "#c62828",
                  "WARNING": "#b26a00", "error": "#c62828", "warning": "#b26a00"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("הודעות אחרונות מבקר הטיסה (STATUSTEXT) ואירועי האפליקציה. "
                             "היעדר הודעת PreArm אינו הוכחה שהבדיקות עברו."))
        lay.addWidget(self.table)
        self._seen = 0

    def add(self, t_wall: float, source: str, severity: str, text: str, ltr_text: bool = False) -> None:
        self.table.insertRow(0)
        vals = [time.strftime("%H:%M:%S", time.localtime(t_wall)), source, severity,
                _ltr(text) if ltr_text else text]
        color = QColor(self.SEV_COLORS.get(severity, "#1d2330"))
        for c, v in enumerate(vals):
            it = QTableWidgetItem(v)
            it.setForeground(color)
            self.table.setItem(0, c, it)
        if self.table.rowCount() > 500:
            self.table.removeRow(self.table.rowCount() - 1)

    def clear(self) -> None:
        self.table.setRowCount(0)


def severity_name(item: dict) -> str:
    return item.get("severity_name") or C.SEVERITY_NAMES.get(item.get("severity"), "?")
