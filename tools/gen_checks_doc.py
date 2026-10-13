"""Generate docs/checks.md from the check registry and the built-in profiles."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from x500fs.core import profile as P  # noqa: E402
from x500fs.core.checks import CATEGORIES, REGISTRY  # noqa: E402


def cfg_text(cfg: dict) -> str:
    if not cfg.get("enabled", True):
        return "מושבת"
    parts = ["**קריטי**" if cfg.get("critical") else "לא קריטי"]
    for k, v in cfg.items():
        if k in ("enabled", "critical", "rationale") or k.startswith("_"):
            continue
        parts.append(f"`{k}`={json.dumps(v, ensure_ascii=False)}")
    return "<br>".join(parts)


def main(out: str) -> None:
    bench, flight = P.load_builtin("bench"), P.load_builtin("flight")
    lines = [
        "# קטלוג הבדיקות האוטומטיות",
        "",
        "נוצר אוטומטית מ-`tools/gen_checks_doc.py` (אל תערוך ידנית). הספים נמצאים בקבצי הפרופיל "
        "`bench.json` / `flight.json` ואפשר לשנות אותם. ערך `null` = לא מוגדר (הבדיקה תהיה UNKNOWN, "
        "ואם היא קריטית - התוצאה הכוללת לא תהיה \"עברו\"). ערך `\"fc:PARAM\"` = הסף נלקח מפרמטר של בקר הטיסה.",
        "",
    ]
    for cat, title in CATEGORIES.items():
        lines += [f"## {title}", "", "| מזהה | בדיקה | ספסל (bench) | טיסה (flight) | הצדקה |", "|---|---|---|---|---|"]
        for d in REGISTRY:
            if d.category != cat:
                continue
            b, f = bench.check(d.id), flight.check(d.id)
            why = f.get("rationale") or b.get("rationale") or ""
            lines.append(f"| `{d.id}` | {d.title} | {cfg_text(b)} | {cfg_text(f)} | {why} |")
        lines.append("")
    Path(out).write_text("\n".join(lines) + "\n", "utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "docs/checks.md")
