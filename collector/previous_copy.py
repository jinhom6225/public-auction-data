"""Yesterday's data branch, checked out by the workflow into `prev/` (D30).

A run is either `--first-run` (no data branch yet: everything is new, no
decrease check) or `--prev DIR`, in which case the previous copy must be
readable; a broken copy fails the run instead of silently treating every
item as new (which would flood the app with "new" badges).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from collector import seen_history
from collector.errors import ValidationFailed


@dataclass(frozen=True)
class Previous:
    seen: dict | None
    total: int | None
    budget: object
    counts: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))


FIRST_RUN = Previous(seen=None, total=None, budget=None)


def _read_json(path: str) -> object:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _counts(index: object) -> dict[str, int]:
    """Item count per province from yesterday's index."""
    regions = index.get("regions") if isinstance(index, dict) else None
    if not isinstance(regions, list) or not regions:
        raise ValueError("no regions")
    counts: dict[str, int] = {}
    for entry in regions:
        sido = entry.get("sido") if isinstance(entry, dict) else None
        count = entry.get("count") if isinstance(entry, dict) else None
        if not isinstance(sido, str) or not (isinstance(count, int) and not isinstance(count, bool) and count >= 0):
            raise ValueError("bad region entry")
        counts[sido] = count
    return counts


def load(folder: str) -> Previous:
    try:
        counts = _counts(_read_json(os.path.join(folder, "v1", "index.json")))
        seen = seen_history.parse(_read_json(os.path.join(folder, "cache", "seen.json")))
    except (OSError, ValueError):
        raise ValidationFailed("previous copy is missing or unreadable (v1/index.json, cache/seen.json)") from None
    try:
        budget = _read_json(os.path.join(folder, "cache", "budget.json"))
    except (OSError, ValueError):
        budget = None
    return Previous(seen=seen, total=sum(counts.values()), budget=budget, counts=MappingProxyType(counts))
