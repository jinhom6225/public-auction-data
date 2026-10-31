"""Real-transaction comparison of one item (research §3.4, plan Phase 3).

sameBuilding (usage groups apt and officetel only — A9, 진호님 2026-11-01;
rowhouse items get sameDong): deals whose building key equals the item
PNU key (pnu.item_key), area within ±10% (inclusive) of `bldgM2`, at least
3 in the 12-month window. medianPerM2 = median of amount/area over *all*
matches, estimate = medianPerM2 x bldgM2, ratio = round(lowestBid /
estimate x 100) half up — null for share items or a null lowestBid.
`n` counts all matches; `deals` shows the 10 newest (`maxShown`). An
estimate above 1e13 falls back to sameDong; a ratio above 100,000 is null.

sameDong (every API, and apt/rh/offi with fewer than 3 matches): same
읍면동 code, area within ±30%, land: same 지목, commercial/factory: same
building use (compare_params.json tables; an unmapped usage code gets
none). The 10 newest deals; `n` and the median are over those deals. The
dong is the item's `umd`, or the PNU's 읍면동 when `umd` is missing.

In both kinds the rounded median must be 1..1e13 won/㎡ (sub-won land
prices or tiny areas otherwise break the app's range rule); if not, the
comparison falls back (sameBuilding -> sameDong -> none).
Cancelled and share deals never reach the cache (rtms_client).

Numbers live in `data/compare_params.json`.
"""

from __future__ import annotations

import json
import math
import os
import statistics
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

from collector import pnu, values
from collector.config import DATA_DIR
from collector.rtms_cache import Cache

NONE: Mapping[str, str] = MappingProxyType({"kind": "none"})
_EPSILON = 1e-9


@dataclass(frozen=True)
class Params:
    months: int
    building_groups: frozenset[str]
    building_apis: frozenset[str]
    building_tolerance: float
    building_min: int
    building_shown: int
    dong_tolerance: float
    dong_max: int
    dong_min: int
    item_area: Mapping[str, str]
    land_jimok: Mapping[str, str]
    building_use: Mapping[str, tuple[str, ...]]

    @staticmethod
    def load(path: str | None = None) -> "Params":
        with open(path or os.path.join(DATA_DIR, "compare_params.json"), encoding="utf-8") as handle:
            data = json.load(handle)
        building, dong = data["sameBuilding"], data["sameDong"]
        return Params(
            months=data["months"], building_groups=frozenset(building["groups"]),
            building_apis=frozenset(building["apis"]),
            building_tolerance=building["areaTolerance"], building_min=building["minDeals"],
            building_shown=building["maxShown"],
            dong_tolerance=dong["areaTolerance"], dong_max=dong["maxDeals"], dong_min=dong["minDeals"],
            item_area=MappingProxyType(dict(data["itemArea"])),
            land_jimok=MappingProxyType(dict(data["landJimok"])),
            building_use=MappingProxyType({code: tuple(uses) for code, uses in data["buildingUse"].items()}),
        )


@dataclass(frozen=True)
class Deal:
    ym: str            # "2026-09"
    day: int
    amount: int
    area: float
    floor: int | None
    umd: str | None
    key: str | None
    jimok: str | None
    use: str | None

    @property
    def per_m2(self) -> float:
        return self.amount / self.area

    def shown(self) -> dict:
        return {"ym": self.ym, "m2": self.area, "floor": self.floor, "amount": self.amount}


DealIndex = Mapping[tuple[str, str], tuple[Deal, ...]]


def _newest_first(deal: Deal) -> tuple:
    return (-int(deal.ym.replace("-", "")), -deal.day, deal.amount, deal.area,
            -10_000 if deal.floor is None else deal.floor)


def deals_index(cache: Cache) -> DealIndex:
    """(api, lawd) -> deals, newest first."""
    grouped: dict[tuple[str, str], list[Deal]] = {}
    for (api, lawd, ym), month in cache.items():
        label = f"{ym[:4]}-{ym[4:]}"
        grouped.setdefault((api, lawd), []).extend(
            Deal(label, row["d"], row["p"], row["a"], row.get("f"), row.get("u"), row.get("k"), row.get("j"),
                 row.get("b")) for row in month.rows)
    return {pair: tuple(sorted(deals, key=_newest_first)) for pair, deals in grouped.items()}


def _within(area: float, target: float, tolerance: float) -> bool:
    return abs(area - target) <= tolerance * target + _EPSILON


def _half_up(value: float) -> int:
    return math.floor(value + 0.5)


def _median_per_m2(deals: Sequence[Deal]) -> float:
    return statistics.median(deal.per_m2 for deal in deals)


def _amount_ok(value: int) -> bool:
    return 1 <= value <= values.MAX_AMOUNT


def _same_building(record: Mapping, area: float, pool: Sequence[Deal], params: Params) -> dict | None:
    key = pnu.item_key(record.get("pnu"))
    if key is None:
        return None
    matched = [d for d in pool if d.key == key and _within(d.area, area, params.building_tolerance)]
    if len(matched) < params.building_min:
        return None
    median = _median_per_m2(matched)
    estimate = _half_up(median * area)
    if not (_amount_ok(_half_up(median)) and _amount_ok(estimate)):
        return None
    lowest = record.get("lowestBid")
    ratio = None if record.get("share") or lowest is None else values.ratio(lowest, estimate)
    return {"kind": "sameBuilding", "months": params.months, "n": len(matched),
            "medianPerM2": _half_up(median), "estimate": estimate, "ratio": ratio,
            "deals": [d.shown() for d in matched[:params.building_shown]]}


def _dong_filter(api: str, usage_code: str | None, params: Params):
    if api == "land":
        jimok = params.land_jimok.get(usage_code or "")
        return (lambda d: d.jimok == jimok) if jimok else None
    if api in ("nrg", "indu"):
        uses = params.building_use.get(usage_code or "")
        return (lambda d: d.use in uses) if uses else None
    return lambda d: True


def _same_dong(record: Mapping, api: str, usage_code: str | None, area: float, pool: Sequence[Deal],
               params: Params) -> dict | None:
    umd = record.get("umd") or _pnu_dong(record.get("pnu"))
    kind_ok = _dong_filter(api, usage_code, params)
    if not umd or kind_ok is None:
        return None
    chosen = [d for d in pool if d.umd == umd and kind_ok(d) and _within(d.area, area, params.dong_tolerance)]
    chosen = chosen[:params.dong_max]
    median = _half_up(_median_per_m2(chosen)) if chosen else 0
    if len(chosen) < params.dong_min or not _amount_ok(median):
        return None
    return {"kind": "sameDong", "months": params.months, "n": len(chosen),
            "medianPerM2": median, "deals": [d.shown() for d in chosen]}


def _pnu_dong(value: object) -> str | None:
    """LOW-4: the 읍면동 code of a 19-digit PNU (Phase 2 keeps only current, address-checked PNUs)."""
    return value[:8] + "00" if pnu.item_key(value) else None  # type: ignore[index]


def compare_item(item, index: DealIndex, params: Params) -> dict:
    """`compare` of one NormalizedItem (record, rtms, usage_code)."""
    api = item.rtms
    if not api or api not in params.item_area:
        return dict(NONE)
    record = item.record
    area = record.get(params.item_area[api])
    if not area:
        return dict(NONE)
    pool = index.get((api, record["sgg"]), ())
    badge = api in params.building_apis and record["usage"]["group"] in params.building_groups
    found = _same_building(record, area, pool, params) if badge else None
    return found or _same_dong(record, api, item.usage_code, area, pool, params) or dict(NONE)
