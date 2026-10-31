"""Building keys for the same-building comparison (research §3.4, plan Phase 3).

Key = legal-dong code (10, incl. 리) + main lot (4) + sub lot (4). The
11th PNU digit (mountain flag) is left out: Onbid writes 0/1, MOLIT
`landCd` 1/2 (A7 ⑤). Items: `pnu[:10] + pnu[11:]`. MOLIT rows: apartment
detail `queried LAWD_CD + umdCd + bonbun + bubun` (`jibun` as fallback),
row-house/officetel `LAWD_CD + umdNm -> code + jibun "본번-부번"`.
Masked lots (`2**`, `산1*`) are never keys.
"""

from __future__ import annotations

import re
from typing import Mapping

_PNU = re.compile(r"[0-9]{19}")
_DONG = re.compile(r"[0-9]{10}")
_JIBUN = re.compile(r"(?:산\s*)?([0-9]{1,4})(?:-([0-9]{1,4}))?")
_LOT_PART = re.compile(r"[0-9]{1,4}")

Lot = tuple[str, str]


def item_key(value: object) -> str | None:
    """Key of an Onbid item PNU (19 ASCII digits), or None."""
    if not isinstance(value, str) or not _PNU.fullmatch(value):
        return None
    return value[:10] + value[11:]


def _lot(main: str, sub: str | None) -> Lot | None:
    lot = (main.zfill(4), (sub or "0").zfill(4))
    return None if lot[0] == "0000" else lot


def jibun_lot(text: object) -> Lot | None:
    """(main, sub) of "645-32", "1022" or "산1-9"; masked or odd text gives None."""
    if not isinstance(text, str):
        return None
    match = _JIBUN.fullmatch(text.strip())
    return _lot(match.group(1), match.group(2)) if match else None


def apt_lot(row: Mapping[str, str]) -> Lot | None:
    """Apartment detail: `bonbun`/`bubun` (4 digits each), else `jibun`."""
    main, sub = row.get("bonbun", ""), row.get("bubun", "")
    if _LOT_PART.fullmatch(main or "") and _LOT_PART.fullmatch(sub or ""):
        return _lot(main, sub)
    return jibun_lot(row.get("jibun"))


def building_key(dong: str | None, lot: Lot | None) -> str | None:
    if dong is None or lot is None or not _DONG.fullmatch(dong):
        return None
    return dong + lot[0] + lot[1]
