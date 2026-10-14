"""Configured addresses are verified against what the Pi observes - never assumed."""

from __future__ import annotations

import copy

from helpers import World
from x500fs.core.status import Status


def _world(link_path="wifi", **drone):
    w = World(profile_id="bench").healthy(link_path=link_path)
    for k, v in drone.items():
        setattr(w.drone, k, v)
    return w


def _set_network(w, routes=None, arrival=None, probes=None):
    doc = copy.deepcopy(w.store.get("pi.collector").value)
    net = doc["sections"]["network"]["data"]
    if routes is not None:
        net["default_routes"] = routes
    if arrival is not None:
        net["ssh_arrival"] = arrival
    if probes:
        doc["sections"]["net_probes"]["data"]["probes"].update(probes)
    w.put("pi.collector", doc)


def _r(w):
    return w.evaluate()[0]["link.config_verify"]


def test_nothing_configured_is_not_applicable():
    assert _r(_world()).status == Status.NOT_APPLICABLE


def test_zte_gateway_matches_route():
    w = _world(zte_gateway="10.20.30.1")
    _set_network(w, routes=[{"dev": "eth1", "gateway": "10.20.30.1", "metric": 200, "kind": "lte"}])
    r = _r(w)
    assert r.status == Status.PASS and "10.20.30.1" in r.observed


def test_zte_gateway_mismatch_warns():
    w = _world(zte_gateway="10.20.30.1")
    _set_network(w, routes=[{"dev": "eth1", "gateway": "192.0.2.254", "metric": 200, "kind": "lte"}])
    r = _r(w)
    assert r.status == Status.WARN and "192.0.2.254" in r.observed


def test_zte_gateway_without_route_or_reply_is_unknown():
    w = _world(zte_gateway="10.20.30.1")
    _set_network(w, routes=[{"dev": "wlan0", "gateway": "192.0.2.1", "metric": 100, "kind": "wifi"}],
                 probes={"zte_gateway": {"target": "10.20.30.1", "iface": "eth1", "ok": False}})
    assert _r(w).status == Status.UNKNOWN


def test_ground_ip_checked_against_tailscale_client_address():
    w = _world(ground_ip="100.99.1.2")
    _set_network(w, arrival={"server_ip": "100.64.0.20", "client_ip": "100.99.1.2", "dev": "tailscale0",
                             "kind": "tailscale"})
    assert _r(w).status == Status.PASS
    _set_network(w, arrival={"server_ip": "100.64.0.20", "client_ip": "100.99.7.7", "dev": "tailscale0",
                             "kind": "tailscale"})
    r = _r(w)
    assert r.status == Status.WARN and "100.99.7.7" in r.observed


def test_ground_ip_over_wifi_cannot_be_verified():
    w = _world(ground_ip="100.99.1.2")
    _set_network(w, arrival={"server_ip": "192.0.2.20", "client_ip": "192.0.2.50", "dev": "wlan0", "kind": "wifi"},
                 probes={"ground_station": {"target": "100.99.1.2", "ok": False}})
    assert _r(w).status == Status.UNKNOWN


def test_ground_no_reply_mentions_windows_firewall():
    w = _world(ground_ip="100.99.1.2")
    _set_network(w, probes={"ground_station": {"target": "100.99.1.2", "ok": False}})
    r = w.evaluate()[0]["link.ground"]
    assert r.status == Status.WARN and "Windows" in r.explanation


def test_battery_capacity_shown_with_fc_value():
    w = _world()
    doc = w.profile.raw
    doc["battery"].update({"chemistry": "LiPo", "cells": 4, "capacity_mah": 5200})
    w.set_profile({"battery": doc["battery"]})
    w.put("mav.param.BATT_CAPACITY", {"value": 5000.0})
    r = w.evaluate()[0]["batt.cells_config"]
    assert r.status == Status.PASS
    assert "5200 mAh" in r.observed and "5000" in r.observed and "שונה" in r.explanation
