"""Onbid usage codes -> one of seven groups (plan A8, data/usage_groups.json).

Small code first, then the mid-class fallback, then `other`. Unknown
codes are reported by the caller so the table can be extended.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from collector.config import DATA_DIR

GROUPS = ("apt", "rowhouse", "officetel", "house", "land", "commercial", "other")
USAGE_TEXT_LIMIT = 30


def _text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:USAGE_TEXT_LIMIT]


@dataclass(frozen=True)
class UsageTable:
    by_small: Mapping[str, Mapping]
    mid_fallback: Mapping[str, str]
    default: str

    @staticmethod
    def load(path: str | None = None) -> "UsageTable":
        with open(path or os.path.join(DATA_DIR, "usage_groups.json"), encoding="utf-8") as handle:
            data = json.load(handle)
        table = UsageTable(MappingProxyType(data["bySmallCode"]), MappingProxyType(data["midFallback"]),
                           data["default"])
        bad = [g for g in [*(v["group"] for v in table.by_small.values()), *table.mid_fallback.values()]
               if g not in GROUPS]
        if bad or table.default not in GROUPS:
            raise ValueError("usage_groups.json has a group outside the seven")
        return table

    def group_of(self, large: object, mid: object, small: object) -> str:
        entry = self.by_small.get(small) if isinstance(small, str) else None
        if entry:
            return entry["group"]
        return self.mid_fallback.get(mid, self.default) if isinstance(mid, str) else self.default

    def rtms_of(self, small: object) -> str | None:
        entry = self.by_small.get(small) if isinstance(small, str) else None
        return entry.get("rtms") if entry else None

    def usage(self, row: Mapping) -> tuple[dict, bool]:
        """(usage record, whether the small code is in the table)."""
        small = row.get("cltrUsgSclsCtgrId")
        record = {
            "l": _text(row.get("cltrUsgLclsCtgrNm")),
            "m": _text(row.get("cltrUsgMclsCtgrNm")),
            "s": _text(row.get("cltrUsgSclsCtgrNm")),
            "group": self.group_of(row.get("cltrUsgLclsCtgrId"), row.get("cltrUsgMclsCtgrId"), small),
        }
        return record, isinstance(small, str) and small in self.by_small
