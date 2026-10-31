"""Cut small MOLIT fixtures for an offline fixture folder from saved real pages.

Dev tool only (tests never read the raw pages). The raw pages are the
uncommitted A9 measurement responses (`.local/a9/raw/rtms_<api>_<lawd>_<ym>_p<N>.xml`).

    python3 -I collector/dev/cut_rtms_fixture.py <raw_dir> <offline_dir> <now ISO-8601>

Normalises `<offline_dir>/onbid_p1.json` at <now>, and for every (API, sgg)
of its items and every month of the 12-month window writes
`<offline_dir>/rtms_<api>_<lawd>_<ym>.xml` with only the deals that decide
an item's comparison: every deal in the item's building (any area) and the
same-dong deals the full data chose. The kept set contains every chosen
deal, so newest-first selection and medians are the same as with the full
pages (the golden `v1/` must not change when this tool is re-run). Months with no kept deal
get no file (the offline transport serves an empty page). Each kept item
keeps only the fields the collector reads (KEEP, plus the building name
for readability), values as received; totalCount is the kept count. Refuses to write a
body that contains `serviceKey`.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")))

from collector import compare, pnu, rtms_cache, rtms_client  # noqa: E402
from collector.config import KST  # noqa: E402
from collector.normalize_item import Context, normalize_all  # noqa: E402

KEEP = ("sggCd", "umdCd", "umdNm", "jibun", "bonbun", "bubun", "landCd", "dealYear", "dealMonth", "dealDay",
        "dealAmount", "cdealType", "excluUseAr", "dealArea", "totalFloorAr", "buildingAr", "buildingUse",
        "jimok", "floor", "shareDealingType", "aptNm", "offiNm", "mhouseNm")
HEAD = ('<?xml version="1.0" encoding="utf-8" standalone="yes"?><response><header><resultCode>000</resultCode>'
        '<resultMsg>OK</resultMsg></header><body><items>')
TAIL = '</items><numOfRows>1000</numOfRows><pageNo>1</pageNo><totalCount>{}</totalCount></body></response>'


def wanted(offline_dir: str, ctx: Context) -> dict[tuple[str, str], list]:
    """(api, sgg) -> the fixture items that compare against it."""
    with open(os.path.join(offline_dir, "onbid_p1.json"), encoding="utf-8") as handle:
        rows = json.load(handle)["body"]["items"]["item"]
    pairs: dict[tuple[str, str], list] = {}
    for item in normalize_all(rows, ctx).items:
        if item.rtms:
            pairs.setdefault((item.rtms, item.record["sgg"]), []).append(item)
    return pairs


def raw_items(raw_dir: str, api: str, lawd: str, ym: str) -> list[ET.Element] | None:
    pages, page = [], 1
    while os.path.exists(path := os.path.join(raw_dir, f"rtms_{api}_{lawd}_{ym}_p{page}.xml")):
        with open(path, "rb") as handle:
            body = handle.read()
        rtms_client.parse_page(body, "fixture")          # same strict checks as the collector
        pages.extend(ET.fromstring(body).iter("item"))
        page += 1
    return pages if page > 1 else None


def _summary(api: str, lawd: str, ym: str, element: ET.Element, ctx: Context):
    row = rtms_client.summarize(api, lawd, {c.tag: (c.text or "").strip() for c in element}, ctx.regions)
    return None if row is None else compare.Deal(f"{ym[:4]}-{ym[4:]}", row["d"], row["p"], row["a"], row.get("f"),
                                                  row.get("u"), row.get("k"), row.get("j"), row.get("b"))


def _needed(items, deals: list, params) -> set[int]:
    """Indexes of [deals] that decide the comparison of [items]."""
    index = {(items[0].rtms, items[0].record["sgg"]): tuple(sorted(
        (d for d in deals if d is not None), key=compare._newest_first))}
    keep: set[int] = set()
    for item in items:
        key = pnu.item_key(item.record["pnu"])
        result = compare.compare_item(item, index, params)
        shown = {(x["ym"], x["m2"], x["floor"], x["amount"]) for x in result.get("deals", [])}
        for i, deal in enumerate(deals):
            if deal is None:
                continue
            if key and deal.key == key:
                keep.add(i)
            elif result["kind"] == "sameDong" and deal.umd == item.record["umd"] and \
                    (deal.ym, deal.area, deal.floor, deal.amount) in shown:
                keep.add(i)
    return keep


def cut(raw_dir: str, offline_dir: str, now: datetime.datetime) -> dict[str, int]:
    ctx = Context.load(now.astimezone(KST))
    params = compare.Params.load()
    written: dict[str, int] = {}
    for (api, lawd), items in sorted(wanted(offline_dir, ctx).items()):
        pages = {ym: raw_items(raw_dir, api, lawd, ym) for ym in rtms_cache.window(now.astimezone(KST).date(), 12)}
        flat = [(ym, element) for ym, elements in pages.items() for element in (elements or [])]
        deals = [_summary(api, lawd, ym, element, ctx) for ym, element in flat]
        keep = _needed(items, deals, params)
        for ym in pages:
            kept = [element for i, (month, element) in enumerate(flat) if month == ym and i in keep]
            if not kept:
                continue
            for element in kept:
                for child in [c for c in element if c.tag not in KEEP]:
                    element.remove(child)
            text = HEAD + "".join(ET.tostring(e, encoding="unicode") for e in kept) + TAIL.format(len(kept))
            if "serviceKey" in text:
                raise SystemExit("refusing a fixture with serviceKey")
            name = f"rtms_{api}_{lawd}_{ym}.xml"
            with open(os.path.join(offline_dir, name), "w", encoding="utf-8") as handle:
                handle.write(text)
            written[name] = len(kept)
    return written


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        sys.exit(__doc__)
    now = datetime.datetime.fromisoformat(argv[3].replace("Z", "+00:00"))
    written = cut(argv[1], argv[2], now)
    print(f"cut_rtms_fixture: {len(written)} files, {sum(written.values())} deals")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
