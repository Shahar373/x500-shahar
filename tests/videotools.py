"""Pick an FFmpeg for network video tests.

The app bundles FFmpeg via imageio-ffmpeg. On Linux that is a fully static
build, and its glibc resolver crashes on *any* network input (TCP or SRT; local
files are fine), so Linux tests use a system FFmpeg with libsrt when present.
The Windows CI job checks the bundled Windows build (gyan.dev, --enable-libsrt)
in the self-test of the installed app.
"""

from __future__ import annotations

import shutil
import sys

import pytest

from x500fs.video.ffmpeg_tools import find_ffmpeg, probe


def network_ffmpeg() -> str:
    candidates = []
    if sys.platform == "win32":
        candidates.append(find_ffmpeg())
    else:
        sysff = shutil.which("ffmpeg")
        if sysff:
            candidates.append(sysff)
    for exe in candidates:
        try:
            if probe(exe)["srt"]:
                return exe
        except Exception:  # noqa: BLE001
            continue
    pytest.skip("no FFmpeg with SRT support usable for network tests on this machine")


def file_ffmpeg() -> str:
    try:
        return find_ffmpeg()
    except FileNotFoundError:
        pytest.skip("bundled FFmpeg not available")
