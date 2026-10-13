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
