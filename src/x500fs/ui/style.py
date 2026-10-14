"""Colors, status badges and the application stylesheet."""

from __future__ import annotations

STATUS_COLORS = {
    "PASS": "#1b7f3b",
    "WARN": "#b26a00",
    "FAIL": "#c62828",
    "UNKNOWN": "#5f6b7a",
    "NOT_APPLICABLE": "#9aa0a6",
}
STATUS_HE = {
    "PASS": "עבר",
    "WARN": "אזהרה",
    "FAIL": "נכשל",
    "UNKNOWN": "לא ידוע",
    "NOT_APPLICABLE": "לא רלוונטי",
}
OVERALL_COLORS = {
    "NOT_RUN": "#5f6b7a",
    "PASS": "#1b7f3b",
    "PASS_WITH_WARNINGS": "#b26a00",
    "INCOMPLETE": "#5f6b7a",
    "FAIL": "#c62828",
}
OVERALL_HE = {
    "NOT_RUN": "טרם הורצו בדיקות",
    "PASS": "בדיקות אוטומטיות עברו",
    "PASS_WITH_WARNINGS": "עברו עם אזהרות",
    "INCOMPLETE": "לא הושלם - חסרים נתונים/הגדרות",
    "FAIL": "בדיקה קריטית נכשלה",
}

SIM_COLOR = "#ffb300"

APP_QSS = """
QMainWindow, QDialog { background: #f5f7fa; }
QToolBar { background: #ffffff; border-bottom: 1px solid #dde3ea; spacing: 6px; padding: 4px; }
QToolButton { font-size: 14px; padding: 8px 14px; border-radius: 6px; border: 1px solid #c9d1db; background: #ffffff;
    color: #1d2330; }
QToolButton:hover { background: #eef3f8; }
QToolButton:disabled { color: #9aa0a6; background: #f3f5f7; }
QToolButton#primary { background: #1f6feb; color: white; border-color: #1f6feb; font-weight: 600; }
QToolButton#primary:disabled { background: #9fbef0; border-color: #9fbef0; }
QFrame#card { background: #ffffff; border: 1px solid #dde3ea; border-radius: 8px; }
QLabel#cardTitle { color: #5f6b7a; font-size: 12px; }
QLabel#cardValue { font-size: 16px; font-weight: 700; }
QTreeWidget, QTableWidget, QTextBrowser { background: #ffffff; border: 1px solid #dde3ea; border-radius: 6px; }
QHeaderView::section { background: #f3f6f9; border: none; border-bottom: 1px solid #dde3ea; padding: 4px 6px; font-weight: 600; }
QTabWidget::pane { border: 1px solid #dde3ea; border-radius: 6px; background: #ffffff; }
QTabBar::tab { padding: 6px 14px; color: #1d2330; background: #eef2f6; border: 1px solid #dde3ea;
    border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-left: 2px; }
QTabBar::tab:selected { background: #ffffff; font-weight: 600; }
QTabBar::tab:!selected:hover { background: #e3e9f0; }
QLabel#simBanner { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ffb300, stop:1 #ffd54f);
    color: #3e2700; font-size: 15px; font-weight: 700; padding: 8px; border-bottom: 2px solid #e65100; }
QLabel#errorBanner { background: #fdecea; color: #8e1c1c; padding: 8px; border-bottom: 1px solid #f5c2c0; font-weight: 600; }
QLabel#disclaimer { color: #5f6b7a; font-size: 11px; }
"""


def badge_html(status: str) -> str:
    color = STATUS_COLORS.get(status, "#5f6b7a")
    return (f"<span style='background:{color};color:white;padding:1px 6px;border-radius:4px;"
            f"font-weight:600'>&#x2066;{status}&#x2069;</span>")
