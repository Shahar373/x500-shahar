"""Redaction of credentials and secrets before anything is written out."""

from __future__ import annotations

import re
from typing import Any

SECRET_KEYS = re.compile(
    r"(^|[_-])(password|passwd|passphrase|secret|token|api[_-]?key|auth[_-]?key|private[_-]?key|credentials?)($|[_-])",
    re.I)
PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED PRIVATE KEY]"),
    (re.compile(r"tskey-[A-Za-z0-9_-]+"), "tskey-[REDACTED]"),
    (re.compile(r"(?i)(://)([^/@\s:]+):([^/@\s]+)@"), r"\1\2:***@"),
    (re.compile(r"(?i)\b(password|passwd|passphrase|token|secret)\s*[=:]\s*\S+"), r"\1=***"),
]
REDACTED = "***"


def redact_text(text: str) -> str:
    for pat, repl in PATTERNS:
        text = pat.sub(repl, text)
    return text


def redact(obj: Any, extra_secrets: list[str] | None = None, _depth: int = 0) -> Any:
    """Return a deep copy with secret-looking keys blanked and strings scrubbed.

    ``extra_secrets`` are literal values (e.g. the stored SSH password) that are
    replaced wherever they appear.
    """
    secrets = [s for s in (extra_secrets or []) if s and len(s) >= 4]
    if _depth > 20:
        return "[TRUNCATED]"
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and SECRET_KEYS.search(k) and v not in (None, "", False):
                out[k] = REDACTED
            else:
                out[k] = redact(v, secrets, _depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(v, secrets, _depth + 1) for v in obj]
    if isinstance(obj, str):
        s = redact_text(obj)
        for secret in secrets:
            s = s.replace(secret, REDACTED)
        return s
    return obj
