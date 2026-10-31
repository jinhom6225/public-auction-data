"""Real-transaction summary cache on the data branch (plan D17, research §3.2).

    cache/rtms/<api>/<lawd>/<yyyymm>.json
        {"schemaVersion": 1, "api", "lawd", "ym", "fetched": "2026-10-31", "rows": [...]}

Only summary rows (rtms_client.ROW_FIELDS: public deal facts without
names or lot numbers beyond the building key), never raw responses; the
app does not read `cache/`. A month with zero deals is still a file — it
records that the month was fetched. The window is the current month plus
the 11 before (KST); files outside it are not carried over (deleted with
the next orphan commit). A broken file is skipped, counted and refetched.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Mapping

from collector.config import RTMS_APIS, SCHEMA_VERSION
from collector.rtms_client import ROW_FIELDS, TEXT_LIMIT
from collector.values import MAX_AMOUNT, MAX_AREA

ROOT = "cache/rtms"
_PATH = re.compile(r"cache/rtms/([a-z]{2,4})/([0-9]{5})/([0-9]{4}(?:0[1-9]|1[0-2]))\.json")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_DONG = re.compile(r"[0-9]{10}")
_KEY = re.compile(r"[0-9]{18}")

Slot = tuple[str, str, str]   # (api, lawd, yyyymm)


@dataclass(frozen=True)
class Month:
    api: str
    lawd: str
    ym: str
    fetched: str
    rows: tuple[Mapping, ...]

    @property
    def slot(self) -> Slot:
        return (self.api, self.lawd, self.ym)


Cache = Mapping[Slot, Month]


def window(today: datetime.date, months: int) -> tuple[str, ...]:
    """yyyymm of the current month and the months - 1 before, newest first."""
    year, month = today.year, today.month
    out = []
    for _ in range(months):
        out.append(f"{year:04d}{month:02d}")
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return tuple(out)


def in_window(cache: Cache, months: tuple[str, ...]) -> dict[Slot, Month]:
    keep = set(months)
    return {slot: entry for slot, entry in cache.items() if slot[2] in keep}


def path_of(slot: Slot) -> str:
    return f"{ROOT}/{slot[0]}/{slot[1]}/{slot[2]}.json"


def _row_sort_key(row: Mapping) -> str:
    return json.dumps(row, sort_keys=True, ensure_ascii=False)


def render(cache: Cache) -> dict[str, bytes]:
    files = {}
    for slot in sorted(cache):
        entry = cache[slot]
        doc = {"schemaVersion": SCHEMA_VERSION, "api": entry.api, "lawd": entry.lawd, "ym": entry.ym,
               "fetched": entry.fetched, "rows": sorted((dict(r) for r in entry.rows), key=_row_sort_key)}
        text = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        files[path_of(slot)] = text.encode("utf-8") + b"\n"
    return files


# Loading ------------------------------------------------------------------

def _int_in(value: object, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _optional(value: object, pattern: re.Pattern[str] | None = None) -> bool:
    if value is None:
        return True
    if not isinstance(value, str) or not value:
        return False
    return bool(pattern.fullmatch(value)) if pattern else len(value) <= TEXT_LIMIT


_FIELD_CHECKS = {
    "d": lambda v: _int_in(v, 1, 31),
    "p": lambda v: _int_in(v, 1, MAX_AMOUNT),
    "a": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v <= MAX_AREA,
    "f": lambda v: v is None or _int_in(v, -999, 999),
    "u": lambda v: _optional(v, _DONG),
    "k": lambda v: _optional(v, _KEY),
    "j": _optional,
    "b": _optional,
}


def _row_ok(api: str, row: object) -> bool:
    fields = ROW_FIELDS[api]
    return isinstance(row, dict) and tuple(sorted(row)) == tuple(sorted(fields)) and all(
        _FIELD_CHECKS[name](row[name]) for name in fields)


def parse_month(path: str, data: object) -> Month | None:
    match = _PATH.fullmatch(path)
    if not match or not isinstance(data, dict):
        return None
    api, lawd, ym = match.groups()
    if api not in RTMS_APIS or data.get("schemaVersion") != SCHEMA_VERSION:
        return None
    if (data.get("api"), data.get("lawd"), data.get("ym")) != (api, lawd, ym):
        return None
    fetched, rows = data.get("fetched"), data.get("rows")
    if not isinstance(fetched, str) or not _DATE.fullmatch(fetched) or not isinstance(rows, list):
        return None
    if not all(_row_ok(api, row) for row in rows):
        return None
    return Month(api, lawd, ym, fetched, tuple(rows))


def _read(path: str) -> object:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def load_folder(folder: str) -> tuple[dict[Slot, Month], int]:
    """(cache, number of skipped files) from `<folder>/cache/rtms/`."""
    base = os.path.join(folder, *ROOT.split("/"))
    cache: dict[Slot, Month] = {}
    bad = 0
    for current, dirs, names in os.walk(base):
        dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(current, d)))
        for name in sorted(names):
            full = os.path.join(current, name)
            rel = os.path.relpath(full, folder).replace(os.sep, "/")
            entry = None
            if not os.path.islink(full):
                try:
                    entry = parse_month(rel, _read(full))
                except (OSError, UnicodeDecodeError, ValueError):
                    entry = None
            if entry is None:
                bad += 1
            else:
                cache[entry.slot] = entry
    return cache, bad


def manifest(files: Mapping[str, bytes]) -> dict:
    """Short fingerprint of the `cache/rtms/` files in [files] (golden pins, runbook checks).

    {"files": n, "rows": total, "sha256": digest over (path, sha256) pairs, "nonEmpty": {path: rows}}
    """
    paths = sorted(path for path in files if path.startswith(ROOT + "/"))
    digest = hashlib.sha256()
    non_empty: dict[str, int] = {}
    total = 0
    for path in paths:
        digest.update(path.encode() + b"\0" + hashlib.sha256(files[path]).hexdigest().encode() + b"\n")
        rows = len(json.loads(files[path].decode("utf-8"))["rows"])
        total += rows
        if rows:
            non_empty[path] = rows
    return {"files": len(paths), "rows": total, "sha256": digest.hexdigest(), "nonEmpty": non_empty}
