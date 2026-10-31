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


def _entry_ok(entry: object) -> bool:
    if not isinstance(entry, dict) or set(entry) != set(ENTRY_FIELDS):
        return False
    try:
        datetime.date.fromisoformat(entry["firstSeen"])
        datetime.date.fromisoformat(entry["lastSeen"])
    except (TypeError, ValueError):
        return False
    return True


def parse(data: object) -> dict[str, dict]:
    """seen map from cache/seen.json; ValueError if it is not usable."""
    if not isinstance(data, dict) or data.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("seen.json has an unknown shape")
    items = data.get("items")
    if not isinstance(items, dict) or not all(_entry_ok(e) for e in items.values()):
        raise ValueError("seen.json has a bad entry")
    return {key: dict(entry) for key, entry in items.items()}


def render(seen: Mapping[str, Mapping]) -> dict:
    return {"schemaVersion": SCHEMA_VERSION, "items": {key: dict(seen[key]) for key in sorted(seen)}}
