"""Automatic checks: grouped tree + details pane."""

from __future__ import annotations

import html
import json

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import QHeaderView, QSplitter, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from ..core.checks.base import CATEGORIES, CATEGORY_ORDER, fmt_age
from ..core.status import CheckResult
from .style import STATUS_COLORS, STATUS_HE, badge_html

COLS = ["סטטוס", "בדיקה", "נצפה", "צפוי", "מקור", "גיל"]


class ChecksView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(COLS))
        self.tree.setHeaderLabels(COLS)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        h = self.tree.header()
        h.setStretchLastSection(False)
        for col, width in ((0, 122), (1, 235), (2, 200), (3, 150), (4, 135), (5, 46)):
            h.setSectionResizeMode(col, QHeaderView.Interactive)
            self.tree.setColumnWidth(col, width)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        h.setMinimumSectionSize(40)
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(False)
        opt = self.details.document().defaultTextOption()
        opt.setTextDirection(Qt.RightToLeft)
        self.details.document().setDefaultTextOption(opt)
        self.details.setMinimumHeight(120)
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.tree)
        split.addWidget(self.details)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)
        self._cats: dict[str, QTreeWidgetItem] = {}
        self._items: dict[str, QTreeWidgetItem] = {}
        self._results: dict[str, CheckResult] = {}
        self.tree.currentItemChanged.connect(self._show_details)
        self.details.setHtml("<p style='color:#5f6b7a'>בחר בדיקה כדי לראות הסבר, ראיות ומגבלות.</p>")

    def update_results(self, results: list[CheckResult]) -> None:
        bold = QFont()
        bold.setBold(True)
        by_cat: dict[str, list[CheckResult]] = {}
        for r in results:
            by_cat.setdefault(r.category, []).append(r)
            self._results[r.check_id] = r
        for cat in CATEGORY_ORDER:
            rows = by_cat.get(cat, [])
            if not rows:
                continue
            parent = self._cats.get(cat)
            if parent is None:
                parent = QTreeWidgetItem(self.tree)
                parent.setFirstColumnSpanned(False)
                parent.setFont(1, bold)
                parent.setExpanded(True)
                self._cats[cat] = parent
            worst = _worst(rows)
            counts = {}
            for r in rows:
                counts[r.status.value] = counts.get(r.status.value, 0) + 1
            parent.setText(0, worst)
            parent.setForeground(0, QBrush(QColor(STATUS_COLORS[worst])))
            parent.setFont(0, bold)
            parent.setText(1, CATEGORIES[cat])
            parent.setText(2, " · ".join(f"{STATUS_HE[k]} {v}" for k, v in counts.items()))
            for r in rows:
                item = self._items.get(r.check_id)
                if item is None:
                    item = QTreeWidgetItem(parent)
                    item.setData(0, Qt.UserRole, r.check_id)
                    self._items[r.check_id] = item
                item.setText(0, r.status.value)
                item.setForeground(0, QBrush(QColor(STATUS_COLORS[r.status.value])))
                item.setFont(0, bold)
                item.setToolTip(0, STATUS_HE[r.status.value])
                item.setText(1, r.title + (" ★" if r.critical else ""))
                item.setToolTip(1, ("קריטי - " if r.critical else "") + r.explanation)
                item.setText(2, r.observed or "—")
                item.setToolTip(2, r.observed or "")
                item.setText(3, r.expected or "—")
                item.setToolTip(3, r.expected or "")
                item.setText(4, r.source)
                item.setToolTip(4, r.source)
                item.setText(5, fmt_age(r.age_s))
        cur = self.tree.currentItem()
        if cur is not None:
            self._show_details(cur, None)

    def _show_details(self, item, _prev) -> None:
        if item is None:
            return
        cid = item.data(0, Qt.UserRole)
        r = self._results.get(cid)
        if r is None:
            return
        e = html.escape
        rows = [
            ("סטטוס", badge_html(r.status.value) + f" {e(STATUS_HE[r.status.value])}"),
            ("קריטי", "כן - חוסם תוצאה 'עברו' אם אינו עובר" if r.critical else "לא"),
            ("הסבר", e(r.explanation)),
            ("נצפה", e(r.observed or "—")),
            ("צפוי", e(r.expected or "—")),
            ("מקור הנתון", e(r.source)),
            ("גיל התצפית", e(fmt_age(r.age_s))),
        ]
        if not r.configured:
            rows.append(("הגדרה", "<b>לא מוגדר</b> - יש להגדיר סף/ערך צפוי בפרופיל או בהגדרות"))
        if r.limitations:
            rows.append(("מגבלות", e(r.limitations)))
        if r.missing:
            rows.append(("ראיות חסרות", e(", ".join(r.missing))))
        body = "".join(f"<tr><td align='right' style='color:#5f6b7a;padding:2px 8px;white-space:nowrap'>{k}</td>"
                       f"<td align='right' style='padding:2px 8px'>{v}</td></tr>" for k, v in rows)
        ev = ""
        if r.evidence:
            ev = ("<p style='color:#5f6b7a;margin-top:6px'>ראיות גולמיות:</p><pre dir='ltr' style='font-size:11px'>"
                  + e(json.dumps(r.evidence, ensure_ascii=False, indent=1)[:6000]) + "</pre>")
        self.details.setHtml(f"<h3 style='margin:2px 0'>{e(r.title)} <span style='color:#9aa0a6;font-size:11px'>"
                             f"&#x2066;{e(r.check_id)}&#x2069;</span></h3><table dir='rtl' cellspacing='0'>{body}</table>{ev}")


_ORDER = ["FAIL", "UNKNOWN", "WARN", "PASS", "NOT_APPLICABLE"]


def _worst(rows: list[CheckResult]) -> str:
    present = {r.status.value for r in rows}
    for s in _ORDER:
        if s in present:
            return s
    return "NOT_APPLICABLE"
