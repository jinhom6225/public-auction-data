"""Day-to-day comparison kept on the data branch (plan D16, research §3.2).

`cache/seen.json`: key -> {firstSeen, lastLowestBid, priceDropAt,
prevLowestBid, lastSeen}. A price drop is today's representative
`lowestBid` below the last known number (D7). A rise or the same price is
not a drop and keeps the earlier `priceDropAt`. An item last seen 90 days
ago or less keeps its `firstSeen` (a re-issued notice is not "new"); one
not seen for 91 days or more is forgotten and counts as new again.

A1 caveat: whether `cltrMngNo` stays the same when every round failed and
the item comes back under a new notice could not be checked offline
(Phase 1 had one day of data). Watch the first days after step 4c.
"""

from __future__ import annotations

import datetime
from typing import Iterable, Mapping

from collector.config import SCHEMA_VERSION, SEEN_RETENTION_DAYS

ENTRY_FIELDS = ("firstSeen", "lastLowestBid", "priceDropAt", "prevLowestBid", "lastSeen")
OPTIONAL_DEFAULTS = {"lastLowestBid": None, "priceDropAt": None, "prevLowestBid": None}


def _fresh(today: str) -> dict:
    return {"firstSeen": today, "lastLowestBid": None, "priceDropAt": None, "prevLowestBid": None,
            "lastSeen": today}


def _alive(entry: Mapping, today: datetime.date) -> bool:
    last = datetime.date.fromisoformat(entry["lastSeen"])
    return (today - last).days <= SEEN_RETENTION_DAYS


def _next_entry(old: Mapping | None, bid: int | None, today: str) -> dict:
    entry = dict(old) if old else _fresh(today)
    last = entry["lastLowestBid"]
    if bid is not None and last is not None and bid < last:
        entry = {**entry, "priceDropAt": today, "prevLowestBid": last}
    if bid is not None:
        entry = {**entry, "lastLowestBid": bid}
    return {**entry, "lastSeen": today}


def apply(records: Iterable[Mapping], previous: Mapping[str, Mapping] | None,
          today: datetime.date) -> tuple[list[dict], dict[str, dict]]:
    """(records with firstSeen/priceDropAt/prevLowestBid, new seen map)."""
    day = today.isoformat()
    alive = {key: entry for key, entry in (previous or {}).items() if _alive(entry, today)}
    seen = dict(alive)
    out = []
    for record in records:
        entry = _next_entry(alive.get(record["key"]), record["lowestBid"], day)
        seen[record["key"]] = entry
        out.append({**record, "firstSeen": entry["firstSeen"], "priceDropAt": entry["priceDropAt"],
                    "prevLowestBid": entry["prevLowestBid"]})
    return out, {key: seen[key] for key in sorted(seen)}


def _is_date(value: object) -> bool:
    try:
        datetime.date.fromisoformat(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return True


def _is_amount(value: object) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool))


def _entry(entry: object) -> dict:
    """Entry with optional fields defaulted; extra fields are kept (L2)."""
    if not isinstance(entry, dict) or not _is_date(entry.get("firstSeen")) or not _is_date(entry.get("lastSeen")):
        raise ValueError("seen.json entry needs firstSeen and lastSeen dates")
    full = {**OPTIONAL_DEFAULTS, **entry}
    if not (_is_amount(full["lastLowestBid"]) and _is_amount(full["prevLowestBid"])):
        raise ValueError("seen.json entry has a bad amount")
    if full["priceDropAt"] is not None and not _is_date(full["priceDropAt"]):
        raise ValueError("seen.json entry has a bad priceDropAt")
    return full


def parse(data: object) -> dict[str, dict]:
    """seen map from cache/seen.json; ValueError if it is not usable."""
    if not isinstance(data, dict) or data.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("seen.json has an unknown shape")
    items = data.get("items")
    if not isinstance(items, dict):
        raise ValueError("seen.json has no items")
    return {key: _entry(entry) for key, entry in items.items()}


def render(seen: Mapping[str, Mapping]) -> dict:
    return {"schemaVersion": SCHEMA_VERSION, "items": {key: dict(seen[key]) for key in sorted(seen)}}
