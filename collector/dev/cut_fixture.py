"""Cut a small, realistic Onbid page fixture from the Phase 1 full dump.

Dev tool only (tests never read the dump). The dump is the uncommitted
`public_auction_alert/tool/spike/.cache/onbid_all_N_p*_n5000.json`
(2026-10-31, pvctTrgtYn=N, all property divisions, ~115MB).

    python3 -I collector/dev/cut_fixture.py <dump_dir> <out_dir> [items_per_province]

Keeps sale rows (dspsMthodCd=0001, what the collector requests) of three
provinces — 11 서울 (plain), 12 전남광주 (old 29/46 PNUs), 28 인천 (split
districts) — choosing every special case first (private price, 2999
placeholder, share, missing PNU, open round, closed-only, mixed rounds,
old/split PNU, many rounds) and filling up in key order. All rows of a
chosen item are kept. Writes `onbid_p1.json` shaped like a real page with
totalCount = rows kept. Output is deterministic.
"""

from __future__ import annotations

import glob
import json
import os
import sys

PROVINCES = {"서울특별시": "11", "전남광주통합특별시": "12", "인천광역시": "28"}


def _load(dump_dir: str) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(glob.glob(os.path.join(dump_dir, "onbid_all_N_p*_n5000.json"))):
        with open(path, encoding="utf-8") as handle:
            rows.extend(json.load(handle)["body"]["items"]["item"])
    return [r for r in rows if r.get("dspsMthodCd") == "0001"]


SPECIAL = {
    "private": lambda rs: any(r["lowstBidPrcIndctCont"] == "비공개" for r in rs),
    "placeholder": lambda rs: any(str(r["cltrBidBgngDt"]).startswith("2999") for r in rs),
    "share": lambda rs: rs[0]["alcYn"] == "Y",
    "no_pnu": lambda rs: not rs[0]["ltnoPnu"],
    "open": lambda rs: any(r["pbctStatCd"] == "0002" for r in rs),
    "closed_only": lambda rs: all(r["pbctStatCd"] == "0003" for r in rs),
    "mixed": lambda rs: {r["pbctStatCd"] for r in rs} == {"0001", "0003"},
    "old_pnu": lambda rs: (rs[0]["ltnoPnu"] or "")[:2] in ("29", "46") or (rs[0]["ltnoPnu"] or "")[:5] in (
        "28110", "28140", "28260"),
    "many_rounds": lambda rs: len(rs) >= 6,
    "officetel": lambda rs: rs[0]["cltrUsgSclsCtgrId"] == "10503",
    "land": lambda rs: rs[0]["cltrUsgMclsCtgrId"] == "10100",
}


def _choose(groups: dict[str, list[dict]], per_province: int) -> list[str]:
    chosen: list[str] = []
    for test in SPECIAL.values():
        for key in sorted(groups):
            if key not in chosen and test(groups[key]):
                chosen.append(key)
                break
    rest = [key for key in sorted(groups) if key not in chosen]
    step = max(1, len(rest) // max(1, per_province - len(chosen)))
    chosen.extend(rest[::step][: max(0, per_province - len(chosen))])
    return sorted(chosen)


def cut(rows: list[dict], per_province: int) -> list[dict]:
    kept: list[dict] = []
    for sido in PROVINCES:
        groups: dict[str, list[dict]] = {}
        for row in rows:
            if row.get("lctnSdnm") == sido:
                groups.setdefault(row["cltrMngNo"], []).append(row)
        keys = set(_choose(groups, per_province))
        kept.extend(r for r in rows if r.get("lctnSdnm") == sido and r["cltrMngNo"] in keys)
    return kept


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print(__doc__, file=sys.stderr)
        return 64
    per_province = int(argv[3]) if len(argv) == 4 else 30
    rows = cut(_load(argv[1]), per_province)
    page = {"header": {"resultCode": "00", "resultMsg": "NORMAL_CODE"},
            "body": {"items": {"item": rows}, "numOfRows": 1000, "pageNo": 1, "totalCount": len(rows)}}
    os.makedirs(argv[2], exist_ok=True)
    with open(os.path.join(argv[2], "onbid_p1.json"), "w", encoding="utf-8") as handle:
        json.dump(page, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
    print(f"rows={len(rows)} items={len({r['cltrMngNo'] for r in rows})}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
