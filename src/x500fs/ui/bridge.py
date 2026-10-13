"""Qt bridge: re-emits controller callbacks (from worker threads) as signals."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ..controller import Listener


class UiBridge(QObject, Listener):
    connection_changed = Signal()
    statustext = Signal(dict)
    hostkey_unknown = Signal(dict)
    video_frame = Signal()
    video_status = Signal(dict)
    notice = Signal(str, str)

    def __init__(self):
        QObject.__init__(self)
        self._frame_pending = False

    def on_connection_changed(self) -> None:
        self.connection_changed.emit()

    def on_statustext(self, item: dict) -> None:
        self.statustext.emit(item)

    def on_hostkey_unknown(self, info: dict) -> None:
        self.hostkey_unknown.emit(info)

    def on_video_frame(self) -> None:
        # Coalesce: at most one pending repaint request in the event queue.
        if not self._frame_pending:
            self._frame_pending = True
            self.video_frame.emit()

    def frame_consumed(self) -> None:
        self._frame_pending = False

    def on_video_status(self, status: dict) -> None:
        self.video_status.emit(status)

    def on_notice(self, level: str, text: str) -> None:
        self.notice.emit(level, text)
