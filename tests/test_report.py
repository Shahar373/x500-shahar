"""Report generation and redaction."""

from __future__ import annotations

import json

from helpers import World

from x500fs.core.manual import ManualChecklist
from x500fs.core.redact import redact, redact_text
from x500fs.core.report import build_report, render_html, save_report


def _report(w: World, secrets=None, manual=None):
    results_map, overall = w.evaluate()
    results = list(results_map.values())
    m = manual or ManualChecklist()
    w.session.link_up("mavlink")
    w.session.link_down("mavlink", "test outage")
    w.session.link_up("mavlink")
    return build_report(
        snap=w.store.snapshot(), results=results, overall=overall, profile_summary=w.profile.summary(),
        profile_error=None,
        settings_summary={"host": "x500pi", "ssh_user": "pi", "link_path": "wifi",
                          "video_url": "rtsp://admin:hunter2@x500pi:8554/x500cam"},
        manual=m.to_list(), manual_done=m.done_count(), manual_total=m.total(),
        interruptions=[i.to_dict() for i in w.session.interruptions()],
        events=[{"text": "password=hunter2 tskey-abcDEF123"}],
        statustext=[{"t_wall": 0, "severity_name": "CRITICAL", "text": "PreArm: Compass not calibrated"}],
        video=None, session_id=w.session.session_id, preflight_started=None, extra_secrets=secrets or [])


def test_report_contents(tmp_path):
    w = World(profile_id="flight").healthy()
    m = ManualChecklist()
    m.set("props", True)
    doc = _report(w, manual=m)
    assert doc["schema"] == "x500fs.report/1"
    assert doc["app"]["version"]
    assert doc["simulation"] is False
    assert doc["profile"]["id"] == "flight" and len(doc["profile"]["sha256"]) == 64
    assert doc["manual_checklist"]["done"] == 1 and doc["manual_checklist"]["completed"] is False
    assert doc["automatic_checks"]["overall"]["status"] == "INCOMPLETE"
    ids = {r["id"] for r in doc["automatic_checks"]["results"]}
    assert {"fc.prearm", "pi.temperature", "batt.voltage"} <= ids
    for r in doc["automatic_checks"]["results"]:
        assert set(r) >= {"status", "explanation", "observed", "expected", "source", "age_s"}
    assert doc["interruptions"] and doc["interruptions"][0]["link"] == "mavlink"
    assert any(x["check"] == "cfg.failsafe" for x in doc["warnings"])
    assert doc["hardware"]["pi"]["hostname"] == "x500pi"
    assert doc["hardware"]["autopilot_version"]["flight_sw"]["text"] == "4.7.2"
    assert "אינו מבטיח טיסה בטוחה" in doc["disclaimer"]
    jp, hp = save_report(doc, tmp_path)
    assert json.loads(jp.read_text("utf-8"))["schema"] == "x500fs.report/1"
    html = hp.read_text("utf-8")
    assert 'dir="rtl"' in html and 'lang="he"' in html
    assert "סימולציה" not in html.split("<body>")[1].split("<h1>")[0]  # no sim banner for live


def test_report_redaction():
    w = World().healthy()
    doc = _report(w, secrets=["MyS3cretPassphrase"])
    doc_text = json.dumps(doc, ensure_ascii=False)
    assert "hunter2" not in doc_text
    assert "tskey-abcDEF123" not in doc_text
    html = render_html(doc)
    assert "hunter2" not in html


def test_redact_helpers():
    assert redact({"ssh_password": "x", "passphrase": "y", "token": "z", "key_path": "/k"}) == \
        {"ssh_password": "***", "passphrase": "***", "token": "***", "key_path": "/k"}
    assert redact({"passed": 3, "bypass": "ok"}) == {"passed": 3, "bypass": "ok"}
    red = redact_text("x -----BEGIN OPENSSH PRIVATE KEY-----\nabcSECRET\n-----END OPENSSH PRIVATE KEY----- y")
    assert "abcSECRET" not in red and "BEGIN" not in red
    assert redact_text("rtsp://u:p@h/x") == "rtsp://u:***@h/x"
    assert redact("my secret is Hunter2Pass", ["Hunter2Pass"]) == "my secret is ***"


def test_simulation_report_is_marked(tmp_path):
    w = World(simulated=True).healthy()
    doc = _report(w)
    assert doc["simulation"] is True
    jp, hp = save_report(doc, tmp_path)
    assert "SIMULATION" in jp.name
    html = hp.read_text("utf-8")
    assert "סימולציה - הנתונים בדוח זה מדומים" in html
    assert all(r["source"].startswith("SIM") for r in doc["automatic_checks"]["results"]
               if r["source"] not in ("profile", "") and r["status"] != "NOT_APPLICABLE")
