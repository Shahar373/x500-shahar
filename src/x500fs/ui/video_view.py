"""Camera panel: live frame display with honest stale/lost indication."""

from __future__ import annotations

import time

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

STATE_HE = {
    "stopped": "כבוי",
    "connecting": "מתחבר…",
    "reconnecting": "מתחבר מחדש…",
    "playing": "חי",
    "failed": "נכשל",
}

# After this long without a frame the old image is removed entirely.
CLEAR_AFTER_S = 6.0


class FrameWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._image: QImage | None = None
        self._frame_mono: float | None = None
        self.state = "stopped"
        self.stale_after_s = 2.0
        self.simulated = False
        self.message = ""

    def set_frame(self, frame) -> None:
        img = QImage(frame.data, frame.width, frame.height, frame.stride, QImage.Format_RGB888)
        self._image = img.copy()  # detach from the worker's buffer
        self._frame_mono = frame.t_mono
        self.simulated = frame.simulated
        self.update()

    def clear(self) -> None:
        self._image = None
        self._frame_mono = None
        self.update()

    def frame_age(self) -> float | None:
        return None if self._frame_mono is None else time.monotonic() - self._frame_mono

    def is_live(self) -> bool:
        age = self.frame_age()
        return self.state == "playing" and age is not None and age <= self.stale_after_s

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor("#111418"))
        age = self.frame_age()
        img = self._image
        if img is not None and age is not None and age > CLEAR_AFTER_S:
            img = None  # never keep showing an old frame
        if img is not None:
            scaled = img.size().scaled(self.size(), Qt.KeepAspectRatio)
            target = QRect(0, 0, scaled.width(), scaled.height())
            target.moveCenter(self.rect().center())
            p.drawImage(target, img)
            if not self.is_live():
                p.fillRect(target, QColor(0, 0, 0, 185))
                self._center_text(p, "תמונה ישנה - אין וידאו חי", f"פריים אחרון לפני {age:.1f} שניות",
                                  QColor("#ff8a80"))
        else:
            title = {"stopped": "הווידאו כבוי", "connecting": "מתחבר לזרם…",
                     "reconnecting": "מתחבר מחדש…", "failed": "הווידאו נכשל"}.get(self.state, "אין תמונה")
            self._center_text(p, title, self.message, QColor("#cfd8dc"))
        if self.simulated and img is not None:
            f = QFont(self.font())
            f.setPointSize(22)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QPen(QColor(255, 179, 0, 220)))
            p.drawText(self.rect().adjusted(10, 10, -10, -10), Qt.AlignTop | Qt.AlignLeft, "SIM · סימולציה")
        p.end()

    def _center_text(self, p: QPainter, title: str, sub: str, color: QColor) -> None:
        f = QFont(self.font())
        f.setPointSize(15)
        f.setBold(True)
        p.setFont(f)
        p.setPen(color)
        r = self.rect()
        p.drawText(QRect(r.x(), r.center().y() - 40, r.width(), 36), Qt.AlignCenter, title)
        if sub:
            f.setPointSize(10)
            f.setBold(False)
            p.setFont(f)
            p.setPen(QColor("#b0bec5"))
            p.drawText(QRect(r.x() + 10, r.center().y(), r.width() - 20, 60), Qt.AlignCenter | Qt.TextWordWrap, sub)


class VideoPanel(QWidget):
    start_requested = Signal()
    stop_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.frame = FrameWidget()
        self.btn = QPushButton("הפעל וידאו")
        self.btn.clicked.connect(self._toggle)
        self.state_lbl = QLabel("מצב: כבוי")
        self.fps_lbl = QLabel("FPS: —")
        self.res_lbl = QLabel("רזולוציה: —")
        self.lat_lbl = QLabel("השהיה: לא נמדדת")
        self.lat_lbl.setToolTip("אין שעון משותף בין ה-Pi למחשב, לכן האפליקציה לא מציגה מספר השהיה.")
        self.rec_lbl = QLabel("")
        info = QHBoxLayout()
        for w in (self.btn, self.state_lbl, self.fps_lbl, self.res_lbl, self.lat_lbl, self.rec_lbl):
            info.addWidget(w)
        info.addStretch(1)
        self.note = QLabel("הפעלה או שינוי הגדרות מצלמה מותרים רק כשהרחפן אינו חמוש. "
                           "תקלה בווידאו היא אזהרה נפרדת, אלא אם הפרופיל דורש וידאו.")
        self.note.setObjectName("disclaimer")
        self.note.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.addLayout(info)
        lay.addWidget(self.frame, 1)
        lay.addWidget(self.note)
        self._running = False
        self._status: dict = {}
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(250)

    def _toggle(self) -> None:
        (self.stop_requested if self._running else self.start_requested).emit()

    def set_running(self, running: bool) -> None:
        self._running = running
        self.btn.setText("עצור וידאו" if running else "הפעל וידאו")
        if not running:
            self.frame.state = "stopped"
            self.frame.message = ""
            self.frame.clear()
            self.fps_lbl.setText("FPS: —")
            self.rec_lbl.setText("")
            self.state_lbl.setText("מצב: כבוי")

    def show_frame(self, frame) -> None:
        if self._running and frame is not None:
            self.frame.set_frame(frame)

    def set_status(self, st: dict) -> None:
        self._status = st
        if not self._running:
            return
        self.frame.state = st.get("state", "stopped")
        self.frame.stale_after_s = float(st.get("stale_after_s") or 2.0)
        self.frame.message = st.get("error") or ""
        if st.get("width"):
            self.res_lbl.setText(f"רזולוציה: ⁦{st['width']}x{st['height']}⁩ ({st.get('codec') or '?'})")
        rc = st.get("reconnects") or 0
        self.rec_lbl.setText(f"התחברויות מחדש: {rc}" if rc else "")
        self._tick()

    def _tick(self) -> None:
        if not self._running:
            return
        fps = self._status.get("fps")
        live = self.frame.is_live()
        state = self.frame.state
        label = STATE_HE.get(state, state)
        if state == "playing" and not live:
            label = "תמונה ישנה (STALE)"
        self.state_lbl.setText(f"מצב: {label}")
        color = "#1b7f3b" if live else ("#c62828" if state in ("failed",) or (state == "playing" and not live) else "#b26a00")
        self.state_lbl.setStyleSheet(f"color:{color};font-weight:600")
        self.fps_lbl.setText(f"FPS: {fps:.1f}" if (fps is not None and live) else "FPS: —")
        self.frame.update()
