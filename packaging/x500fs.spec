# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for X500 Flight Station (one-folder build; Inno Setup wraps it).
#   pyinstaller --noconfirm --clean packaging/x500fs.spec
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

datas = collect_data_files("x500fs", includes=["profiles/*.json", "assets/*"])
# The Pi-side scripts are streamed to the Pi as source, so they must ship as files.
datas += collect_data_files("x500fs", include_py_files=True, includes=["pi_side/x500fs_*.py"])
datas += [(os.path.join(ROOT, "docs", "THIRD_PARTY_NOTICES.md"), ".")]
# FFmpeg executable (gyan.dev 7.1 essentials on Windows, built with --enable-libsrt) from the
# hash-pinned imageio-ffmpeg wheel. The pyinstaller-hooks-contrib hook does the same; explicit here.
datas += collect_data_files("imageio_ffmpeg", subdir="binaries")

hiddenimports = collect_submodules("x500fs") + [
    "pymavlink.dialects.v20.ardupilotmega",
    "keyring.backends.Windows",
    "imageio_ffmpeg.binaries",
]

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[SRC],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter", "matplotlib", "numpy", "PIL", "IPython", "pytest",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQuick", "PySide6.QtQml",
        "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtPdf",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="X500FlightStation",
    console=False,
    icon=os.path.join(SRC, "x500fs", "assets", "app.ico"),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="X500FlightStation", upx=False)
