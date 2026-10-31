"""Onbid rows -> item records (research §3.3, plan D6-D8, D13, A1-A8).

Rows are grouped by `cltrMngNo` (A1). Each item takes its representative
round (D7/A2), current-code location (A3), usage group (A8), values (A7)
and links (A5). Records that break a D13 rule are dropped and counted by
reason. `firstSeen`/`priceDropAt`/`prevLowestBid` (seen_history) and
`compare` (compare_hook) are added later.
"""

from __future__ import annotations

import datetime
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Mapping

from collector import parcel, rounds, values
from collector.compare_hook import NO_COMPARE
from collector.region_index import Location, RegionIndex
from collector.usage import UsageTable
from collector.validate import record_problems

SALE = "0001"
BID_LIST = "N"


@dataclass(frozen=True)
class Context:
    regions: RegionIndex
    usage: UsageTable
    now: datetime.datetime

    @staticmethod
    def load(now: datetime.datetime) -> "Context":
        return Context(RegionIndex.load(), UsageTable.load(), now)


@dataclass(frozen=True)
class NormalizedItem:
    """An output record plus what Phase 3 needs but the app does not."""

    record: Mapping
    sido: str
    usage_code: str | None
    rtms: str | None
    rows: tuple[Mapping, ...]


@dataclass(frozen=True)
class NormalizeResult:
    items: tuple[NormalizedItem, ...]
    dropped: Mapping[str, int]
    unknown_usage: Mapping[str, int]
    closed: int
    filtered_rows: int

    @property
    def candidates(self) -> int:
        return len(self.items) + sum(self.dropped.values())


def _text(value: object) -> str | None:
    """Whitespace collapsed; never cut (an over-long value fails D13)."""
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or None


def _numbers(row: Mapping) -> dict:
    lowest, lowest_text = values.lowest_bid(row.get("lowstBidPrcIndctCont"))
    appraisal = values.amount(row.get("apslEvlAmt"))
    fails = row.get("usbdNft")
    return {
        "appraisal": appraisal, "lowestBid": lowest, "lowestBidText": lowest_text,
        "ratioToAppraisal": values.ratio(lowest, appraisal),
        "failCount": fails if isinstance(fails, int) and not isinstance(fails, bool) else None,
        "landM2": values.area(row.get("landSqms")), "bldgM2": values.area(row.get("bldSqms")),
    }


def build_record(key: str, picked: rounds.Picked, location: Location, usage: dict, index: RegionIndex) -> dict:
    row = picked.representative
    round_no = row.get("pbctNsq")
    return {
        "key": key,
        "round": str(round_no) if isinstance(round_no, (str, int)) and str(round_no) else None,
        "name": _text(row.get("onbidCltrNm")),
        "pnu": parcel.pnu_of(dict(row), location, index),
        "sgg": location.sgg, "umd": location.umd, "emdName": location.emd_name,
        "usage": usage,
        "assetType": _text(row.get("prptDivNm")),
        "share": row.get("alcYn") == "Y",
        **_numbers(row),
        "bidStart": values.bid_time(row.get("cltrBidBgngDt")),
        "bidEnd": values.bid_time(row.get("cltrBidEndDt")),
        "thumb": values.onbid_https(row.get("thnlImgUrlAdr")),
        "onbidUrl": values.item_link(row),
        "nextRounds": rounds.next_rounds(picked),
    }


def _checked(record: dict, ctx: Context) -> list[str]:
    """D13 problems, with the fields added later filled by placeholders."""
    sido = record["sgg"][:2]
    stub = {**record, "firstSeen": ctx.now.date().isoformat(), "priceDropAt": None,
            "prevLowestBid": None, "compare": dict(NO_COMPARE)}
    return record_problems(stub, sido, ctx.regions)


def normalize_item(key: str, rows: list[Mapping], ctx: Context, unknown: Counter) -> NormalizedItem | str | None:
    """The item, a drop reason, or None for an item with no open round."""
    picked = rounds.pick(rows, ctx.now)
    if picked is None:
        return None
    row = picked.representative
    location = ctx.regions.locate(row)
    if location is None:
        return "no location"
    usage, known = ctx.usage.usage(row)
    code = row.get("cltrUsgSclsCtgrId") if isinstance(row.get("cltrUsgSclsCtgrId"), str) else None
    if not known:
        unknown[code or "-"] += 1
    record = build_record(key, picked, location, usage, ctx.regions)
    problems = _checked(record, ctx)
    if problems:
        return problems[0]
    return NormalizedItem(record, location.sgg[:2], code, ctx.usage.rtms_of(code), tuple(rows))


def _in_scope(row: Mapping) -> bool:
    return row.get("dspsMthodCd") == SALE and row.get("pvctTrgtYn") == BID_LIST


def normalize_all(rows: Iterable[Mapping], ctx: Context) -> NormalizeResult:
    rows = list(rows)
    kept = [row for row in rows if _in_scope(row)]
    items, dropped, unknown, closed = [], Counter(), Counter(), 0
    groups = rounds.group_by_key(kept)
    keyless = sum(1 for row in kept if not isinstance(row.get("cltrMngNo"), str) or not row["cltrMngNo"])
    if keyless:
        dropped["no key (rows)"] += keyless
    for key, group in groups.items():
        outcome = normalize_item(key, group, ctx, unknown)
        if outcome is None:
            closed += 1
        elif isinstance(outcome, str):
            dropped[outcome] += 1
        else:
            items.append(outcome)
    return NormalizeResult(tuple(items), dict(+dropped), dict(unknown), closed, len(rows) - len(kept))
