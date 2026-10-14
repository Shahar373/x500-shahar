"""Redaction of credentials and secrets before anything is written out."""

from __future__ import annotations

import ipaddress
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

# Private (not secret) details that should not leave the laptop in a shared
# report or diagnostics bundle: addresses, hardware serials, account names, SSIDs.
KEEP_IPV4 = {"127.0.0.1", "0.0.0.0", "1.1.1.1", "8.8.8.8", "9.9.9.9", "255.255.255.255"}
_IPV4 = re.compile(r"(?<![\d.])((?:25[0-5]|2[0-4]\d|1?\d?\d)\.(?:25[0-5]|2[0-4]\d|1?\d?\d))"
                   r"\.((?:25[0-5]|2[0-4]\d|1?\d?\d)\.(?:25[0-5]|2[0-4]\d|1?\d?\d))(?![\d.])")
_IPV6 = re.compile(r"(?<![\w:])((?:[0-9a-f]{1,4}:){2,7}[0-9a-f]{0,4}|(?:[0-9a-f]{1,4}:){1,6}:[0-9a-f]{1,4})(?![\w:*])",
                   re.I)
PRIVATE_PATTERNS = [
    (re.compile(r"\b([0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2})(?::[0-9a-f]{2}){3}\b", re.I), r"\1:**:**:**"),
    (re.compile(r"(usb-[\w.-]*?_)([0-9A-Fa-f]{8,})(-if\d+)"), r"\1***\3"),
    (re.compile(r"(ID_SERIAL(?:_SHORT)?=)\S+"), r"\1***"),
    (re.compile(r"(/home/)[^/\s\"']+"), r"\1<user>"),
    (re.compile(r"(?i)(\bSSID:[ \t]*)\S[^\n]*"), r"\1***"),
    (re.compile(r"(?i)(\bssid=)\S+"), r"\1***"),
]
PRIVATE_KEYS = re.compile(r"(?i)^(ssid|essid|bssid|mac|mac_address|serial|serial_number|uid)$")


def _mask_ipv4(m: re.Match) -> str:
    ip = m.group(0)
    if ip in KEEP_IPV4 or ip.startswith("127."):
        return ip
    return f"{m.group(1)}.*.*"


def _mask_ipv6(m: re.Match) -> str:
    ip = m.group(0)
    try:
        addr = ipaddress.IPv6Address(ip)  # rejects clock times (10:00:00) and MAC fragments
    except ValueError:
        return ip
    if addr.is_loopback or addr.is_unspecified:
        return ip
    head = ip.split(":")[0]
    return f"{head}:****" if head else "::****"


def redact_private_text(text: str) -> str:
    for pat, repl in PRIVATE_PATTERNS:
        text = pat.sub(repl, text)
    text = _IPV4.sub(_mask_ipv4, text)
    text = _IPV6.sub(_mask_ipv6, text)
    return text


def redact_text(text: str, private: bool = False) -> str:
    for pat, repl in PATTERNS:
        text = pat.sub(repl, text)
    return redact_private_text(text) if private else text


def redact(obj: Any, extra_secrets: list[str] | None = None, _depth: int = 0, private: bool = False) -> Any:
    """Return a deep copy with secret-looking keys blanked and strings scrubbed.

    ``extra_secrets`` are literal values (e.g. the stored SSH password) that are
    replaced wherever they appear. ``private=True`` also masks addresses, MACs,
    USB serial numbers, home-directory account names and Wi-Fi network names.
    """
    secrets = [s for s in (extra_secrets or []) if s and len(s) >= 4]
    if _depth > 20:
        return "[TRUNCATED]"
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and SECRET_KEYS.search(k) and v not in (None, "", False):
                out[k] = REDACTED
            elif private and isinstance(k, str) and PRIVATE_KEYS.match(k) and isinstance(v, str) and v:
                out[k] = REDACTED
            else:
                out[k] = redact(v, secrets, _depth + 1, private)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(v, secrets, _depth + 1, private) for v in obj]
    if isinstance(obj, str):
        s = redact_text(obj, private)
        for secret in secrets:
            s = s.replace(secret, REDACTED)
        return s
    return obj
