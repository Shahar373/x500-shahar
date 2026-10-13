"""Scripts that run on the Raspberry Pi.

They are stdlib-only and are streamed over SSH to ``python3 -`` (nothing is
installed on the Pi), or optionally installed explicitly with
``pi/deploy/install_collector.sh``.
"""

from importlib import resources


def script_source(name: str) -> bytes:
    """Return the bytes of a Pi-side script shipped inside the package."""
    return resources.files(__package__).joinpath(name).read_bytes()
