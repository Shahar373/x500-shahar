# Third-party components / רכיבי צד שלישי

X500 Flight Station bundles the following components in the Windows build
(PyInstaller + Inno Setup). Versions are pinned in `requirements.txt`.
This list is informational and is not legal advice.

| Component | Use | License |
|---|---|---|
| Python 3.12 runtime | interpreter embedded by PyInstaller | PSF License |
| PySide6-Essentials / Shiboken6 (Qt 6) | user interface | LGPL-3.0 (Qt for Python, dynamically linked DLLs) |
| pymavlink | MAVLink parsing/encoding | LGPL-3.0 |
| fastcrc, lxml (pymavlink dependencies) | | MIT / BSD-3-Clause |
| paramiko | SSH | LGPL-2.1 |
| cryptography, bcrypt, PyNaCl, cffi, pycparser, invoke | paramiko dependencies | Apache-2.0 / BSD |
| keyring, jaraco.*, more-itertools, pywin32-ctypes | Windows Credential Manager access | MIT / BSD-3-Clause |
| imageio-ffmpeg 0.6.0 | locates the bundled FFmpeg executable | BSD-2-Clause |
| FFmpeg 7.1 "essentials" build by gyan.dev (`ffmpeg-win-x86_64-v7.1.exe`, shipped inside the imageio-ffmpeg wheel) | SRT caller + MPEG-TS/H.264 decoding (and RTSP for the MediaMTX alternative), run as a separate process | Configured with `--enable-gpl --enable-version3` (libx264, libx265, libsrt, …): **GPL-3.0-or-later**. Source: https://ffmpeg.org and https://www.gyan.dev/ffmpeg/builds/ |

## Notes

* FFmpeg runs as a **separate executable** (subprocess); it is not linked into the
  app. The app only **decodes** video at runtime. The self-test also uses FFmpeg
  to encode a tiny local clip and a local SRT test stream to verify decoding.
* PyAV is no longer used: its wheels are built without libsrt, so they cannot
  open the Pi's existing SRT stream.
* If you redistribute the installer to others, comply with the GPL/LGPL terms of
  the components above (provide license texts and the corresponding source or a
  written offer). For personal use on your own laptop no action is required.
* On the Raspberry Pi, the optional MediaMTX server (MIT license) is installed
  separately by `pi/deploy/install_mediamtx.sh`; it is not part of the Windows
  installer.
* Inno Setup (used to build Setup.exe) is not redistributed inside the app.
