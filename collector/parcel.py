"""19-digit parcel number (PNU) of an item, in current codes (plan A3, A7).

PNU = legal-dong code (10, incl. 리) + mountain flag (1, Onbid 0/1) +
main lot (4) + sub lot (4). Onbid `ltnoPnu` is used first; an old prefix
is mapped (one-to-one: first five digits; split: the dong from the
address). Without `ltnoPnu`, the lot is read from the item name
(`<읍면동> [리] [산] 본번[-부번]`, A7 ④) — 94% agreement in Phase 1.
"""

from __future__ import annotations

import re

from collector.region_index import Location, RegionIndex

_PNU = re.compile(r"\d{19}")


def _known_dong(code: str, index: RegionIndex) -> bool:
    return code in index.emds or code in index.ris


def from_onbid_pnu(raw: object, location: Location, index: RegionIndex) -> str | None:
    if not isinstance(raw, str) or not _PNU.fullmatch(raw):
        return None
    if _known_dong(raw[:10], index) and index.is_selectable(raw[:5]):
        return raw
    prefix = raw[:5]
    if prefix in index.one_to_one:
        mapped = index.one_to_one[prefix] + raw[5:]
        return mapped if _known_dong(mapped[:10], index) else None
    if prefix in index.split and location.umd:
        return location.umd + raw[10:]
    return None


def _lot_pattern(emd_name: str) -> re.Pattern[str]:
    return re.compile(
        r"(?:^|\s)" + re.escape(emd_name)
        + r"\s+(?:(\S+리)\s+)?(산\s*)?(\d{1,4})(?:-(\d{1,4}))?(?![\d-])"
    )


def from_item_name(name: object, location: Location, index: RegionIndex) -> str | None:
    if not isinstance(name, str) or not location.umd or not location.emd_name:
        return None
    match = _lot_pattern(location.emd_name).search(name)
    if not match:
        return None
    village, mountain, main, sub = match.groups()
    dong = location.umd
    if village:
        dong = index.ri_code(location.umd, village)
        if dong is None:
            return None
    return dong + ("1" if mountain else "0") + main.zfill(4) + (sub or "0").zfill(4)


def pnu_of(row: dict, location: Location, index: RegionIndex) -> str | None:
    return (from_onbid_pnu(row.get("ltnoPnu"), location, index)
            or from_item_name(row.get("onbidCltrNm"), location, index))
