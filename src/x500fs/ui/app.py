"""QApplication setup shared by the GUI entry point and the self-test."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPalette
from PySide6.QtWidgets import QApplication

from .. import APP_ID, APP_NAME
from .style import APP_QSS


def app_icon() -> QIcon | None:
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "app.ico")
    return QIcon(path) if os.path.exists(path) else None


LIGHT_PALETTE = {
    QPalette.Window: "#f5f7fa",
    QPalette.WindowText: "#1d2330",
    QPalette.Base: "#ffffff",
    QPalette.AlternateBase: "#f3f6f9",
    QPalette.ToolTipBase: "#ffffe1",
    QPalette.ToolTipText: "#1d2330",
    QPalette.PlaceholderText: "#8a94a3",
    QPalette.Text: "#1d2330",
    QPalette.Button: "#ffffff",
    QPalette.ButtonText: "#1d2330",
    QPalette.BrightText: "#ffffff",
    QPalette.Light: "#ffffff",
    QPalette.Midlight: "#e9edf2",
    QPalette.Mid: "#c9d1db",
    QPalette.Dark: "#8a94a3",
    QPalette.Shadow: "#5f6b7a",
    QPalette.Highlight: "#1f6feb",
    QPalette.HighlightedText: "#ffffff",
    QPalette.Link: "#1f6feb",
    QPalette.LinkVisited: "#6f42c1",
}
DISABLED_TEXT = "#9aa0a6"


def apply_light_theme(app: QApplication) -> None:
    """Always use the app's own light theme.

    The stylesheet paints light backgrounds (white cards, toolbar, tables). On
    Windows in dark mode Qt would otherwise supply a dark palette with white text,
    which ends up as white text on white backgrounds. So the color scheme, the
    style (Fusion follows the palette exactly) and the palette are all fixed.
    """
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):  # Qt >= 6.8
        try:
            hints.setColorScheme(Qt.ColorScheme.Light)
        except Exception:  # noqa: BLE001 - best effort; the palette below is what matters
            pass
    app.setStyle("Fusion")  # resets the palette, so it must come before setPalette
    pal = QPalette()
    for role, color in LIGHT_PALETTE.items():
        pal.setColor(QPalette.All, role, QColor(color))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(DISABLED_TEXT))
    app.setPalette(pal)


def create_application(argv: list[str] | None = None) -> QApplication:
    app = QApplication.instance() or QApplication(argv or sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ID)
    app.setLayoutDirection(Qt.RightToLeft)
    apply_light_theme(app)
    if sys.platform == "win32":
        app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(APP_QSS)
    icon = app_icon()
    if icon:
        app.setWindowIcon(icon)
    return app
