"""Where an Onbid item is, in current legal-dong codes (plan D8, A3).

Order: ① address fields (`lctnSdnm`/`lctnSggnm`/`lctnEmdNm`, already in
current names) ② `ltnoPnu` (old prefixes mapped by region_map.json)
③ the address at the start of the item name. Split reorganisations
(인천 2026-06-30, 화성 2026-02-01) are resolved by dong name among the new
districts. A record nothing resolves is dropped by the caller (logged).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from collector.config import DATA_DIR

SEJONG_SIDO = "36"
SEJONG_SGG_NAME = "세종시"
_PNU = re.compile(r"\d{19}")


@dataclass(frozen=True)
class Location:
    sgg: str
    umd: str | None
    emd_name: str | None


def _load(name: str) -> dict:
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as handle:
        return json.load(handle)


def _clean(text: object) -> str:
    return re.sub(r"\s+", "", text) if isinstance(text, str) else ""


def _unique_index(ris: Mapping[str, str]) -> dict[tuple[str, str], str | None]:
    """(dong prefix, village name) -> code; None when the name repeats."""
    index: dict[tuple[str, str], str | None] = {}
    for code, name in ris.items():
        slot = (code[:8], name)
        index[slot] = None if slot in index else code
    return index


@dataclass(frozen=True)
class RegionIndex:
    sidos: Mapping[str, str]
    sggs: Mapping[str, Mapping]
    emds: Mapping[str, str]
    ris: Mapping[str, str]
    one_to_one: Mapping[str, str]
    split: Mapping[str, tuple[str, ...]]
    sido_by_name: Mapping[str, str]
    sgg_by_name: Mapping[tuple[str, str], str]
    emd_by_name: Mapping[tuple[str, str], str]
    ri_by_name: Mapping[tuple[str, str], str | None]

    @staticmethod
    def load(regions: dict | None = None, region_map: dict | None = None) -> "RegionIndex":
        regions = regions or _load("regions.json")
        region_map = region_map or _load("region_map.json")
        sggs = regions["sggs"]
        freeze = MappingProxyType
        return RegionIndex(
            sidos=freeze(dict(regions["sidos"])),
            sggs=freeze(dict(sggs)),
            emds=freeze(dict(regions["emds"])),
            ris=freeze(dict(regions["ris"])),
            one_to_one=freeze({old: v["to"] for old, v in region_map["oneToOne"].items()}),
            split=freeze({old: tuple(v["to"]) for old, v in region_map["split"].items()}),
            sido_by_name=freeze({name: code for code, name in regions["sidos"].items()}),
            sgg_by_name=freeze({(v["sido"], v["name"]): code for code, v in sggs.items()}),
            emd_by_name=freeze({(code[:5], name): code for code, name in regions["emds"].items()}),
            ri_by_name=freeze(_unique_index(regions["ris"])),
        )

    # Lookups ---------------------------------------------------------------

    def is_selectable(self, sgg: str) -> bool:
        entry = self.sggs.get(sgg)
        return entry is not None and not entry["parent"]

    def sido_name(self, sido: str) -> str:
        return self.sidos[sido]

    def sido_codes(self) -> tuple[str, ...]:
        return tuple(sorted(self.sidos))

    def emd_code(self, sgg: str, name: str | None) -> str | None:
        return self.emd_by_name.get((sgg, name)) if name else None

    def ri_code(self, umd: str, name: str) -> str | None:
        return self.ri_by_name.get((umd[:8], name))

    def _children(self, parent: str) -> tuple[str, ...]:
        prefix = self.sggs[parent]["name"]
        sido = self.sggs[parent]["sido"]
        return tuple(sorted(code for code, v in self.sggs.items()
                            if v["sido"] == sido and code != parent and v["name"].startswith(prefix)))

    def _siblings(self, sgg: str) -> tuple[str, ...]:
        groups = [targets for targets in self.split.values() if sgg in targets]
        return tuple(sorted({code for group in groups for code in group if code != sgg}))

    def _unique_dong(self, candidates: tuple[str, ...], emd: str | None) -> Location | None:
        found = [(sgg, self.emd_code(sgg, emd)) for sgg in candidates if self.emd_code(sgg, emd)]
        if len(found) != 1:
            return None
        sgg, umd = found[0]
        return Location(sgg, umd, emd)

    # Resolution ------------------------------------------------------------

    def locate(self, row: Mapping) -> Location | None:
        emd = row.get("lctnEmdNm") if isinstance(row.get("lctnEmdNm"), str) else None
        return (self._by_address(row.get("lctnSdnm"), row.get("lctnSggnm"), emd)
                or self._by_pnu(row.get("ltnoPnu"), emd)
                or self._by_name(row.get("onbidCltrNm")))

    def _by_address(self, sido_name: object, sgg_name: object, emd: str | None) -> Location | None:
        sido = self.sido_by_name.get(_clean(sido_name))
        if sido is None:
            return None
        name = _clean(sgg_name) or (SEJONG_SGG_NAME if sido == SEJONG_SIDO else "")
        sgg = self.sgg_by_name.get((sido, name))
        if sgg is None:
            return None
        if self.sggs[sgg]["parent"]:
            return self._unique_dong(self._children(sgg), emd)
        umd = self.emd_code(sgg, emd)
        if umd:
            return Location(sgg, umd, emd)
        return self._unique_dong(self._siblings(sgg), emd) or Location(sgg, None, emd or None)

    def _by_pnu(self, pnu: object, emd: str | None) -> Location | None:
        if not isinstance(pnu, str) or not _PNU.fullmatch(pnu):
            return None
        prefix = pnu[:5]
        if self.is_selectable(prefix):
            umd = pnu[:8] + "00"
            return Location(prefix, umd, self.emds[umd]) if umd in self.emds else Location(prefix, None, emd)
        if prefix in self.one_to_one:
            new = self.one_to_one[prefix]
            umd = new + pnu[5:8] + "00"
            return Location(new, umd, self.emds[umd]) if umd in self.emds else Location(new, None, emd)
        if prefix in self.split:
            return self._unique_dong(self.split[prefix], emd)
        return None

    def _by_name(self, item_name: object) -> Location | None:
        words = item_name.split() if isinstance(item_name, str) else []
        if len(words) < 3:
            return None
        one_word = self._by_address(words[0], words[1], words[2])
        if one_word and one_word.umd:
            return one_word
        if len(words) >= 4:
            two_words = self._by_address(words[0], words[1] + words[2], words[3])
            if two_words and two_words.umd:
                return two_words
        return None
