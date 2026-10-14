"""Locating and probing the FFmpeg binary used for video.

The app ships FFmpeg through the hash-pinned ``imageio-ffmpeg`` wheel (the
Windows build is gyan.dev's 7.1 "essentials", configured with
``--enable-libsrt``). PyAV is not used: its wheels are built without libsrt.
"""

from __future__ import annotations

import functools
import os
import re
import subprocess

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def find_ffmpeg(override: str = "") -> str:
    """Path of the FFmpeg executable: the configured one, else the bundled one."""
    if override:
        if not os.path.isfile(override):
            raise FileNotFoundError(f"FFmpeg not found at configured path: {override}")
        return override
    try:
        import imageio_ffmpeg
    except ImportError as e:  # pragma: no cover - packaging error
        raise FileNotFoundError(f"bundled FFmpeg (imageio-ffmpeg) is missing: {e}") from e
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    if not exe or not os.path.isfile(exe):
        raise FileNotFoundError("bundled FFmpeg binary not found")
    return exe


def _run(exe: str, *args: str, timeout: float = 15.0) -> str:
    p = subprocess.run([exe, "-hide_banner", *args], stdin=subprocess.DEVNULL, capture_output=True,
                       timeout=timeout, creationflags=NO_WINDOW)
    return p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")


@functools.lru_cache(maxsize=4)
def probe(exe: str) -> dict:
    """Version and input protocols of an FFmpeg binary (cached per path)."""
    out = _run(exe, "-version")
    m = re.search(r"ffmpeg version (\S+)", out)
    prot = _run(exe, "-protocols")
    inputs = set()
    section = None
    for line in prot.splitlines():
        s = line.strip()
        if s.startswith("Input:"):
            section = "in"
        elif s.startswith("Output:"):
            section = "out"
        elif section == "in" and s and " " not in s:
            inputs.add(s)
    return {"path": exe, "version": m.group(1) if m else None, "input_protocols": sorted(inputs),
            "srt": "srt" in inputs, "libsrt_configured": "--enable-libsrt" in out}


def describe(override: str = "") -> dict:
    try:
        exe = find_ffmpeg(override)
        info = dict(probe(exe))
        info["path"] = os.path.basename(exe)
        return info
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}", "srt": False}
