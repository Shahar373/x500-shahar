"""Entry point: ``python -m x500fs`` or the packaged X500FlightStation.exe."""

from __future__ import annotations

import logging
import logging.handlers
import sys


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        from .core.redact import redact_text

        try:
            record.msg = redact_text(str(record.getMessage()))
            record.args = ()
        except Exception:  # noqa: BLE001
            pass
        return True


def setup_logging(logs_dir) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(logs_dir / "x500fs.log", maxBytes=2_000_000, backupCount=3,
                                                  encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        fh.addFilter(_RedactingFilter())
        root.addHandler(fh)
    except OSError:
        pass
    logging.getLogger("paramiko").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--self-test" in argv:
        from .selftest import run_selftest

        i = argv.index("--self-test")
        out = argv[i + 1] if len(argv) > i + 1 and not argv[i + 1].startswith("--") else None
        return run_selftest(out, offscreen="--offscreen" in argv)
    if "--version" in argv:
        from . import __version__

        print(__version__)
        return 0

    from . import APP_ID, APP_NAME
    from .settings import SettingsStore
    from .ui.app import app_icon, create_application
    from .ui.main_window import MainWindow

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:  # noqa: BLE001
            pass
    store = SettingsStore()
    setup_logging(store.logs_dir)
    logging.getLogger(__name__).info("starting %s", APP_NAME)
    app = create_application(sys.argv[:1])
    icon = app_icon()
    w = MainWindow(store, icon)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
