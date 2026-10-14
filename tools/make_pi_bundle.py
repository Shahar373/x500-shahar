"""Build the explicit Pi deployment bundle (no network, no Pi access).

    python tools/make_pi_bundle.py            # -> dist/x500-pi-bundle-<version>.tar.gz

Copy the archive to the Pi, unpack it, read pi/x500/README.md, then run
``sudo bash deploy_x500.sh`` (dry run) and ``sudo bash deploy_x500.sh --apply``.
"""

from __future__ import annotations

import hashlib
import io
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

LIB_FILES = ["x500fs/__init__.py", "x500fs/pi_side/__init__.py", "x500fs/pi_side/x500fs_collector.py",
             "x500fs/pi_side/x500fs_diag.py", "x500fs/transport/__init__.py", "x500fs/transport/mav_ingest.py",
             "x500fs/transport/mavlink_client.py", "x500fs/transport/streams.py"]


def members() -> list[tuple[str, Path, int]]:
    out = []
    for p in sorted((ROOT / "pi" / "x500").rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            mode = 0o755 if p.parent.name == "bin" else 0o644
            out.append((f"x500/{p.relative_to(ROOT / 'pi' / 'x500').as_posix()}", p, mode))
    for name in ("deploy_x500.sh", "install_video_sudoers.sh"):
        out.append((name, ROOT / "pi" / "deploy" / name, 0o755))
    rels = list(LIB_FILES)
    for sub in ("core", "pi_tools"):
        rels += [p.relative_to(SRC).as_posix() for p in sorted((SRC / "x500fs" / sub).rglob("*.py"))]
    rels += [p.relative_to(SRC).as_posix() for p in sorted((SRC / "x500fs" / "profiles").glob("*.json"))]
    for rel in rels:
        out.append((f"lib/{rel}", SRC / rel, 0o644))
    return out


def build(dest_dir: Path) -> Path:
    from x500fs import __version__

    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"x500-pi-bundle-{__version__}.tar.gz"
    top = f"x500-pi-bundle-{__version__}"
    sums = []
    with tarfile.open(out, "w:gz") as tar:
        for arc, path, mode in members():
            data = path.read_bytes()
            sums.append(f"{hashlib.sha256(data).hexdigest()}  {arc}")
            info = tarfile.TarInfo(f"{top}/{arc}")
            info.size, info.mode, info.mtime = len(data), mode, int(path.stat().st_mtime)
            tar.addfile(info, io.BytesIO(data))
        man = ("\n".join(sums) + "\n").encode()
        info = tarfile.TarInfo(f"{top}/SHA256SUMS")
        info.size, info.mode = len(man), 0o644
        tar.addfile(info, io.BytesIO(man))
    return out


if __name__ == "__main__":
    print(build(ROOT / "dist"))
