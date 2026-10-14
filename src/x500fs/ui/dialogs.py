"""Settings, host-key confirmation and first-run dialogs."""

from __future__ import annotations

import copy
import json

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget,
)

from .. import credentials
from ..settings import SRT_PROFILES, Settings
from ..sim.simulator import SCENARIOS

LINK_PATHS = [("wifi", "Wi-Fi"), ("tailscale", "Tailscale"), ("lte", "LTE (דרך Tailscale)")]
AUTH = [("key", "מפתח SSH (מומלץ)"), ("agent", "SSH agent"), ("password", "סיסמה")]
MAV_TRANSPORTS = [("ssh_tunnel", "מנהרת SSH לפורט של mavlink-router (מומלץ)"),
                  ("tcp", "TCP ישיר"), ("udp_listen", "האזנה ל-UDP")]
VIDEO_TRANSPORTS = [("ssh_tunnel", "RTSP דרך מנהרת SSH"), ("direct", "RTSP ישיר")]
VIDEO_BACKENDS = [("srt", "SRT - הצינור הקיים ב-Pi (x500-video@<פרופיל>) - ברירת מחדל"),
                  ("rtsp", "RTSP / MediaMTX - חלופה")]


def _profile_label(name: str) -> str:
    p = SRT_PROFILES[name]
    return (f"{name}: {p['width']}x{p['height']} @ {p['fps']} FPS, {p['bitrate_kbps']} kbps, "
            f"latency {p['latency_us'] // 1000} ms")


def _combo(options, current):
    cb = QComboBox()
    for key, label in options:
        cb.addItem(label, key)
    idx = max(0, cb.findData(current))
    cb.setCurrentIndex(idx)
    return cb


def _spin(value, lo, hi):
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(int(value))
    return s


def _dspin(value, lo, hi, step=0.5):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setValue(float(value))
    return s


