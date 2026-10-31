"""Representative round of an item (plan D7, A2).

One API row is one round. Representative = the round in bidding
(입찰진행중), else the nearest scheduled one (입찰준비중). Closed rows
(입찰마감) are never representative, so an item with only closed rounds
leaves the copy. Rounds whose end has passed are skipped. Up to three
later scheduled rounds go to `nextRounds`. The 2999 placeholder sorts
last; ties break on `pbctCdtnNo` so the choice is deterministic.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Iterable, Mapping

from collector import values

STATUS_READY = "0001"
STATUS_OPEN = "0002"
MAX_NEXT_ROUNDS = 3
_FAR_FUTURE = datetime.datetime.max.replace(tzinfo=datetime.timezone.utc)


@dataclass(frozen=True)
class Picked:
    representative: Mapping
    upcoming: tuple[Mapping, ...]


def group_by_key(rows: Iterable[Mapping]) -> dict[str, list[Mapping]]:
    """Rows per item key (`cltrMngNo`), keys in sorted order."""
    groups: dict[str, list[Mapping]] = {}
    for row in rows:
        key = row.get("cltrMngNo")
        if isinstance(key, str) and key:
            groups.setdefault(key, []).append(row)
    return {key: groups[key] for key in sorted(groups)}


def _order(row: Mapping) -> tuple:
    start = values.bid_datetime(row.get("cltrBidBgngDt"))
    number = row.get("pbctCdtnNo")
    return (start or _FAR_FUTURE, number if isinstance(number, int) else 0)


def _still_open(row: Mapping, now: datetime.datetime) -> bool:
    end = values.bid_datetime(row.get("cltrBidEndDt"))
    return end is None or end > now


def pick(rows: Iterable[Mapping], now: datetime.datetime) -> Picked | None:
    live = [r for r in rows if _still_open(r, now)]
    open_rows = sorted((r for r in live if r.get("pbctStatCd") == STATUS_OPEN), key=_order)
    ready = sorted((r for r in live if r.get("pbctStatCd") == STATUS_READY), key=_order)
    if open_rows:
        chosen = open_rows[0]
    elif ready:
        chosen = ready[0]
    else:
        return None
    later = [r for r in ready if r is not chosen and _order(r) > _order(chosen)]
    return Picked(chosen, tuple(later[:MAX_NEXT_ROUNDS]))


def next_rounds(picked: Picked) -> list[dict]:
    return [
        {
            "bidStart": values.bid_time(r.get("cltrBidBgngDt")),
            "bidEnd": values.bid_time(r.get("cltrBidEndDt")),
            "lowestBid": values.lowest_bid(r.get("lowstBidPrcIndctCont"))[0],
        }
        for r in picked.upcoming
    ]
