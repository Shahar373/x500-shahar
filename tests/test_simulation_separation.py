"""Simulation can never be mistaken for, or mixed with, the real drone."""

from __future__ import annotations

import time

import pytest

from x500fs.controller import Controller, Listener
from x500fs.core.observations import ObservationStore, OriginMismatch
from x500fs.core.session import SessionLog
from x500fs.settings import SettingsStore
from x500fs.sim.simulator import SimDrone


def test_store_refuses_other_origin():
    live = ObservationStore(simulated=False)
    with pytest.raises(OriginMismatch):
        live.put("pi.collector", {}, "SIM", simulated=True)
    sim = ObservationStore(simulated=True)
    with pytest.raises(OriginMismatch):
        sim.put("pi.collector", {}, "x", simulated=False)


def test_sim_drone_refuses_live_store():
    with pytest.raises(ValueError):
        SimDrone(ObservationStore(simulated=False), SessionLog())


def test_controller_simulation_end_to_end(tmp_path):
    store = SettingsStore(tmp_path)
    s = store.load()
    s.simulation = True
    s.connection.host = "should-not-be-contacted.invalid"
    store.save(s)
    ctl = Controller(store, Listener())
    ctl.connect()
    try:
        assert ctl.simulation and ctl._ssh is None and ctl._mav is None
        time.sleep(1.5)
        ctl.run_preflight()
        time.sleep(0.5)
        results, overall, snap = ctl.evaluate()
        assert snap.simulated
        for r in results:
            if r.source not in ("profile", "", "evaluator") and r.status.value != "NOT_APPLICABLE":
                assert r.source.startswith("SIM"), (r.check_id, r.source)
        jp, hp = ctl.export_report(tmp_path / "r")
        assert "SIMULATION" in jp.name
        assert ctl.settings_summary()["host"] == "SIM"
    finally:
        ctl.disconnect()


def test_switching_mode_requires_new_store(tmp_path):
    store = SettingsStore(tmp_path)
    ctl = Controller(store, Listener())
    assert ctl.simulation is False
    new = store.load()
    new.simulation = True
    ctl.apply_settings(new)
    assert ctl.simulation is True  # a fresh simulation-only store
    with pytest.raises(OriginMismatch):
        ctl.store.put("x", 1, "live", simulated=False)


def test_manual_checklist_resets_each_preflight(tmp_path):
    store = SettingsStore(tmp_path)
    s = store.load()
    s.simulation = True
    store.save(s)
    ctl = Controller(store, Listener())
    ctl.connect()
    try:
        ctl.manual.set("props", True)
        assert ctl.manual.done_count() == 1
        ctl.run_preflight()
        assert ctl.manual.done_count() == 0
    finally:
        ctl.disconnect()
