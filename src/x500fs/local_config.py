"""Import site-specific settings from a local JSON file (explicit user action).

Connection details (addresses, user names, paths on the Pi) belong on the user's
computer, not in the repository. This module lets the user keep them in one
local file and import it into the app's settings (``%APPDATA%``)::

    {
      "schema": "x500fs.local-config/1",
      "settings": {"connection": {"host": "x500pi", "username": "..."},
                   "pi": {"zte_gateway": "...", "ground_ip": "..."}},
      "profiles": {"bench": {"battery": {"chemistry": "LiPo", "cells": 4, "capacity_mah": 5200}}}
    }

* Only keys that exist in the settings schema are merged; anything else is
  listed as ignored. Values keep the types of the schema.
* Profiles: only the ``battery`` section of a profile can be imported, and the
  result is validated like any profile before it is written.
* A file that contains anything that looks like a password, passphrase, token or
  key is rejected as a whole - secrets live only in Windows Credential Manager.
* Nothing is sent anywhere and nothing on the Pi or the flight controller changes.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .core import profile as profile_mod
from .core.redact import SECRET_KEYS
from .settings import Settings, SettingsStore

SCHEMA = "x500fs.local-config/1"
PROFILE_BATTERY_KEYS = {"chemistry", "cells", "capacity_mah", "preflight_min_cell_v"}


class LocalConfigError(ValueError):
    pass


@dataclass
class ImportPlan:
    settings: Settings
    profiles: dict[str, dict] = field(default_factory=dict)      # profile id -> full document to write
    changes: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)


def _secret_paths(obj: Any, path: str = "") -> list[str]:
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            if isinstance(k, str) and SECRET_KEYS.search(k):
                out.append(p)
            out += _secret_paths(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += _secret_paths(v, f"{path}[{i}]")
    return out


def read(path: str | Path) -> dict:
    try:
        doc = json.loads(Path(path).read_text("utf-8-sig"))
    except OSError as e:
        raise LocalConfigError(f"לא ניתן לקרוא את הקובץ: {e}") from e
    except json.JSONDecodeError as e:
        raise LocalConfigError(f"הקובץ אינו JSON תקין (שורה {e.lineno}): {e.msg}") from e
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise LocalConfigError(f"זה לא קובץ הגדרות מקומי של האפליקציה (schema צריך להיות {SCHEMA}).")
    secrets = _secret_paths(doc)
    if secrets:
        raise LocalConfigError("הקובץ מכיל שדות שנראים כמו סיסמה או מפתח (" + ", ".join(secrets[:5]) + "). "
                               "סיסמאות נשמרות רק במנהל האישורים של Windows, לא בקובץ. הייבוא בוטל.")
    return doc


def _merge(base: dict, extra: dict, path: str, ignored: list[str]) -> dict:
    out = copy.deepcopy(base)
    for k, v in extra.items():
        p = f"{path}.{k}" if path else k
        if k not in base:
            # dict-valued settings (e.g. services) accept new keys; dataclass sections do not
            if path and isinstance(base, dict) and _is_open_dict(path):
                out[k] = v
            else:
                ignored.append(p)
            continue
        if isinstance(base[k], dict) and isinstance(v, dict):
            out[k] = _merge(base[k], v, p, ignored)
        else:
            out[k] = v
    return out


def _is_open_dict(path: str) -> bool:
    return path in ("pi.services", "video.srt_profiles")


def _flatten(d: Any, path: str = "") -> dict[str, Any]:
    if isinstance(d, dict):
        out = {}
        for k, v in d.items():
            out.update(_flatten(v, f"{path}.{k}" if path else str(k)))
        return out
    return {path: d}


def plan(doc: dict, current: Settings, profiles_dir: Path | None) -> ImportPlan:
    ignored: list[str] = []
    before = current.to_dict()
    merged = _merge(before, doc.get("settings") or {}, "", ignored)
    new = Settings.from_dict(merged)
    after = new.to_dict()
    fb, fa = _flatten(before), _flatten(after)
    changes = [f"{k}: {fb.get(k)!r} → {fa[k]!r}" for k in sorted(fa) if fb.get(k) != fa[k]]
    # values that were present but rejected by the schema's types
    for k, v in _flatten(doc.get("settings") or {}).items():
        if k in fa and fa[k] != v and not any(k.startswith(i) for i in ignored):
            ignored.append(f"{k} (סוג לא תואם: {v!r})")
    result = ImportPlan(settings=new, changes=changes, ignored=ignored)
    for pid, pdoc in (doc.get("profiles") or {}).items():
        if not isinstance(pdoc, dict):
            ignored.append(f"profiles.{pid}")
            continue
        for k in pdoc:
            if k != "battery":
                ignored.append(f"profiles.{pid}.{k} (רק battery ניתן לייבוא)")
        bat = pdoc.get("battery")
        if not isinstance(bat, dict):
            continue
        user_file = profiles_dir / f"{pid}.json" if profiles_dir else None
        if user_file and user_file.exists():
            base = json.loads(user_file.read_text("utf-8"))
        elif pid in profile_mod.BUILTIN_IDS:
            base = profile_mod.builtin_doc(pid)
        else:
            ignored.append(f"profiles.{pid} (פרופיל לא קיים)")
            continue
        newdoc = copy.deepcopy(base)
        newdoc.setdefault("battery", {})
        for k, v in bat.items():
            if k not in PROFILE_BATTERY_KEYS:
                ignored.append(f"profiles.{pid}.battery.{k}")
                continue
            if newdoc["battery"].get(k) != v:
                result.changes.append(f"profile {pid}: battery.{k}: {newdoc['battery'].get(k)!r} → {v!r}")
            newdoc["battery"][k] = v
        try:
            profile_mod.from_dict(newdoc, f"import:{pid}")
        except profile_mod.ProfileError as e:
            raise LocalConfigError(f"ערכי הסוללה לפרופיל {pid} אינם תקינים: {e}") from e
        result.profiles[pid] = newdoc
    return result


def apply(p: ImportPlan, store: SettingsStore) -> None:
    store.save(p.settings)
    if p.profiles:
        store.profiles_dir.mkdir(parents=True, exist_ok=True)
        for pid, doc in p.profiles.items():
            target = store.profiles_dir / f"{pid}.json"
            if target.exists():
                target.with_suffix(".before-import.json").write_text(target.read_text("utf-8"), "utf-8")
            target.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", "utf-8")
