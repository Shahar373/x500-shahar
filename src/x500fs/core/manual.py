"""Manual pre-flight checklist. Reset for every new preflight session."""

from __future__ import annotations

import time
from dataclasses import dataclass

ITEMS: list[tuple[str, str, str]] = [
    ("props", "מדחפים ומנועים",
     "מדחפים תקינים (ללא סדקים), מותקנים בכיוון הנכון ומהודקים. המנועים מחוברים היטב לזרועות."),
    ("frame", "שלדה וברגים",
     "הזרועות נעולות, הברגים מהודקים, אין סדקים או חלקים רופפים בשלדה."),
    ("battery", "סוללה ומחברים",
     "הסוללה קשורה היטב ברצועה, המחברים תקינים ונעולים, אין נפיחות או נזק לסוללה."),
    ("payload", "מטען וכבלים",
     "מצלמה, Pi, מודם LTE, GPS וכבלים מחוזקים, ואף כבל אינו יכול להגיע למדחפים."),
    ("area", "אזור המראה ותנאי סביבה",
     "שטח פתוח ובטוח, אין אנשים בקרבת מקום, רוח ומזג אוויר מתאימים, והטיסה מותרת במקום."),
]


@dataclass
class Confirmation:
    item_id: str
    confirmed: bool
    t_wall: float | None


class ManualChecklist:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._state = {item_id: Confirmation(item_id, False, None) for item_id, _, _ in ITEMS}
        self.reset_at = time.time()

    def set(self, item_id: str, confirmed: bool) -> None:
        if item_id not in self._state:
            raise KeyError(item_id)
        self._state[item_id] = Confirmation(item_id, confirmed, time.time() if confirmed else None)

    def is_confirmed(self, item_id: str) -> bool:
        return self._state[item_id].confirmed

    def done_count(self) -> int:
        return sum(1 for c in self._state.values() if c.confirmed)

    def total(self) -> int:
        return len(self._state)

    def completed(self) -> bool:
        return self.done_count() == self.total()

    def to_list(self) -> list[dict]:
        out = []
        for item_id, title, detail in ITEMS:
            c = self._state[item_id]
            out.append({
                "id": item_id, "title": title, "detail": detail, "confirmed": c.confirmed,
                "confirmed_at": None if c.t_wall is None else time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(c.t_wall)),
            })
        return out
