"""GUI smoke tests (offscreen): RTL, simulation banner, stale video, manual checklist."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("PySide6.QtWidgets")
pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def app():
    from x500fs.ui.app import create_application

    return create_application()


def pump(app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def _window(tmp_path, simulation=True):
    from x500fs.settings import SettingsStore
    from x500fs.ui.main_window import MainWindow

    store = SettingsStore(tmp_path / "cfg")
    s = store.load()
    s.simulation = simulation
    s.reports_dir = str(tmp_path / "reports")
    store.save(s)
    return MainWindow(store)


def test_rtl_and_sim_banner(app, tmp_path):
    from PySide6.QtCore import Qt

    w = _window(tmp_path)
    w.show()
    assert app.layoutDirection() == Qt.RightToLeft
    assert w.sim_banner.isVisible()
    assert w.windowTitle().startswith("[סימולציה]")
    w.close()


def test_live_mode_has_no_sim_banner(app, tmp_path):
    w = _window(tmp_path, simulation=False)
    w.show()
    assert not w.sim_banner.isVisible()
    assert "סימולציה" not in w.windowTitle()
    w.close()


def test_sim_session_flow(app, tmp_path):
    w = _window(tmp_path)
    w.show()
    w._toggle_connect()
    pump(app, 2.0)
    assert w.ctl.connected
    w.manual._boxes["props"].setChecked(True)
    pump(app, 0.1)
    assert w.ctl.manual.is_confirmed("props")
    w._run_preflight()
    pump(app, 0.2)
    assert not w.ctl.manual.is_confirmed("props")
    assert not w.manual._boxes["props"].isChecked()
    w._start_video()
    pump(app, 1.0)
    assert w.video.frame.is_live()
    jp, hp = w.ctl.export_report()
    assert jp.exists() and hp.exists()
    w._toggle_connect()
    pump(app, 0.3)
    assert not w.ctl.connected
    w.close()


def test_stale_frame_is_not_shown_as_live(app):
    from x500fs.ui.video_view import FrameWidget
    from x500fs.video.worker import Frame

    fw = FrameWidget()
    fw.state = "playing"
    fw.stale_after_s = 0.2
    fw.set_frame(Frame(4, 4, 12, bytes(48), time.monotonic(), 1))
    assert fw.is_live()
    time.sleep(0.3)
    assert not fw.is_live()
    img = fw.grab()  # paints the stale overlay without errors
    assert not img.isNull()


def test_video_refused_in_live_mode_without_telemetry(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    w = _window(tmp_path, simulation=False)
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    w._start_video()
    assert shown and not w.ctl.video_running()
    w.close()


def test_import_local_config_from_ui(app, tmp_path, monkeypatch):
    import json

    from PySide6.QtWidgets import QMessageBox

    from x500fs import local_config

    w = _window(tmp_path, simulation=False)
    shown = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: (shown.append(a[2]), QMessageBox.Yes)[1])
    p = tmp_path / "x500-local-config.json"
    p.write_text(json.dumps({"schema": local_config.SCHEMA,
                             "settings": {"connection": {"username": "pilot1"}, "pi": {"ground_ip": "100.99.1.2"}},
                             "profiles": {"bench": {"battery": {"chemistry": "LiPo", "cells": 4,
                                                                "capacity_mah": 5200}}}}), "utf-8")
    w._import_local_config(str(p))
    assert shown and "pilot1" in shown[0] and "Pi" in shown[0]
    assert w.ctl.settings.connection.username == "pilot1"
    assert w.ctl.profile.battery["capacity_mah"] == 5200
    w.close()


def test_example_local_config_is_importable(tmp_path):
    from pathlib import Path

    from x500fs import local_config
    from x500fs.settings import SettingsStore

    ex = Path(__file__).resolve().parents[1] / "docs" / "local-config.example.json"
    store = SettingsStore(tmp_path / "cfg")
    plan = local_config.plan(local_config.read(ex), store.load(), store.profiles_dir)
    assert plan.changes and not plan.ignored


def test_light_theme_even_when_windows_is_in_dark_mode(app):
    """Windows dark mode used to give white text on the app's white backgrounds."""
    from PySide6.QtGui import QColor, QPalette

    from x500fs.ui.app import apply_light_theme

    dark = QPalette()
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        dark.setColor(role, QColor("#ffffff"))
    for role in (QPalette.Window, QPalette.Base, QPalette.Button):
        dark.setColor(role, QColor("#202020"))
    app.setPalette(dark)
    apply_light_theme(app)
    pal = app.palette()
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        assert pal.color(role).lightness() < 80, role       # dark text
    for role in (QPalette.Window, QPalette.Base, QPalette.Button):
        assert pal.color(role).lightness() > 220, role      # light backgrounds


def test_checks_problems_first_and_filter(app, tmp_path):
    w = _window(tmp_path)
    w.show()
    w.ctl.connect()
    pump(app, 2.0)
    w.refresh()
    cv = w.checks
    root = cv.tree.invisibleRootItem()
    order = ["FAIL", "UNKNOWN", "WARN", "PASS", "NOT_APPLICABLE"]
    cats = [root.child(i).text(0) for i in range(root.childCount())]
    assert [order.index(c) for c in cats] == sorted(order.index(c) for c in cats)
    healthy = [root.child(i) for i in range(root.childCount()) if root.child(i).text(0) in ("PASS", "NOT_APPLICABLE")]
    assert healthy and not any(c.isExpanded() for c in healthy)
    cv.btn_problems.click()
    visible = [it for cid, it in cv._items.items() if not it.isHidden() and not it.parent().isHidden()]
    assert visible and all(cv._results[it.data(0, 0x0100)].status.value in ("FAIL", "UNKNOWN", "WARN")
                           for it in visible)
    cv.btn_all.click()
    assert not any(it.isHidden() for it in cv._items.values())
    w.ctl.disconnect()
    w.close()


def test_video_panel_is_compact_while_off(app, tmp_path):
    w = _window(tmp_path)
    w.show()
    pump(app, 0.2)
    assert w.tabs.currentWidget() is w.telemetry
    assert not w.video.details.isVisible()
    w.video.set_running(True)
    assert w.video.details.isVisibleTo(w.video) and not w.video.idle_lbl.isVisibleTo(w.video)
    w.video.set_running(False)
    assert not w.video.details.isVisibleTo(w.video)
    w.close()