def _ltr_edit(text="", placeholder=""):
    e = QLineEdit(text)
    e.setPlaceholderText(placeholder)
    e.setLayoutDirection(Qt.LeftToRight)
    return e


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, config_dir: str, profiles_dir: str, forget_hostkey=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("הגדרות")
        self.s = copy.deepcopy(settings)
        self._forget_hostkey = forget_hostkey
        tabs = QTabWidget()
        # Every tab scrolls, so the dialog (and its Save button) always fits on small screens.
        for builder, title in ((self._tab_connection, "חיבור"), (self._tab_mavlink, "MAVLink"),
                               (self._tab_video, "וידאו"), (self._tab_pi, "Pi"),
                               (lambda: self._tab_general(config_dir, profiles_dir), "כללי וסימולציה")):
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.NoFrame)
            area.setWidget(builder())
            tabs.addTab(area, title)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText("שמור")
        bb.button(QDialogButtonBox.Cancel).setText("ביטול")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        note = QLabel("שינויים בחיבור נכנסים לתוקף בחיבור הבא. סיסמאות נשמרות רק במנהל האישורים של Windows.")
        note.setObjectName("disclaimer")
        lay.addWidget(note)
        lay.addWidget(bb)
        self._fit_to_screen(760, 640)

    def _fit_to_screen(self, width: int, height: int) -> None:
        screen = (self.parent().screen() if self.parent() else None) or QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            width, height = min(width, int(avail.width() * 0.95)), min(height, int(avail.height() * 0.88))
        self.resize(width, height)

    # --------------------------------------------------------------- tabs
    def _tab_connection(self):
        c = self.s.connection
        w = QWidget()
        f = QFormLayout(w)
        self.drone_name = QLineEdit(self.s.drone_name)
        self.host = _ltr_edit(c.host, "x500pi / 100.x.y.z")
        self.port = _spin(c.ssh_port, 1, 65535)
        self.user = _ltr_edit(c.username, "pi")
        self.auth = _combo(AUTH, c.auth_method)
        self.key_path = _ltr_edit(c.key_path, r"C:\Users\you\.ssh\id_ed25519")
        browse = QPushButton("בחר…")
        browse.clicked.connect(self._browse_key)
        kp = QHBoxLayout()
        kp.addWidget(self.key_path, 1)
        kp.addWidget(browse)
        self.secret_btn = QPushButton("שמור סיסמה / passphrase…")
        self.secret_btn.clicked.connect(self._set_secret)
        self.secret_lbl = QLabel("")
        self.link = _combo(LINK_PATHS, c.link_path)
        self.exp_host = _ltr_edit(c.expected_hostname, "x500pi")
        self.timeout = _dspin(c.connect_timeout_s, 2, 60)
        forget = QPushButton("שכח את מפתח השרת השמור…")
        forget.clicked.connect(self._forget)
        f.addRow("שם הרחפן", self.drone_name)
        f.addRow("כתובת ה-Pi (hostname/IP)", self.host)
        f.addRow("פורט SSH", self.port)
        f.addRow("משתמש SSH", self.user)
        f.addRow("אימות", self.auth)
        f.addRow("קובץ מפתח פרטי", kp)
        f.addRow(self.secret_btn, self.secret_lbl)
        f.addRow("נתיב תקשורת שנבחר", self.link)
        f.addRow("שם מארח צפוי (לאימות זהות)", self.exp_host)
        f.addRow("זמן המתנה לחיבור (שניות)", self.timeout)
        f.addRow(forget)
        self._refresh_secret_label()
        return w

    def _tab_mavlink(self):
        m = self.s.mavlink
        w = QWidget()
        f = QFormLayout(w)
        self.mav_transport = _combo(MAV_TRANSPORTS, m.transport)
        self.tunnel_host = _ltr_edit(m.tunnel_host)
        self.tunnel_port = _spin(m.tunnel_port, 1, 65535)
        self.tcp_host = _ltr_edit(m.tcp_host, "(Pi host)")
        self.tcp_port = _spin(m.tcp_port, 1, 65535)
        self.udp_port = _spin(m.udp_port, 1, 65535)
        self.src_sysid = _spin(m.source_sysid, 1, 254)
        self.src_compid = _spin(m.source_compid, 1, 254)
        self.tgt_sysid = _spin(m.target_sysid, 0, 255)
        self.req = QCheckBox("בקש הודעות ופרמטרים (קריאה בלבד: MAV_CMD_REQUEST_MESSAGE, PARAM_REQUEST_READ)")
        self.req.setChecked(m.request_messages)
        self.req_int = _dspin(m.request_interval_s, 0.5, 30)
        self.prearm_diag = QCheckBox("בלחיצה על 'הרץ בדיקה' בלבד, כשהרחפן לא חמוש: בקש מ-ArduPilot להריץ את בדיקות "
                                     "ה-Pre-arm (MAV_CMD_RUN_PREARM_CHECKS, קריאה בלבד)")
        self.prearm_diag.setChecked(m.prearm_diagnostic)
        info = QLabel("האפליקציה לעולם אינה שולחת HEARTBEAT, פקודות חימוש/מצב/מנועים או שינויי פרמטרים, "
                      "ולעולם לא מנסה לחמש כדי לבדוק מוכנות. ה-sysid שלה חייב להיות שונה מזה של Mission Planner "
                      "(בדרך כלל 255). אם בקישור יש יותר מבקר טיסה אחד, קבע כאן את ה-sysid של הרחפן.")
        info.setWordWrap(True)
        info.setObjectName("disclaimer")
        f.addRow("סוג חיבור", self.mav_transport)
        f.addRow("מנהרה: כתובת ב-Pi", self.tunnel_host)
        f.addRow("מנהרה: פורט ב-Pi", self.tunnel_port)
        f.addRow("TCP: כתובת", self.tcp_host)
        f.addRow("TCP: פורט", self.tcp_port)
        f.addRow("UDP: פורט האזנה", self.udp_port)
        f.addRow("sysid של האפליקציה", self.src_sysid)
        f.addRow("compid של האפליקציה", self.src_compid)
        f.addRow("sysid של בקר הטיסה (0=אוטומטי)", self.tgt_sysid)
        f.addRow(self.req)
        f.addRow("מרווח בקשות (שניות)", self.req_int)
        f.addRow(self.prearm_diag)
        f.addRow(info)
        return w

    def _tab_video(self):
        v = self.s.video
        w = QWidget()
        f = QFormLayout(w)
        self.v_backend = _combo(VIDEO_BACKENDS, v.backend)
        self.v_profile = _combo([(k, _profile_label(k)) for k in SRT_PROFILES], v.srt_profile)
        self.v_srt_host = _ltr_edit(v.srt_host, "(ריק = כתובת ה-Tailscale של ה-Pi, מתוך נתוני ה-Pi)")
        self.v_srt_port = _spin(v.srt_port, 1, 65535)
        self.v_manage = QCheckBox("הפעל/עצור את x500-video@<פרופיל> ב-Pi בלחיצה על הפעל/עצור וידאו "
                                  "(sudo -n systemctl start|stop בלבד; רק כשהרחפן לא חמוש)")
        self.v_manage.setChecked(v.manage_pi_service)
        self.v_stop_svc = QCheckBox("בעצירת וידאו - עצור גם את השירות ב-Pi (רק אם האפליקציה הפעילה אותו)")
        self.v_stop_svc.setChecked(v.stop_pi_service_on_stop)
        self.v_srt_extra = _ltr_edit(v.srt_extra_params, "(מתקדם) למשל passphrase=...")
        self.v_ffmpeg = _ltr_edit(v.ffmpeg_path, "(ריק = FFmpeg שמגיע עם האפליקציה)")
        self.v_transport = _combo(VIDEO_TRANSPORTS, v.transport)
        self.v_port = _spin(v.rtsp_port, 1, 65535)
        self.v_path = _ltr_edit(v.path)
        self.v_url = _ltr_edit(v.direct_url, "rtsp://host:8554/x500cam")
        self.v_w = _spin(v.width, 160, 1920)
        self.v_h = _spin(v.height, 120, 1080)
        self.v_fps = _spin(v.fps, 1, 60)
        self.v_br = _spin(v.bitrate_kbps, 100, 20000)
        self.v_apply = QCheckBox("RTSP: החל רזולוציה/FPS על המצלמה לפני הפעלה (רק כשהרחפן לא חמוש)")
        self.v_apply.setChecked(v.apply_camera_settings)
        self.v_rec = _spin(v.max_reconnects, 0, 50)
        self.v_stale = _dspin(v.stale_after_s, 0.5, 10)
        self.v_api = _ltr_edit(v.mediamtx_api)
        f.addRow("מקור הווידאו", self.v_backend)
        f.addRow(QLabel("<b>SRT (הצינור הקיים ב-Pi)</b>"))
        f.addRow("פרופיל", self.v_profile)
        f.addRow("כתובת SRT של ה-Pi", self.v_srt_host)
        f.addRow("פורט SRT", self.v_srt_port)
        f.addRow(self.v_manage)
        f.addRow(self.v_stop_svc)
        f.addRow("פרמטרים נוספים ל-SRT", self.v_srt_extra)
        f.addRow("נתיב FFmpeg", self.v_ffmpeg)
        f.addRow(QLabel("<b>RTSP / MediaMTX (חלופה)</b>"))
        f.addRow("העברה", self.v_transport)
        f.addRow("פורט RTSP ב-Pi", self.v_port)
        f.addRow("נתיב (path) ב-MediaMTX", self.v_path)
        f.addRow("כתובת מלאה (עוקף)", self.v_url)
        f.addRow("רוחב", self.v_w)
        f.addRow("גובה", self.v_h)
        f.addRow("FPS", self.v_fps)
        f.addRow("קצב סיביות (kbps)", self.v_br)
        f.addRow(self.v_apply)
        f.addRow("MediaMTX API (ב-Pi, localhost)", self.v_api)
        f.addRow(QLabel("<b>כללי</b>"))
        f.addRow("מקסימום ניסיונות התחברות מחדש", self.v_rec)
        f.addRow("תמונה נחשבת ישנה אחרי (שניות)", self.v_stale)
        hint = QLabel("התחל בפרופיל degraded ומדוד CPU וטמפרטורה ב-Pi לפני מעבר ל-normal או high (ב-Pi 5 הקידוד בתוכנה). "
                      "ערכי ה-latency הם הגדרת חוצץ של SRT, לא השהיה שנמדדה. אם ה-Pi דורש סיסמה ל-sudo, "
                      "ראו docs/video.md - האפליקציה לא מקלידה סיסמאות ולא דורשת sudo רחב.")
        hint.setWordWrap(True)
        hint.setObjectName("disclaimer")
        f.addRow(hint)
        return w

    def _tab_pi(self):
        p = self.s.pi
        w = QWidget()
        f = QFormLayout(w)
        sv = p.services
        self.svc_router = _ltr_edit(sv.get("mavlink_router", ""), "mavlink-router")
        self.svc_bb = _ltr_edit(sv.get("blackbox", ""), "x500-blackbox")
        self.svc_health = _ltr_edit(sv.get("health_monitor", ""), "x500-health-monitor")
        self.svc_net = _ltr_edit(sv.get("net_monitor", ""), "x500-net-monitor")
        self.svc_ts = _ltr_edit(sv.get("tailscale", ""), "tailscaled")
        self.svc_mtx = _ltr_edit(sv.get("mediamtx", ""), "(ריק אם אין MediaMTX)")
        self.wd_runtime = _ltr_edit(p.watchdog_expected_runtime, "15s")
        self.bb_globs = _ltr_edit("; ".join(p.blackbox_globs), "/var/log/x500/blackbox/x500-blackbox-*.csv")
        self.bb_sleep = _dspin(p.blackbox_sleep_s, 1, 600, 1)
        self.mode = _combo([("stream", "הזרמה דרך SSH (לא מתקין דבר) - מומלץ"),
                            ("installed", "גרסה מותקנת ב-Pi")], p.collector_mode)
        self.python = _ltr_edit(p.python)
        self.legacy_python = _ltr_edit(p.legacy_python, "(ה-venv של ה-Pi, למשל /home/<user>/x500-venv/bin/python)")
        self.poll = _dspin(p.poll_interval_s, 3, 120, 1)
        self.slow_poll = _dspin(p.slow_poll_interval_s, 15, 600, 5)
        self.wifi_if = _ltr_edit(p.wifi_interface, "wlan0")
        self.lte_if = _ltr_edit(p.lte_interface, "eth1")
        self.ts_if = _ltr_edit(p.tailscale_interface, "tailscale0")
        self.zte_gw = _ltr_edit(p.zte_gateway, "(כתובת השער של מודם ה-ZTE, למשל 192.168.0.1)")
        self.ground_ip = _ltr_edit(p.ground_ip, "(כתובת תחנת הקרקע)")
        self.ping = _ltr_edit(p.ping_target, "1.1.1.1")
        self.lte_vids = _ltr_edit(", ".join(p.lte_usb_vids), "19d2")
        self.fc_glob = _ltr_edit(p.fc_byid_glob, "usb-Holybro_Pixhawk6C_*-if00")
        self.fc_exact = _ltr_edit(p.expected_fc_byid, "(אופציונלי: השם המלא ב-/dev/serial/by-id)")
        self.cam_probe = QCheckBox("בדוק זיהוי מצלמה (rpicam-hello --list-cameras, במחזור האיטי)")
        self.cam_probe.setChecked(p.camera_probe)
        self.tools = QPlainTextEdit(json.dumps(p.tools, ensure_ascii=False, indent=1))
        self.tools.setLayoutDirection(Qt.LeftToRight)
        self.tools.setMaximumHeight(110)
        f.addRow("שירות mavlink-router", self.svc_router)
        f.addRow("שירות קופסה שחורה", self.svc_bb)
        f.addRow("שירות ניטור בריאות", self.svc_health)
        f.addRow("שירות ניטור רשת", self.svc_net)
        f.addRow("שירות Tailscale", self.svc_ts)
        f.addRow("שירות MediaMTX", self.svc_mtx)
        f.addRow("RuntimeWatchdogSec צפוי", self.wd_runtime)
        f.addRow("קבצי הקופסה השחורה (glob, מופרד ב-;)", self.bb_globs)
        f.addRow("השהיה בין דגימות בקופסה השחורה (שניות)", self.bb_sleep)
        f.addRow("אופן הרצת ה-collector", self.mode)
        f.addRow("פקודת Python ל-collector", self.python)
        f.addRow("Python של ה-venv ב-Pi", self.legacy_python)
        f.addRow("מחזור מהיר (שניות)", self.poll)
        f.addRow("מחזור איטי: ping, מצלמה, USB (שניות)", self.slow_poll)
        f.addRow("ממשק Wi-Fi", self.wifi_if)
        f.addRow("ממשק LTE", self.lte_if)
        f.addRow("ממשק Tailscale", self.ts_if)
        f.addRow("שער מודם ה-ZTE", self.zte_gw)
        f.addRow("כתובת תחנת הקרקע", self.ground_ip)
        f.addRow("יעד ping לאינטרנט", self.ping)
        f.addRow("USB vendor ID של מודם LTE", self.lte_vids)
        f.addRow("בקר הטיסה ב-by-id (תבנית)", self.fc_glob)
        f.addRow("בקר הטיסה ב-by-id (שם מדויק)", self.fc_exact)
        f.addRow(self.cam_probe)
        f.addRow("סקריפטים קיימים (JSON)", self.tools)
        hint = QLabel('דוגמה: [{"name": "x500-health", "command": "x500-health --json", "timeout_s": 20, '
                      '"enabled": false}]. סקריפטים מורצים רק אם enabled=true. ודא שהם לקריאה בלבד לפני שתפעיל אותם.')
        hint.setWordWrap(True)
        hint.setObjectName("disclaimer")
        f.addRow(hint)
        return w

    def _tab_general(self, config_dir, profiles_dir):
        w = QWidget()
        f = QFormLayout(w)
        self.sim = QCheckBox("מצב סימולציה (לא מתחבר לרחפן - נתונים מדומים בלבד)")
        self.sim.setChecked(self.s.simulation)
        self.scenario = _combo(list(SCENARIOS.items()), self.s.sim_scenario)
        self.reports = _ltr_edit(self.s.reports_dir, r"Documents\X500FlightStation\reports")
        self.hide_private = QCheckBox("הסתר פרטים פרטיים בדוחות ובייצוא האבחון (כתובות IP, MAC, מספר סידורי של "
                                      "ה-Pixhawk, שם משתמש, שם רשת Wi-Fi)")
        self.hide_private.setChecked(self.s.report_hide_private)
        open_cfg = QPushButton("פתח תיקיית הגדרות")
        open_cfg.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(config_dir)))
        open_prof = QPushButton("פתח תיקיית פרופילים (bench.json / flight.json)")
        open_prof.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(profiles_dir)))
        imp = QPushButton("ייבוא הגדרות מקובץ מקומי…")
        imp.setToolTip("קובץ JSON עם פרטי החיבור (כתובות, שם משתמש, נתיבים) ונתוני הסוללה. "
                       "הקובץ נשאר במחשב שלך ולא עולה לשום מקום. סיסמאות לא נשמרות בו.")
        imp.clicked.connect(self._choose_import)
        f.addRow(self.sim)
        f.addRow("תרחיש סימולציה", self.scenario)
        f.addRow("תיקיית דוחות", self.reports)
        f.addRow(self.hide_private)
        f.addRow(open_cfg)
        f.addRow(open_prof)
        f.addRow(imp)
        cfg_lbl = QLabel(f"הגדרות: ⁦{config_dir}⁩")
        cfg_lbl.setObjectName("disclaimer")
        f.addRow(cfg_lbl)
        return w

    # --------------------------------------------------------------- actions
    IMPORT_RESULT = 2

    def _choose_import(self):
        path, _ = QFileDialog.getOpenFileName(self, "בחר קובץ הגדרות מקומי", "", "JSON (*.json)")
        if path:
            self.import_path = path
            self.done(self.IMPORT_RESULT)  # the main window previews and applies it

    def _browse_key(self):
        path, _ = QFileDialog.getOpenFileName(self, "בחר מפתח SSH פרטי")
        if path:
            self.key_path.setText(path)

    def _secret_ident(self):
        if self.auth.currentData() == "password":
            return credentials.password_id(self.user.text().strip(), self.host.text().strip(), self.port.value()), "סיסמת SSH"
        if self.key_path.text().strip():
            return credentials.passphrase_id(self.key_path.text().strip()), "passphrase של המפתח"
        return None, None

    def _refresh_secret_label(self):
        ident, label = self._secret_ident()
        if ident is None:
            self.secret_lbl.setText("לא נדרש (מפתח ללא passphrase או agent)")
            return
        self.secret_lbl.setText(f"{label}: " + ("שמורה במנהל האישורים" if credentials.get(ident) else "לא שמורה"))

    def _set_secret(self):
        ident, label = self._secret_ident()
        if ident is None:
            QMessageBox.information(self, "סיסמה", "בחר אימות בסיסמה, או קובץ מפתח עם passphrase.")
            return
        text, ok = QInputDialog.getText(self, label, f"הזן {label} (תישמר רק במנהל האישורים של Windows):",
                                        QLineEdit.Password)
        if not ok:
            return
        try:
            if text:
                credentials.set(ident, text)
            else:
                credentials.delete(ident)
        except credentials.SecretStoreError as e:
            QMessageBox.critical(self, "שגיאה", f"לא ניתן לשמור באופן מאובטח: {e}")
        self._refresh_secret_label()

    def _forget(self):
        if not self._forget_hostkey:
            return
        if QMessageBox.question(self, "שכח מפתח שרת",
                                "למחוק את מפתח השרת השמור של ה-Pi? בחיבור הבא תתבקש לאמת את טביעת האצבע מחדש.") \
                == QMessageBox.Yes:
            done = self._forget_hostkey()
            QMessageBox.information(self, "מפתח שרת", "המפתח נמחק." if done else "לא נמצא מפתח שמור לכתובת הזו.")

    def _accept(self):
        try:
            tools = json.loads(self.tools.toPlainText() or "[]")
            if not isinstance(tools, list):
                raise ValueError("must be a JSON list")
        except ValueError as e:
            QMessageBox.critical(self, "שגיאה", f"רשימת הסקריפטים אינה JSON תקין: {e}")
            return
        s = self.s
        s.drone_name = self.drone_name.text().strip() or "X500"
        c = s.connection
        c.host = self.host.text().strip()
        c.ssh_port = self.port.value()
        c.username = self.user.text().strip()
        c.auth_method = self.auth.currentData()
        c.key_path = self.key_path.text().strip()
        c.link_path = self.link.currentData()
        c.expected_hostname = self.exp_host.text().strip()
        c.connect_timeout_s = self.timeout.value()
        m = s.mavlink
        m.transport = self.mav_transport.currentData()
        m.tunnel_host = self.tunnel_host.text().strip() or "127.0.0.1"
        m.tunnel_port = self.tunnel_port.value()
        m.tcp_host = self.tcp_host.text().strip()
        m.tcp_port = self.tcp_port.value()
        m.udp_port = self.udp_port.value()
        m.source_sysid = self.src_sysid.value()
        m.source_compid = self.src_compid.value()
        m.target_sysid = self.tgt_sysid.value()
        m.request_messages = self.req.isChecked()
        m.request_interval_s = self.req_int.value()
        m.prearm_diagnostic = self.prearm_diag.isChecked()
        v = s.video
        v.backend = self.v_backend.currentData()
        v.srt_profile = self.v_profile.currentData()
        v.srt_host = self.v_srt_host.text().strip()
        v.srt_port = self.v_srt_port.value()
        v.manage_pi_service = self.v_manage.isChecked()
        v.stop_pi_service_on_stop = self.v_stop_svc.isChecked()
        v.srt_extra_params = self.v_srt_extra.text().strip()
        v.ffmpeg_path = self.v_ffmpeg.text().strip()
        v.transport = self.v_transport.currentData()
        v.rtsp_port = self.v_port.value()
        v.path = self.v_path.text().strip() or "x500cam"
        v.direct_url = self.v_url.text().strip()
        v.width, v.height, v.fps = self.v_w.value(), self.v_h.value(), self.v_fps.value()
        v.bitrate_kbps = self.v_br.value()
        v.apply_camera_settings = self.v_apply.isChecked()
        v.max_reconnects = self.v_rec.value()
        v.stale_after_s = self.v_stale.value()
        v.mediamtx_api = self.v_api.text().strip()
        p = s.pi
        p.services = dict(p.services)  # keep keys this dialog does not show
        p.services.update({"mavlink_router": self.svc_router.text().strip(), "blackbox": self.svc_bb.text().strip(),
                           "health_monitor": self.svc_health.text().strip(),
                           "net_monitor": self.svc_net.text().strip(), "tailscale": self.svc_ts.text().strip(),
                           "mediamtx": self.svc_mtx.text().strip()})
        p.watchdog_expected_runtime = self.wd_runtime.text().strip()
        p.blackbox_globs = [g.strip() for g in self.bb_globs.text().split(";") if g.strip()]
        p.blackbox_sleep_s = self.bb_sleep.value()
        p.collector_mode = self.mode.currentData()
        p.python = self.python.text().strip() or "python3"
        p.legacy_python = self.legacy_python.text().strip()
        p.poll_interval_s = self.poll.value()
        p.slow_poll_interval_s = self.slow_poll.value()
        p.wifi_interface = self.wifi_if.text().strip()
        p.lte_interface = self.lte_if.text().strip()
        p.tailscale_interface = self.ts_if.text().strip()
        p.zte_gateway = self.zte_gw.text().strip()
        p.ground_ip = self.ground_ip.text().strip()
        p.ping_target = self.ping.text().strip()
        p.lte_usb_vids = [x.strip().lower() for x in self.lte_vids.text().split(",") if x.strip()]
        p.fc_byid_glob = self.fc_glob.text().strip() or "usb-Holybro_Pixhawk6C_*-if00"
        p.expected_fc_byid = self.fc_exact.text().strip()
        p.camera_probe = self.cam_probe.isChecked()
        p.tools = tools
        s.simulation = self.sim.isChecked()
        s.sim_scenario = self.scenario.currentData()
        s.reports_dir = self.reports.text().strip()
        s.report_hide_private = self.hide_private.isChecked()
        s.first_run_done = True
        self.accept()


