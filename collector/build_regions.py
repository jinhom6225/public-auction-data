"""Build collector/data/regions.json from the legal-dong CSV.

The input is the same file the app bundle is built from
(public_auction_alert `tool/data/legal_dong_20260630.csv`, 공공데이터포털
15063424, UTF-8 with BOM). The app keeps names only; the collector also
needs the 10-digit dong codes, so it gets its own shape:

    sidos: {"11": "서울특별시", ...}
    sggs:  {"41110": {"name": "수원시", "sido": "41", "parent": true}, ...}
    emds:  {"1111010100": "청운동", ...}   (읍면동)
    ris:   {"4182025021": "대곡리", ...}   (리, for PNUs of 읍·면 parcels)

`parent` marks a city that has general districts (일반구): it cannot be
chosen in a condition and items are filed under the district code (D9).

Usage:
    python3 -m collector.build_regions <csv> <out.json> <dataDate YYYY-MM-DD>
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sys

DATASET = "15063424"
SCHEMA_VERSION = 1
HEADER = ["법정동코드", "시도명", "시군구명", "읍면동명", "리명", "순번", "생성일자"]
CODE = re.compile(r"\d{10}")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _rows(text: str) -> list[list[str]]:
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    rows = [row for row in reader if row]
    if not rows or rows[0] != HEADER:
        raise ValueError("unexpected legal-dong CSV header")
    for row in rows[1:]:
        if len(row) != len(HEADER) or not CODE.fullmatch(row[0]):
            raise ValueError("unexpected legal-dong CSV row")
    return sorted(rows[1:], key=lambda row: row[0])


def _parents(sggs: dict[str, dict]) -> set[str]:
    by_sido: dict[str, list[tuple[str, str]]] = {}
    for code, sgg in sggs.items():
        by_sido.setdefault(sgg["sido"], []).append((code, sgg["name"]))
    parents = set()
    for entries in by_sido.values():
        for code, name in entries:
            if any(other != name and other.startswith(name) for _, other in entries):
                parents.add(code)
    return parents


def build(text: str, source: dict[str, str]) -> dict:
    """Regions dict for the CSV [text]; raises ValueError on a bad file."""
    sidos: dict[str, str] = {}
    sggs: dict[str, dict] = {}
    emds: dict[str, str] = {}
    ris: dict[str, str] = {}
    for code, sido, sgg, emd, ri, *_ in _rows(text):
        if code[2:] == "00000000":
            sidos[code[:2]] = sido
        elif code[5:] == "00000":
            sggs[code[:5]] = {"name": sgg, "sido": code[:2], "parent": False}
        elif code[8:] == "00":
            emds[code] = emd
        else:
            ris[code] = ri
    for code in emds:
        if code[:5] not in sggs:
            raise ValueError(f"dong {code} has no district row")
    for code in ris:
        if code[:8] + "00" not in emds:
            raise ValueError(f"village {code} has no dong row")
    parents = _parents(sggs)
    for code in parents:
        sggs[code] = {**sggs[code], "parent": True}
    return {
        "schemaVersion": SCHEMA_VERSION,
        "source": {"dataset": DATASET, **source},
        "sidos": sidos,
        "sggs": sggs,
        "emds": emds,
        "ris": ris,
    }


def dumps(regions: dict) -> str:
    """Deterministic JSON text with a final newline."""
    return json.dumps(regions, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 4 or not DATE.fullmatch(argv[3]):
        print("usage: python3 -m collector.build_regions <csv> <out.json> <YYYY-MM-DD>", file=sys.stderr)
        return 64
    with open(argv[1], "rb") as handle:
        raw = handle.read()
    source = {
        "file": os.path.basename(argv[1]),
        "dataDate": argv[3],
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    regions = build(raw.decode("utf-8"), source)
    with open(argv[2], "w", encoding="utf-8", newline="\n") as handle:
        handle.write(dumps(regions))
    print(f"sidos={len(regions['sidos'])} sggs={len(regions['sggs'])} emds={len(regions['emds'])} ris={len(regions['ris'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
