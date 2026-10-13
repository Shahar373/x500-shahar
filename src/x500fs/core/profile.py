"""Operating profiles (bench / flight) - the inspectable check policy.

A profile is a JSON document. Each check id maps to a config dict with at
least ``enabled`` and ``critical``; other keys are thresholds. A threshold of
``null`` means "not configured" - the check then reports UNKNOWN (and, when
critical, blocks an overall pass). A threshold given as ``"fc:PARAM"`` takes
its value from the flight controller's own parameter (e.g. GPS_HDOP_GOOD),
which is how ArduPilot itself judges the same condition.

Built-in profiles live in ``x500fs/profiles``. Editable copies are placed in
the user's config directory on first run; user copies take precedence.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

SCHEMA = "x500fs.profile/1"
MODES = ("bench", "flight")
BUILTIN_IDS = ("bench", "flight")

_BOOL_KEYS = ("enabled", "critical")


class ProfileError(ValueError):
    pass


@dataclass
class Profile:
    id: str
    name: str
    mode: str
    description: str
    checks: dict[str, dict[str, Any]]
    battery: dict[str, Any]
    expected_params: dict[str, dict[str, Any]]
    video_required: bool = False
    source: str = "builtin"
    sha256: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def check(self, check_id: str) -> dict[str, Any]:
        cfg = self.checks.get(check_id)
        if cfg is None:
            # Unknown to this profile: evaluate, but nothing is configured.
            return {"enabled": True, "critical": False, "_missing_from_profile": True}
        return cfg

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "mode": self.mode,
            "source": self.source,
            "sha256": self.sha256,
            "video_required": self.video_required,
        }


def _validate(doc: Any, origin: str) -> list[str]:
    errs: list[str] = []
    if not isinstance(doc, dict):
        return [f"{origin}: profile must be a JSON object"]
    if doc.get("schema") != SCHEMA:
        errs.append(f"{origin}: schema must be {SCHEMA!r}")
    for key in ("id", "name", "mode"):
        if not isinstance(doc.get(key), str) or not doc.get(key):
            errs.append(f"{origin}: '{key}' must be a non-empty string")
    if doc.get("mode") not in MODES:
        errs.append(f"{origin}: 'mode' must be one of {MODES}")
    checks = doc.get("checks")
    if not isinstance(checks, dict):
        errs.append(f"{origin}: 'checks' must be an object")
    else:
        for cid, cfg in checks.items():
            if not isinstance(cfg, dict):
                errs.append(f"{origin}: checks.{cid} must be an object")
                continue
            for b in _BOOL_KEYS:
                if b in cfg and not isinstance(cfg[b], bool):
                    errs.append(f"{origin}: checks.{cid}.{b} must be true/false")
            for k, v in cfg.items():
                if k in _BOOL_KEYS or k.startswith("_") or k == "rationale":
                    continue
                if not _valid_threshold(v):
                    errs.append(f"{origin}: checks.{cid}.{k} has unsupported value {v!r}")
    battery = doc.get("battery", {})
    if not isinstance(battery, dict):
        errs.append(f"{origin}: 'battery' must be an object")
    else:
        cells = battery.get("cells")
        if cells is not None and (not isinstance(cells, int) or isinstance(cells, bool) or not 1 <= cells <= 14):
            errs.append(f"{origin}: battery.cells must be null or an integer 1..14")
        chem = battery.get("chemistry")
        if chem is not None and chem not in ("LiPo", "LiPoHV", "LiIon"):
            errs.append(f"{origin}: battery.chemistry must be null, LiPo, LiPoHV or LiIon")
    exp = doc.get("expected_params", {})
    if not isinstance(exp, dict):
        errs.append(f"{origin}: 'expected_params' must be an object")
    else:
        for group, rules in exp.items():
            if not isinstance(rules, dict):
                errs.append(f"{origin}: expected_params.{group} must be an object")
                continue
            for pname, rule in rules.items():
                if not isinstance(pname, str) or not re.fullmatch(r"[A-Z0-9_]{1,16}", pname):
                    errs.append(f"{origin}: expected_params.{group}: bad parameter name {pname!r}")
                err = rule_error(rule)
                if err:
                    errs.append(f"{origin}: expected_params.{group}.{pname}: {err}")
    return errs


def _valid_threshold(v: Any) -> bool:
    if v is None or isinstance(v, (bool, int, float)):
        return True
    if isinstance(v, str):
        return True  # "fc:PARAM", "auto", names
    if isinstance(v, list):
        return all(isinstance(x, (str, int, float)) for x in v)
    if isinstance(v, dict):
        return all(_valid_threshold(x) for x in v.values())
    return False


_RULE_OPS = {"eq", "ne", "min", "max", "in", "not_in", "bits_set", "bits_clear"}


def rule_error(rule: Any) -> str | None:
    if not isinstance(rule, dict) or not rule:
        return "rule must be a non-empty object such as {\"eq\": 1}"
    unknown = set(rule) - _RULE_OPS - {"note"}
    if unknown:
        return f"unknown operator(s) {sorted(unknown)}"
    for op, v in rule.items():
        if op == "note":
            continue
        if op in ("in", "not_in"):
            if not isinstance(v, list) or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
                return f"'{op}' needs a list of numbers"
        elif not isinstance(v, (int, float)) or isinstance(v, bool):
            return f"'{op}' needs a number"
    return None


def rule_matches(rule: dict, value: float) -> bool:
    for op, v in rule.items():
        if op == "note":
            continue
        if op == "eq" and not _feq(value, v):
            return False
        if op == "ne" and _feq(value, v):
            return False
        if op == "min" and value < v:
            return False
        if op == "max" and value > v:
            return False
        if op == "in" and not any(_feq(value, x) for x in v):
            return False
        if op == "not_in" and any(_feq(value, x) for x in v):
            return False
        if op == "bits_set" and (int(value) & int(v)) != int(v):
            return False
        if op == "bits_clear" and (int(value) & int(v)) != 0:
            return False
    return True


def rule_text(rule: dict) -> str:
    parts = []
    for op, v in rule.items():
        if op == "note":
            continue
        sym = {"eq": "=", "ne": "≠", "min": "≥", "max": "≤", "in": "∈", "not_in": "∉",
               "bits_set": "bits set", "bits_clear": "bits clear"}[op]
        parts.append(f"{sym} {v}")
    return " ו-".join(parts)


def _feq(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) <= 1e-6 * max(1.0, abs(float(b)))


def from_dict(doc: dict, source: str) -> Profile:
    errs = _validate(doc, source)
    if errs:
        raise ProfileError("\n".join(errs))
    canonical = json.dumps(doc, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return Profile(
        id=doc["id"],
        name=doc["name"],
        mode=doc["mode"],
        description=doc.get("description", ""),
        checks=copy.deepcopy(doc["checks"]),
        battery=copy.deepcopy(doc.get("battery", {})),
        expected_params=copy.deepcopy(doc.get("expected_params", {})),
        video_required=bool(doc.get("video_required", False)),
        source=source,
        sha256=hashlib.sha256(canonical).hexdigest(),
        raw=copy.deepcopy(doc),
    )


def builtin_doc(profile_id: str) -> dict:
    text = resources.files("x500fs.profiles").joinpath(f"{profile_id}.json").read_text("utf-8")
    return json.loads(text)


def load_builtin(profile_id: str) -> Profile:
    return from_dict(builtin_doc(profile_id), f"builtin:{profile_id}")


def load_file(path: Path) -> Profile:
    try:
        doc = json.loads(Path(path).read_text("utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        raise ProfileError(f"{path}: cannot read: {e}") from e
    except json.JSONDecodeError as e:
        raise ProfileError(f"{path}: invalid JSON at line {e.lineno}: {e.msg}") from e
    return from_dict(doc, str(path))


def ensure_user_profiles(profile_dir: Path) -> None:
    """Copy built-in profiles to the user dir if they are not there yet."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    for pid in BUILTIN_IDS:
        target = profile_dir / f"{pid}.json"
        if not target.exists():
            target.write_text(
                json.dumps(builtin_doc(pid), ensure_ascii=False, indent=2) + "\n", "utf-8"
            )


def list_profiles(profile_dir: Path | None) -> list[str]:
    ids = list(BUILTIN_IDS)
    if profile_dir and profile_dir.is_dir():
        for p in sorted(profile_dir.glob("*.json")):
            if p.stem not in ids:
                ids.append(p.stem)
    return ids


def load(profile_id: str, profile_dir: Path | None) -> tuple[Profile, str | None]:
    """Load a profile, preferring the user's copy.

    Returns (profile, error). If the user's copy is invalid the built-in
    profile of the same id is returned together with an error message that the
    UI must display prominently - the policy in force must never change
    silently.
    """
    if profile_dir:
        path = profile_dir / f"{profile_id}.json"
        if path.exists():
            try:
                return load_file(path), None
            except ProfileError as e:
                if profile_id in BUILTIN_IDS:
                    return load_builtin(profile_id), str(e)
                return load_builtin("bench"), str(e)
    if profile_id in BUILTIN_IDS:
        return load_builtin(profile_id), None
    return load_builtin("bench"), f"profile {profile_id!r} not found"
