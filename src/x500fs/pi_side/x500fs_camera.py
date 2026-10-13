#!/usr/bin/env python3
"""x500fs camera helper - query/configure the MediaMTX camera path.

Talks only to the MediaMTX Control API on localhost. ``status`` is read-only.
``configure`` PATCHes the runtime path configuration (resolution, FPS,
bitrate) and is only invoked by an explicit user action in the desktop app,
which first confirms the vehicle is disarmed. It does not edit mediamtx.yml,
restart services or install anything.

Usage: python3 - status|configure [--api URL] [--path NAME] [--width W ...]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def _req(url, method="GET", body=None, timeout=3.0):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (localhost only)
        raw = r.read(262144)
        return json.loads(raw.decode("utf-8")) if raw.strip() else {}


def status(api, path):
    out = {"ok": True, "api": api, "path_name": path}
    try:
        st = _req("%s/v3/paths/get/%s" % (api, path))
        out["path"] = {"ready": st.get("ready"), "source_type": (st.get("source") or {}).get("type"),
                       "readers": len(st.get("readers") or []), "tracks": st.get("tracks"),
                       "bytes_received": st.get("bytesReceived")}
    except urllib.error.HTTPError as e:
        out["path"] = None
        out["path_error"] = "HTTP %s" % e.code
    conf = _req("%s/v3/config/paths/get/%s" % (api, path))
    keep = ("source", "sourceOnDemand", "rpiCameraWidth", "rpiCameraHeight", "rpiCameraFPS",
            "rpiCameraBitrate", "rpiCameraCodec", "rpiCameraIDRPeriod")
    out["config"] = {k: conf.get(k) for k in keep if k in conf}
    return out


def configure(api, path, width, height, fps, bitrate):
    before = status(api, path)
    if before["config"].get("source") != "rpiCamera":
        return {"ok": False, "error": "path source is %r, not rpiCamera; edit mediamtx.yml instead"
                % before["config"].get("source"), "before": before}
    patch = {"rpiCameraWidth": width, "rpiCameraHeight": height, "rpiCameraFPS": fps,
             "rpiCameraBitrate": bitrate}
    changed = {k: v for k, v in patch.items() if before["config"].get(k) != v}
    if changed:
        _req("%s/v3/config/paths/patch/%s" % (api, path), method="PATCH", body=changed)
    after = status(api, path)
    return {"ok": True, "changed": changed, "before": before["config"], "after": after["config"]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["status", "configure"])
    ap.add_argument("--api", default="http://127.0.0.1:9997")
    ap.add_argument("--path", default="x500cam")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--fps", type=float, default=15)
    ap.add_argument("--bitrate", type=int, default=1000000)
    a = ap.parse_args(argv)
    api = a.api.rstrip("/")
    if not (api.startswith("http://127.0.0.1") or api.startswith("http://localhost")):
        print(json.dumps({"ok": False, "error": "API must be on localhost"}))
        return 2
    try:
        if a.action == "status":
            res = status(api, a.path)
        else:
            res = configure(api, a.path, a.width, a.height, a.fps, a.bitrate)
    except Exception as e:  # noqa: BLE001
        res = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
