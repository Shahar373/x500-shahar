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
    assert "profile flight: expected_params.rtl: 1" in joined
    local_config.apply(plan, store)
    s2 = store.load()
    assert s2.connection.username == "pilot1" and s2.pi.zte_gateway == "10.9.8.1"
    assert s2.mavlink.source_sysid == 240 and s2.video.srt_port == 5600
    assert s2.pi.services["extra"] == "x500-extra" and s2.pi.services["mavlink_router"] == "mavlink-router"
    prof, err = profile_mod.load("flight", store.profiles_dir)
    assert err is None and prof.battery["cells"] == 4 and prof.battery["capacity_mah"] == 5200
    assert prof.expected_params["rtl"] == {"RTL_ALT": {"min": 1}}
    assert profile_mod.load("bench", store.profiles_dir)[0].expected_params.get("rtl") in ({}, None)
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


def test_connect_refuses_unusable_auth_with_clear_message(tmp_path, monkeypatch):
    from x500fs import credentials
    from x500fs.controller import Controller, Listener

    class Rec(Listener):
        def __init__(self):
            self.notices = []

        def on_notice(self, level, text):
            self.notices.append((level, text))

    store = SettingsStore(tmp_path / "cfg")
    s = store.load()
    s.connection.host, s.connection.username, s.connection.auth_method, s.connection.key_path = \
        "x500pi", "pilot1", "key", ""
    store.save(s)
    rec = Rec()
    ctl = Controller(store, rec)
    ctl.connect()
    assert not ctl.connected and "קובץ מפתח" in rec.notices[-1][1]
    ctl.settings.connection.auth_method = "password"
    monkeypatch.setattr(credentials, "get", lambda ident: None)
    ctl.connect()
    assert not ctl.connected and "סיסמה" in rec.notices[-1][1]


def test_settings_dialog_fits_small_screens(tmp_path):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from x500fs.ui.app import create_application
    from x500fs.ui.dialogs import SettingsDialog

    app = create_application()
    store = SettingsStore(tmp_path / "cfg")
    d = SettingsDialog(store.load(), str(store.dir), str(store.profiles_dir))
    assert d.minimumSizeHint().height() < 600     # tabs scroll instead of growing the dialog
    avail = app.primaryScreen().availableGeometry()
    assert d.height() <= avail.height()
    d.close()


def test_expected_params_and_arming_exemption_import(tmp_path):
    """The user approves the values read from the FC: they become expectations, the FC is not touched."""
    from helpers import World

    from x500fs.core.status import Status

    doc = {"schema": local_config.SCHEMA, "profiles": {"bench": {
        "expected_params": {"failsafe": {"FS_GCS_ENABLE": {"eq": 5}, "BATT_LOW_VOLT": {"eq": 14.4}},
                            "fence": {"FENCE_RADIUS": {"eq": 50}}, "arming": {"ARMING_CHECK": {"eq": 1}}},
        "checks": {"fc.arming_checks": {"allowed_skipped": ["RC"]}, "pi.storage": {"warn_free_mb": 1}}}}}
    store = SettingsStore(tmp_path / "cfg")
    plan = local_config.plan(local_config.read(_write(tmp_path, doc)), store.load(), store.profiles_dir)
    ign = "\n".join(plan.ignored)
    assert "expected_params.arming" in ign and "pi.storage" in ign     # only the whitelisted parts
    local_config.apply(plan, store)
    prof, err = profile_mod.load("bench", store.profiles_dir)
    assert err is None
    assert prof.expected_params["failsafe"]["FS_GCS_ENABLE"] == {"eq": 5}
    assert prof.check("fc.arming_checks")["allowed_skipped"] == ["RC"]
    assert prof.check("pi.storage")["warn_free_mb"] == 2048
    # evaluated against the float values ArduPilot actually reports
    w = World().healthy(params={"ARMING_SKIPCHK": 64, "FS_GCS_ENABLE": 5, "BATT_LOW_VOLT": 14.399999618530273,
                                "FENCE_RADIUS": 50.0})
    w.profile = prof
    res, _ = w.evaluate()
    assert res["fc.arming_checks"].status is Status.WARN                 # skipped, but explicitly allowed
    assert res["cfg.failsafe"].status is Status.PASS and res["cfg.fence"].status is Status.PASS
    w.put("mav.param.FS_GCS_ENABLE", {"value": 1})
    res, _ = w.evaluate()
    assert res["cfg.failsafe"].status is Status.FAIL


def test_unknown_arming_name_is_rejected(tmp_path):
    doc = {"schema": local_config.SCHEMA,
           "profiles": {"bench": {"checks": {"fc.arming_checks": {"allowed_skipped": ["RADIO"]}}}}}
    store = SettingsStore(tmp_path / "cfg")
    with pytest.raises(local_config.LocalConfigError, match="RADIO"):
        local_config.plan(local_config.read(_write(tmp_path, doc)), store.load(), store.profiles_dir)
