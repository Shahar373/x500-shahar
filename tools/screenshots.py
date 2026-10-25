"""Render documentation screenshots in simulation mode (no hardware needed).

    QT_QPA_PLATFORM=offscreen python tools/screenshots.py docs/screenshots
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path


def pump(app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def main(out_dir: str) -> int:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    from x500fs.settings import SettingsStore
    from x500fs.ui.app import create_application
    from x500fs.ui.dialogs import HostKeyDialog, SettingsDialog
    from x500fs.ui.main_window import MainWindow

    tmp = Path(tempfile.mkdtemp(prefix="x500fs-shots-"))
    app = create_application()
    for name, scenario, profile, tab, select in (
        ("main-bench-healthy", "healthy", "bench", 1, "fc.prearm"),
        ("main-flight-prearm-fail", "prearm_fail", "flight", 2, "fc.prearm"),
        ("main-telemetry", "healthy", "bench", 0, "batt.voltage"),
    ):
        store = SettingsStore(tmp / name)
        s = store.load()
        s.simulation = True
        s.sim_scenario = scenario
        s.profile_id = profile
        store.save(s)
        w = MainWindow(store)
        w.resize(1500, 900)
        w.show()
        w.ctl.connect()
        pump(app, 2.0)
        w._run_preflight()
        if tab == 1:
            w._start_video()
        pump(app, 2.5)
        w.tabs.setCurrentIndex(tab)
        item = w.checks._items.get(select)
        if item is not None:
            w.checks.tree.setCurrentItem(item)
        w.refresh()
        pump(app, 0.5)
        w.grab().save(str(out / f"{name}.png"))
        w.ctl.disconnect()
        w.close()
        pump(app, 0.3)
    store = SettingsStore(tmp / "dlg")
    d = SettingsDialog(store.load(), str(store.dir), str(store.profiles_dir))
    d.show()
    pump(app, 0.3)
    d.grab().save(str(out / "settings.png"))
    d.close()
    h = HostKeyDialog({"host": "x500pi", "port": 22, "key_type": "ssh-ed25519",
                       "fingerprint": "SHA256:EXAMPLE0example0EXAMPLE0example0EXAMPLE0ex"})
    h.show()
    pump(app, 0.3)
    h.grab().save(str(out / "hostkey.png"))
    h.close()
    print(f"screenshots written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots"))
