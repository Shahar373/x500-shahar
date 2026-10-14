"""Main window (Hebrew, right-to-left)."""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QSplitter, QTabWidget, QToolBar,
    QToolButton, QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__
from ..controller import Controller
from ..settings import SettingsStore
from .bridge import UiBridge
from .checks_view import ChecksView
from .dialogs import HostKeyDialog, SettingsDialog
from .panels import ManualPanel, MessagesPanel, TelemetryPanel, scrollable, severity_name
from .style import OVERALL_COLORS, OVERALL_HE
from .video_view import VideoPanel

LINK_HE = {"wifi": "Wi-Fi", "tailscale": "Tailscale", "lte": "LTE"}


def _card(title: str) -> tuple[QFrame, QLabel, QLabel]:
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(12, 8, 12, 8)
    t = QLabel(title)
    t.setObjectName("cardTitle")
    v = QLabel("—")
    v.setObjectName("cardValue")
    v.setWordWrap(True)
    sub = QLabel("")
    sub.setObjectName("cardTitle")
    sub.setWordWrap(True)
    lay.addWidget(t)
    lay.addWidget(v)
    lay.addWidget(sub)
    return f, v, sub


class MainWindow(QMainWindow):
    def __init__(self, store: SettingsStore | None = None, icon: QIcon | None = None):
        super().__init__()
        self.bridge = UiBridge()
        self.ctl = Controller(store or SettingsStore(), self.bridge)
        if icon:
            self.setWindowIcon(icon)
        self.resize(1400, 860)
        self._build()
        self.bridge.connection_changed.connect(self._on_connection_changed)
        self.bridge.statustext.connect(self._on_statustext)
        self.bridge.hostkey_unknown.connect(self._on_hostkey)
        self.bridge.video_frame.connect(self._on_frame)
        self.bridge.video_status.connect(self.video.set_status)
        self.bridge.notice.connect(self._on_notice)
        self._seen_events = 0
        self._events_session = None
        self._last_results = []
        self._last_overall = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self._apply_mode_chrome()
        self.refresh()

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        tb = QToolBar("פעולות")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(Qt.TopToolBarArea, tb)
        self.act_connect = QAction("התחבר", self)
        self.act_connect.setShortcut(QKeySequence("Ctrl+K"))
        self.act_connect.triggered.connect(self._toggle_connect)
        self.act_preflight = QAction("הרץ בדיקה", self)
        self.act_preflight.setShortcut(QKeySequence("F5"))
        self.act_preflight.triggered.connect(self._run_preflight)
        self.act_video = QAction("הפעל וידאו", self)
        self.act_video.triggered.connect(self._toggle_video)
        self.act_report = QAction("ייצא דוח", self)
        self.act_report.setShortcut(QKeySequence("Ctrl+S"))
        self.act_report.triggered.connect(self._export_report)
        self.act_settings = QAction("הגדרות", self)
        self.act_settings.triggered.connect(self._open_settings)
        for a in (self.act_connect, self.act_preflight, self.act_video, self.act_report, self.act_settings):
            tb.addAction(a)
        btn = tb.widgetForAction(self.act_connect)
        if isinstance(btn, QToolButton):
            btn.setObjectName("primary")
        tb.addSeparator()
        tb.addWidget(QLabel(" פרופיל: "))
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(160)
        self._fill_profiles()
        self.profile_combo.currentIndexChanged.connect(self._profile_changed)
        tb.addWidget(self.profile_combo)
        tb.addSeparator()
        self.act_diag = QAction("ייצוא אבחון", self)
        self.act_diag.setToolTip("פעולה מפורשת: אוסף מה-Pi מידע לאבחון (קריאה בלבד, לא נכתב דבר ב-Pi), "
                                 "מסתיר סודות ופרטים פרטיים ושומר קובץ במחשב.")
        self.act_diag.triggered.connect(self._export_diagnostics)
        tb.addAction(self.act_diag)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 0, 8, 8)
        self.sim_banner = QLabel("מצב סימולציה - הנתונים מדומים ואינם מגיעים מהרחפן. שום דבר כאן אינו מצב הרחפן האמיתי.")
        self.sim_banner.setObjectName("simBanner")
        self.sim_banner.setAlignment(Qt.AlignCenter)
        self.err_banner = QLabel("")
        self.err_banner.setObjectName("errorBanner")
        self.err_banner.setWordWrap(True)
        self.err_banner.hide()
        root.addWidget(self.sim_banner)
        root.addWidget(self.err_banner)

        cards = QHBoxLayout()
        c1, self.conn_val, self.conn_sub = _card("חיבור")
        c2, self.auto_val, self.auto_sub = _card("בדיקות אוטומטיות")
        c3, self.manual_val, self.manual_sub = _card("רשימה ידנית")
        c4, self.prof_val, self.prof_sub = _card("פרופיל ונתיב")
        for c in (c1, c2, c3, c4):
            cards.addWidget(c, 1)
        cards.setStretch(1, 2)
        root.addLayout(cards)

        split = QSplitter(Qt.Horizontal)
        self.checks = ChecksView()
        split.addWidget(self.checks)
        self.tabs = QTabWidget()
        self.video = VideoPanel()
        self.video.start_requested.connect(self._start_video)
        self.video.stop_requested.connect(self._stop_video)
        self.telemetry = TelemetryPanel()
        self.manual = ManualPanel()
        self.manual.changed.connect(self._manual_changed)
        self.messages = MessagesPanel()
        self.tabs.addTab(self.video, "מצלמה")
        self.tabs.addTab(self.telemetry, "טלמטריה")
        self.tabs.addTab(scrollable(self.manual), "רשימה ידנית")
        self.tabs.addTab(self.messages, "הודעות")
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([900, 480])
        root.addWidget(split, 1)
        disc = QLabel("האפליקציה מבצעת בדיקות וניטור בלבד ואינה שולטת ברחפן. תוצאה 'עברו' אינה מבטיחה טיסה בטוחה. "
                      "Mission Planner אחראי על השליטה בטיסה.")
        disc.setObjectName("disclaimer")
        root.addWidget(disc)
        self.setCentralWidget(central)
        self.statusBar().showMessage(f"{APP_NAME} {__version__}")

    def _fill_profiles(self) -> None:
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        names = {"bench": "ספסל (Bench)", "flight": "טיסה (Flight)"}
        for pid in self.ctl.available_profiles():
            self.profile_combo.addItem(names.get(pid, pid), pid)
        idx = self.profile_combo.findData(self.ctl.settings.profile_id)
        self.profile_combo.setCurrentIndex(max(0, idx))
        self.profile_combo.blockSignals(False)

    def _apply_mode_chrome(self) -> None:
        sim = self.ctl.settings.simulation
        self.sim_banner.setVisible(sim)
        self.setWindowTitle(("[סימולציה] " if sim else "") + f"{APP_NAME} · {self.ctl.settings.drone_name}")
        self.act_connect.setText(("נתק" if self.ctl.connected else ("התחבר לסימולציה" if sim else "התחבר")))
        err = self.ctl.profile_error or self.ctl.settings_store.load_error
        if err:
            self.err_banner.setText("שגיאה בקובץ הגדרות/פרופיל - נעשה שימוש בברירת המחדל המובנית: " + err)
            self.err_banner.show()
        else:
            self.err_banner.hide()

    # ------------------------------------------------------------------ actions
    def _toggle_connect(self) -> None:
        if self.ctl.connected:
            self.ctl.disconnect()
        else:
            if not self.ctl.settings.simulation and not self.ctl.settings.is_configured():
                QMessageBox.information(self, "הגדרות חסרות",
                                        "יש להזין כתובת ושם משתמש של ה-Pi בהגדרות, או להפעיל מצב סימולציה.")
                self._open_settings()
                return
            self.messages.clear()
            self.ctl.connect()

    def _run_preflight(self) -> None:
        self.ctl.run_preflight()
        self.manual.sync(self.ctl.manual.to_list())
        self.statusBar().showMessage("סשן בדיקה חדש התחיל: מרענן נתונים מה-Pi ומבקר הטיסה…", 8000)
        self.refresh()

    def _toggle_video(self) -> None:
        if self.ctl.video_running():
            self._stop_video()
        else:
            self._start_video()

    def _start_video(self) -> None:
        gate = self.ctl.start_video()
        if not gate.allowed:
            QMessageBox.warning(self, "וידאו", gate.reason)
            return
        self.video.set_running(True)
        self.act_video.setText("עצור וידאו")
        self.tabs.setCurrentWidget(self.video)

    def _stop_video(self) -> None:
        self.ctl.stop_video()
        self.video.set_running(False)
        self.act_video.setText("הפעל וידאו")

    def _on_frame(self) -> None:
        self.bridge.frame_consumed()
        w = self.ctl.video_worker()
        if w is not None:
            self.video.show_frame(w.latest())

    def _export_report(self) -> None:
        try:
            jp, hp = self.ctl.export_report()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "ייצוא דוח", f"שמירת הדוח נכשלה: {e}")
            return
        box = QMessageBox(self)
        box.setWindowTitle("הדוח נשמר")
        box.setText(f"הדוח נשמר:\n⁦{hp}⁩\n⁦{jp}⁩")
        open_btn = box.addButton("פתח דוח", QMessageBox.AcceptRole)
        box.addButton("סגור", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(hp)))

    def _export_diagnostics(self) -> None:
        if QMessageBox.question(
                self, "ייצוא אבחון",
                "לאסוף מידע אבחון מה-Pi? הפקודות לקריאה בלבד ולא נכתב דבר ב-Pi. הקובץ נשמר בתיקיית הדוחות במחשב, "
                "בלי סיסמאות, ועם הסתרת כתובות ומספרים סידוריים אם האפשרות פעילה בהגדרות.") != QMessageBox.Yes:
            return
        self.act_diag.setEnabled(False)

        def work():
            try:
                path = self.ctl.export_diagnostics()
                self.bridge.on_notice("info", f"ייצוא האבחון נשמר: \u2066{path}\u2069")
            except Exception as e:  # noqa: BLE001
                self.bridge.on_notice("warning", f"ייצוא האבחון נכשל: {e}")
        t = threading.Thread(target=work, name="diag-export", daemon=True)
        t.start()
        QTimer.singleShot(3000, lambda: self.act_diag.setEnabled(True))

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.ctl.settings, str(self.ctl.settings_store.dir),
                             str(self.ctl.settings_store.profiles_dir), self.ctl.forget_host_key, self)
        if dlg.exec():
            was_connected = self.ctl.connected
            mode_changed = dlg.s.simulation != self.ctl.settings.simulation
            if was_connected and mode_changed:
                self.ctl.disconnect()
            self.ctl.apply_settings(dlg.s)
            self._fill_profiles()
            self._apply_mode_chrome()
            if was_connected and not mode_changed:
                self.statusBar().showMessage("ההגדרות נשמרו. התנתק והתחבר מחדש כדי להחיל שינויי חיבור.", 8000)

    def _profile_changed(self, _idx: int) -> None:
        pid = self.profile_combo.currentData()
        if pid:
            self.ctl.set_profile(pid)
            self._apply_mode_chrome()
            self.refresh()

    def _manual_changed(self, item_id: str, on: bool) -> None:
        self.ctl.manual.set(item_id, on)
        self.manual.sync(self.ctl.manual.to_list())
        self.refresh()

    # ------------------------------------------------------------------ callbacks
    def _on_connection_changed(self) -> None:
        if not self.ctl.connected:
            self.video.set_running(False)
            self.act_video.setText("הפעל וידאו")
        self._apply_mode_chrome()
        self.refresh()

    def _on_statustext(self, item: dict) -> None:
        self.messages.add(item.get("t_wall", time.time()), "בקר טיסה", severity_name(item), item.get("text", ""),
                          ltr_text=True)

    def _on_notice(self, level: str, text: str) -> None:
        if level == "error":
            QMessageBox.critical(self, "שגיאה", text)
        else:
            self.statusBar().showMessage(text, 10000)

    def _on_hostkey(self, info: dict) -> None:
        dlg = HostKeyDialog(info, self)
        if dlg.exec():
            self.ctl.trust_pending_host_key()
        else:
            self.ctl.reject_pending_host_key()

    # ------------------------------------------------------------------ periodic refresh
    def refresh(self) -> None:
        ctl = self.ctl
        results, overall, snap = ctl.evaluate()
        self._last_results, self._last_overall = results, overall
        self.checks.update_results(results)
        # connection card
        sim = ctl.simulation
        if not ctl.connected:
            self.conn_val.setText("לא מחובר")
            self.conn_val.setStyleSheet("color:#5f6b7a")
            self.conn_sub.setText(ctl.settings.drone_name)
        else:
            ssh = snap.get("pi.ssh")
            link = snap.get("mav.link")
            ssh_state = ssh.value.get("state") if ssh else "?"
            mav_state = link.value.get("state") if link else "?"
            live = mav_state == "live" and ssh_state == "connected"
            self.conn_val.setText(("סימולציה · " if sim else "") + ("מחובר" if live else "מתחבר / חלקי"))
            self.conn_val.setStyleSheet("color:%s" % ("#b26a00" if sim else ("#1b7f3b" if live else "#b26a00")))
            host = ssh.value.get("host") if ssh else ctl.settings.connection.host
            self.conn_sub.setText(f"{ctl.settings.drone_name} · ⁦{host}⁩ · SSH {ssh_state} · MAVLink {mav_state}")
        # overall card
        st = overall.status.value
        self.auto_val.setText(OVERALL_HE[st] if ctl.connected or st != "INCOMPLETE" else "לא מחובר - אין נתונים")
        self.auto_val.setStyleSheet(f"color:{OVERALL_COLORS[st]}")
        c = overall.counts
        sub = f"עבר {c['PASS']} · אזהרה {c['WARN']} · נכשל {c['FAIL']} · לא ידוע {c['UNKNOWN']}"
        if overall.blocking:
            sub += f" · חוסמות: {len(overall.blocking)}"
        prog = ctl.preflight_progress()
        if prog:
            parts = []
            if "pi" in prog:
                parts.append("Pi ✓" if prog["pi"] else "Pi…")
            if prog.get("params_pending"):
                parts.append(f"פרמטרים ממתינים: {prog['params_pending']}")
            sub += " · סשן: " + time.strftime("%H:%M", time.localtime(ctl.preflight_started)) + (" · " + ", ".join(parts) if parts else "")
        self.auto_sub.setText(sub + " · אין בכך הבטחה לטיסה בטוחה")
        # manual card
        done, total = ctl.manual.done_count(), ctl.manual.total()
        self.manual_val.setText("הושלמה" if done == total else f"{done}/{total}")
        self.manual_val.setStyleSheet("color:%s" % ("#1b7f3b" if done == total else "#5f6b7a"))
        self.manual_sub.setText("נפרד מהבדיקות האוטומטיות; מתאפס בכל סשן")
        # profile card
        p = ctl.profile
        self.prof_val.setText(p.name)
        self.prof_sub.setText(f"נתיב: {LINK_HE.get(ctl.settings.connection.link_path, ctl.settings.connection.link_path)}"
                              + (" · וידאו נדרש" if p.video_required else ""))
        # buttons
        self.act_preflight.setEnabled(ctl.connected)
        self.act_video.setEnabled(ctl.connected)
        self.act_connect.setText("נתק" if ctl.connected else ("התחבר לסימולציה" if ctl.settings.simulation else "התחבר"))
        # panels
        video = ctl.video_worker().status() if ctl.video_worker() else None
        self.telemetry.update_snapshot(snap, ctl.session.rtt.summary(), video)
        self.video.update_pipeline(snap, video, ctl.simulation)
        if self._events_session is not ctl.session:
            self._events_session = ctl.session
            self._seen_events = 0
        for ev in ctl.session.events_after(self._seen_events):
            self.messages.add(ev.t_wall, f"אפליקציה/{ev.source}", ev.level, ev.text)
            self._seen_events = ev.seq

    def closeEvent(self, ev) -> None:
        self.timer.stop()
        self.ctl.shutdown()
        super().closeEvent(ev)
