"""Generate src/x500fs/assets/app.ico (multi-size, PNG-compressed entries).

    QT_QPA_PLATFORM=offscreen python tools/make_icon.py
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QImage, QPainter, QPen


def render(size: int) -> bytes:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 64.0
    p.setBrush(QBrush(QColor("#1f6feb")))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(2 * s, 2 * s, 60 * s, 60 * s), 12 * s, 12 * s)
    pen = QPen(QColor("white"), max(1.0, 4 * s))
    p.setPen(pen)
    c = QPointF(32 * s, 32 * s)
    for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        end = QPointF(c.x() + dx * 15 * s, c.y() + dy * 15 * s)
        p.drawLine(c, end)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(end, 8 * s, 8 * s)
    p.setBrush(QBrush(QColor("#ffb300")))
    p.setPen(Qt.NoPen)
    p.drawEllipse(c, 6 * s, 6 * s)
    p.end()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def main(out: str) -> None:
    QGuiApplication(sys.argv[:1])
    sizes = [16, 24, 32, 48, 64, 128, 256]
    pngs = [render(s) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries, data = b"", b""
    for s, png in zip(sizes, pngs):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_bytes(header + entries + data)
    print(f"wrote {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "src/x500fs/assets/app.ico")
