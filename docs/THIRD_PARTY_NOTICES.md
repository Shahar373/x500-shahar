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
| PyAV (`av`) | video decoding | BSD-3-Clause |
| FFmpeg 9.x libraries (shipped inside the PyAV wheel, `av.libs`) | RTSP + H.264 decoding | FFmpeg itself reports "LGPL version 3 or later"; **the PyAV wheel build also enables libx264/libx265 (GPL-2.0-or-later)**. Treat the bundled FFmpeg as GPL for redistribution purposes. |

## Notes

* The app only **decodes** video. The x264/x265 encoders are present in the
  PyAV wheel but are not used at runtime (except by the self-test, which encodes
  a tiny local clip to verify decoding).
* If you redistribute the installer to others, comply with the GPL/LGPL terms of
  the components above (provide license texts and the corresponding source or a
  written offer). For personal use on your own laptop no action is required.
* On the Raspberry Pi, the optional MediaMTX server (MIT license) is installed
  separately by `pi/deploy/install_mediamtx.sh`; it is not part of the Windows
  installer.
* Inno Setup (used to build Setup.exe) is not redistributed inside the app.
