"""QApplication setup shared by the GUI entry point and the self-test."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from .. import APP_ID, APP_NAME
from .style import APP_QSS


def app_icon() -> QIcon | None:
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "app.ico")
    return QIcon(path) if os.path.exists(path) else None


def create_application(argv: list[str] | None = None) -> QApplication:
    app = QApplication.instance() or QApplication(argv or sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ID)
    app.setLayoutDirection(Qt.RightToLeft)
    if sys.platform == "win32":
        app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(APP_QSS)
    icon = app_icon()
    if icon:
        app.setWindowIcon(icon)
    return app
