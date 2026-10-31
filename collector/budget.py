"""Daily call caps per API, counted per KST day (plan D17).

Stored as `cache/budget.json` on the data branch:
    {"date": "2026-11-01", "used": {"onbid": 49}}
A failed run leaves the data branch untouched, so its calls are not
recorded; the caps leave room for that (Onbid uses about 49 of 900).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from collector.config import DAILY_CAPS
from collector.errors import BudgetExceeded


@dataclass(frozen=True)
class Budget:
    date: str
    used: Mapping[str, int] = field(default_factory=dict)
    caps: Mapping[str, int] = field(default_factory=lambda: dict(DAILY_CAPS))

    @staticmethod
    def load(saved: object, today: str, caps: Mapping[str, int] = DAILY_CAPS) -> "Budget":
        used: dict[str, int] = {}
        if isinstance(saved, dict) and saved.get("date") == today and isinstance(saved.get("used"), dict):
            used = {api: max(0, count) for api, count in saved["used"].items()
                    if api in caps and isinstance(count, int) and not isinstance(count, bool)}
        return Budget(today, MappingProxyType(used), MappingProxyType(dict(caps)))

    def used_of(self, api: str) -> int:
        return self.used.get(api, 0)

    def can_spend(self, api: str, calls: int) -> bool:
        return self.used_of(api) + calls <= self.caps[api]

    def spend(self, api: str) -> "Budget":
        if not self.can_spend(api, 1):
            raise BudgetExceeded(api)
        used = {**self.used, api: self.used_of(api) + 1}
        return Budget(self.date, MappingProxyType(used), self.caps)

    def to_json(self) -> dict:
        return {"date": self.date, "used": dict(sorted(self.used.items()))}
