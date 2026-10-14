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


_crash_file = None  # kept open for faulthandler for the life of the process


def install_crash_handlers(logs_dir) -> None:
    """Make every failure visible in the logs folder (the windowed exe has no console).

    * native crashes (access violation etc.): Python stacks of all threads -> logs/crash.log
    * uncaught Python exceptions in the UI thread or worker threads -> logs/x500fs.log
    * a windowed PyInstaller exe has sys.stdout/sys.stderr = None; give them a file
    """
    global _crash_file
    import faulthandler
    import threading
    import time
    import traceback

    log = logging.getLogger("x500fs.crash")
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        if sys.stdout is None or sys.stderr is None:
            stdio = open(logs_dir / "stdio.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
            sys.stdout = sys.stdout or stdio
            sys.stderr = sys.stderr or stdio
        _crash_file = open(logs_dir / "crash.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        _crash_file.write(f"\n--- start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        faulthandler.enable(file=_crash_file, all_threads=True)
    except OSError:
        pass

    def excepthook(exc_type, exc, tb):
        log.critical("uncaught exception in the UI thread:\n%s",
                     "".join(traceback.format_exception(exc_type, exc, tb)))

    def thread_excepthook(args):
        log.critical("uncaught exception in thread %s:\n%s", getattr(args.thread, "name", "?"),
                     "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook


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
    install_crash_handlers(store.logs_dir)
    logging.getLogger(__name__).info("starting %s", APP_NAME)
    app = create_application(sys.argv[:1])
    icon = app_icon()
    w = MainWindow(store, icon)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
