"""Profiles: validation, fallback; settings persistence and secrets."""

from __future__ import annotations

import json

import pytest

from x500fs.core import profile as P
from x500fs.settings import Settings, SettingsStore


def test_builtin_profiles_valid_and_hashed():
    for pid in P.BUILTIN_IDS:
        p = P.load_builtin(pid)
        assert p.mode == pid and len(p.sha256) == 64


def test_validation_errors():
    doc = P.builtin_doc("bench")
    doc["checks"]["pi.temperature"]["enabled"] = "yes"
    doc["battery"]["cells"] = 99
    doc["expected_params"]["failsafe"] = {"fs_thr": {"eq": 1}, "FS_GCS_ENABLE": {"approx": 1}}
    with pytest.raises(P.ProfileError) as ei:
        P.from_dict(doc, "t")
    msg = str(ei.value)
    assert "enabled" in msg and "cells" in msg and "fs_thr" in msg and "approx" in msg


def test_invalid_user_profile_falls_back_with_error(tmp_path):
    d = tmp_path / "profiles"
    P.ensure_user_profiles(d)
    (d / "flight.json").write_text("{ not json", "utf-8")
    prof, err = P.load("flight", d)
    assert prof.source == "builtin:flight"
    assert err and "invalid JSON" in err


def test_user_profile_overrides_builtin(tmp_path):
    d = tmp_path / "profiles"
    P.ensure_user_profiles(d)
    doc = json.loads((d / "bench.json").read_text("utf-8"))
    doc["battery"]["cells"] = 4
    doc["battery"]["chemistry"] = "LiPo"
    (d / "bench.json").write_text(json.dumps(doc), "utf-8")
    prof, err = P.load("bench", d)
    assert err is None and prof.battery["cells"] == 4 and prof.source.endswith("bench.json")


def test_rules():
    assert P.rule_matches({"eq": 1}, 1.0)
    assert not P.rule_matches({"eq": 1}, 2)
    assert P.rule_matches({"min": 10, "max": 20}, 15)
    assert not P.rule_matches({"in": [1, 2]}, 3)
    assert P.rule_matches({"bits_set": 5}, 7)
    assert not P.rule_matches({"bits_clear": 2}, 7)
    assert P.rule_error({"eq": "x"})
    assert P.rule_error({}) is not None


def test_settings_roundtrip_and_tolerance(tmp_path):
    st = SettingsStore(tmp_path)
    s = Settings()
    s.connection.host = "x500pi"
    s.pi.services["watchdog"] = "wd.service"
    st.save(s)
    s2 = st.load()
    assert s2.connection.host == "x500pi" and s2.pi.services["watchdog"] == "wd.service"
    # wrong types and unknown keys are ignored, not crashing
    raw = json.loads(st.path.read_text("utf-8"))
    raw["connection"]["ssh_port"] = "22"
    raw["unknown"] = 1
    st.path.write_text(json.dumps(raw), "utf-8")
    s3 = st.load()
    assert s3.connection.ssh_port == 22 and s3.connection.host == "x500pi"


def test_corrupt_settings_are_backed_up(tmp_path):
    st = SettingsStore(tmp_path)
    st.dir.mkdir(exist_ok=True)
    st.path.write_text("{oops", "utf-8")
    s = st.load()
    assert isinstance(s, Settings) and st.load_error
    assert (tmp_path / "settings.broken.json").exists()


def test_settings_file_contains_no_secret_fields():
    keys = json.dumps(Settings().to_dict()).lower()
    for word in ("password\"", "passphrase\"", "secret", "token"):
        assert word not in keys
