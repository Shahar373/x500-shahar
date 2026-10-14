"""Importing site-specific settings from a local file (never from the repository)."""

from __future__ import annotations

import json

import pytest

from x500fs import local_config
from x500fs.core import profile as profile_mod
from x500fs.settings import SettingsStore

DOC = {
    "schema": local_config.SCHEMA,
    "settings": {
        "profile_id": "bench",
        "connection": {"host": "x500pi", "username": "pilot1", "expected_hostname": "x500pi", "bogus": 1},
        "pi": {"zte_gateway": "10.9.8.1", "ground_ip": "100.99.1.2", "legacy_python": "/home/pilot1/venv/bin/python",
               "services": {"extra": "x500-extra"}},
        "video": {"srt_profile": "degraded", "srt_port": "5600"},
        "unknown_section": {"a": 1},
    },
    "profiles": {
        "bench": {"battery": {"chemistry": "LiPo", "cells": 4, "capacity_mah": 5200}},
        "flight": {"battery": {"chemistry": "LiPo", "cells": 4, "capacity_mah": 5200},
                   "expected_params": {"rtl": {"RTL_ALT": {"min": 1}}}},
    },
}


def _write(tmp_path, doc):
    p = tmp_path / "x500-local-config.json"
    p.write_text(json.dumps(doc), "utf-8")
    return p


def test_plan_and_apply_merges_known_fields_only(tmp_path):
    store = SettingsStore(tmp_path / "cfg")
    s = store.load()
    s.mavlink.source_sysid = 240           # user's own choice must survive the import
    store.save(s)
    profile_mod.ensure_user_profiles(store.profiles_dir)
    plan = local_config.plan(local_config.read(_write(tmp_path, DOC)), store.load(), store.profiles_dir)
    joined = "\n".join(plan.changes)
    assert "connection.username: '' → 'pilot1'" in joined
    assert "pi.zte_gateway" in joined and "pi.ground_ip" in joined
    assert "profile flight: battery.cells: None → 4" in joined
    ign = "\n".join(plan.ignored)
    assert "connection.bogus" in ign and "unknown_section" in ign
    assert "video.srt_port" in ign                         # wrong type ("5600" string) is not imported
    assert "expected_params" in ign                        # failsafe/RTL expectations are not importable
    local_config.apply(plan, store)
    s2 = store.load()
    assert s2.connection.username == "pilot1" and s2.pi.zte_gateway == "10.9.8.1"
    assert s2.mavlink.source_sysid == 240 and s2.video.srt_port == 5600
    assert s2.pi.services["extra"] == "x500-extra" and s2.pi.services["mavlink_router"] == "mavlink-router"
    prof, err = profile_mod.load("flight", store.profiles_dir)
    assert err is None and prof.battery["cells"] == 4 and prof.battery["capacity_mah"] == 5200
    assert prof.expected_params.get("rtl") in ({}, None)   # still unconfigured
    assert (store.profiles_dir / "flight.before-import.json").exists()


def test_secrets_are_rejected(tmp_path):
    doc = json.loads(json.dumps(DOC))
    doc["settings"]["connection"]["password"] = "hunter2"
    with pytest.raises(local_config.LocalConfigError, match="סיסמה"):
        local_config.read(_write(tmp_path, doc))
    doc = json.loads(json.dumps(DOC))
    doc["settings"]["pi"]["sudo_passphrase"] = "x"
    with pytest.raises(local_config.LocalConfigError):
        local_config.read(_write(tmp_path, doc))


def test_wrong_schema_and_invalid_battery(tmp_path):
    with pytest.raises(local_config.LocalConfigError):
        local_config.read(_write(tmp_path, {"settings": {}}))
    doc = json.loads(json.dumps(DOC))
    doc["profiles"]["bench"]["battery"]["cells"] = 40
    store = SettingsStore(tmp_path / "cfg")
    with pytest.raises(local_config.LocalConfigError, match="bench"):
        local_config.plan(local_config.read(_write(tmp_path, doc)), store.load(), store.profiles_dir)


def test_controller_import_requires_disconnect(tmp_path):
    from x500fs.controller import Controller

    store = SettingsStore(tmp_path / "cfg")
    ctl = Controller(store)
    plan = ctl.plan_local_config(_write(tmp_path, DOC))
    ctl.connected = True
    with pytest.raises(RuntimeError):
        ctl.apply_local_config(plan)
    ctl.connected = False
    ctl.apply_local_config(plan)
    assert ctl.settings.connection.username == "pilot1"
    assert ctl.profile.battery["chemistry"] == "LiPo"


def test_local_config_files_are_gitignored():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / ".gitignore").read_text()
    assert "x500-local-config*.json" in text and "local/" in text