class HostKeyDialog(QDialog):
    """Explicit confirmation of an unknown SSH host key. Default = reject."""

    def __init__(self, info: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("אימות מפתח השרת של ה-Pi")
        self.resize(620, 360)
        lay = QVBoxLayout(self)
        t = QLabel("<b>מפתח השרת של ה-Pi אינו מוכר למחשב הזה.</b>")
        lay.addWidget(t)
        lay.addWidget(QLabel(f"כתובת: ⁦{info['host']}:{info['port']}⁩"))
        lay.addWidget(QLabel(f"סוג מפתח: ⁦{info['key_type']}⁩"))
        fp = QLineEdit(info["fingerprint"])
        fp.setReadOnly(True)
        fp.setLayoutDirection(Qt.LeftToRight)
        fp.setStyleSheet("font-family:Consolas,monospace;font-size:13px")
        lay.addWidget(QLabel("טביעת אצבע (fingerprint):"))
        lay.addWidget(fp)
        how = QLabel("כדי לוודא שזה באמת ה-Pi שלך, הרץ <b>על ה-Pi עצמו</b> (מסך/מקלדת, או חיבור שאתה כבר סומך עליו):"
                     "<br><code>&#x2066;ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub&#x2069;</code><br>"
                     "ואשר רק אם טביעת האצבע זהה לחלוטין.")
        how.setWordWrap(True)
        lay.addWidget(how)
        lay.addStretch(1)
        bb = QDialogButtonBox()
        trust = bb.addButton("הטביעה זהה - סמוך ושמור", QDialogButtonBox.AcceptRole)
        cancel = bb.addButton("ביטול", QDialogButtonBox.RejectRole)
        cancel.setDefault(True)
        trust.setAutoDefault(False)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
