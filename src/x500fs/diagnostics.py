"""Explicit diagnostics export (user action only).

Runs the read-only ``x500fs_diag.py`` on the Pi over the verified SSH session
(streamed - nothing is installed or written on the Pi), adds the latest
collector document and the session log, removes secrets and masks private
details, and saves one JSON file on the laptop.
"""

from __future__ import annotations

import json
import shlex
import time
from pathlib import Path

from . import __version__
from .core.redact import redact
from .pi_side import script_source

SCHEMA = "x500fs.diagnostics/1"


def build_bundle(pi_diag: dict | None, pi_error: str | None, collector_doc: dict | None, events: list[dict],
                 interruptions: list[dict], settings_summary: dict, simulated: bool, extra_secrets=None,
                 private: bool = True) -> dict:
    doc = {
        "schema": SCHEMA,
        "app_version": __version__,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "simulation": simulated,
        "settings": settings_summary,
        "pi_diag": pi_diag,
        "pi_diag_error": pi_error,
        "collector": collector_doc,
        "session_events": events[-300:],
        "interruptions": interruptions,
        "redaction": {"secrets": True, "private_details": private},
    }
    return redact(doc, extra_secrets, private=private)


def run_pi_diag(ssh, python: str = "python3", journal_lines: int = 40, timeout_s: float = 90.0) -> dict:
    cmd = f"{shlex.quote(python)} -I - --journal-lines {int(journal_lines)}"
    res = ssh.exec(cmd, stdin=script_source("x500fs_diag.py"), timeout=timeout_s)
    if res.exit_code != 0:
        raise RuntimeError(f"diag exit {res.exit_code}: {res.stderr.decode('utf-8', 'replace')[-300:]}")
    return json.loads(res.stdout.decode("utf-8", "replace"))


def save_bundle(doc: dict, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    tag = "-SIMULATION" if doc.get("simulation") else ""
    path = directory / f"x500fs-diagnostics-{time.strftime('%Y%m%d-%H%M%S')}{tag}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str), "utf-8")
    return path
