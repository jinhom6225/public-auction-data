"""Yesterday's data branch, checked out by the workflow into `prev/` (D30).

A run is either `--first-run` (no data branch yet: everything is new, no
decrease check) or `--prev DIR`, in which case the previous copy must be
readable; a broken copy fails the run instead of silently treating every
item as new (which would flood the app with "new" badges). The real-
transaction cache (`cache/rtms/`, Phase 3) is optional: a missing folder
is an empty cache and a broken month file is skipped (refetched).
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from collector import rtms_cache, seen_history
from collector.config import KST, MAX_INDEX_BYTES as MAX_INDEX_FILE
from collector.errors import ValidationFailed


@dataclass(frozen=True)
class Previous:
    seen: dict | None
    total: int | None
    budget: object
    counts: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    rtms: rtms_cache.Cache = field(default_factory=lambda: MappingProxyType({}))
    rtms_skipped: int = 0
    backfill_state: object = None


FIRST_RUN = Previous(seen=None, total=None, budget=None)


# Security LOW-1: size caps before json.load (seen.json for ~10,000 items is about 2MB).
MAX_INDEX_BYTES = MAX_INDEX_FILE
MAX_SEEN_BYTES = 64 * 1024 * 1024
MAX_SMALL_BYTES = 1024 * 1024


def _read_json(path: str, max_bytes: int) -> object:
    return rtms_cache.read_json(path, max_bytes)


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


def _optional_json(path: str) -> object:
    try:
        return _read_json(path, MAX_SMALL_BYTES)
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def load(folder: str, today: datetime.date | None = None) -> Previous:
    today = today or datetime.datetime.now(KST).date()
    try:
        counts = _counts(_read_json(os.path.join(folder, "v1", "index.json"), MAX_INDEX_BYTES))
        seen = seen_history.parse(_read_json(os.path.join(folder, "cache", "seen.json"), MAX_SEEN_BYTES))
    except (OSError, UnicodeDecodeError, ValueError):
        raise ValidationFailed("previous copy is missing or unreadable (v1/index.json, cache/seen.json)") from None
    budget = _optional_json(os.path.join(folder, "cache", "budget.json"))
    state = _optional_json(os.path.join(folder, "cache", "backfill_state.json"))
    try:
        rtms, skipped = rtms_cache.load_folder(folder, today)
    except (OSError, ValueError) as error:
        raise ValidationFailed(f"previous real-transaction cache refused: {error}") from None
    return Previous(seen=seen, total=sum(counts.values()), budget=budget, counts=MappingProxyType(counts),
                    rtms=MappingProxyType(rtms), rtms_skipped=skipped, backfill_state=state)
