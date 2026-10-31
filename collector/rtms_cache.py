"""Real-transaction summary cache on the data branch (plan D17, research §3.2).

    cache/rtms/<api>/<lawd>/<yyyymm>.json
        {"schemaVersion": 1, "api", "lawd", "ym", "fetched": "2026-10-31", "rows": [...]}

Only summary rows (rtms_client.ROW_FIELDS: public deal facts without
names or lot numbers beyond the building key), never raw responses; the
app does not read `cache/`. A month with zero deals is still a file — it
records that the month was fetched. The window is the current month plus
the 11 before (KST); files outside it are not carried over (deleted with
the next orphan commit). A broken file is skipped, counted and refetched.
Reading is bounded (file size, rows, file count, nesting depth, a real
`fetched` date) and refuses symlinks.
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
from collector.rtms_client import MAX_MONTH_ROWS, ROW_FIELDS, TEXT_LIMIT
from collector.values import MAX_AMOUNT, MAX_AREA

ROOT = "cache/rtms"
_PATH = re.compile(r"cache/rtms/([a-z]{2,4})/([0-9]{5})/([0-9]{4}(?:0[1-9]|1[0-2]))\.json")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_DONG = re.compile(r"[0-9]{10}")
_KEY = re.compile(r"[0-9]{18}")

Slot = tuple[str, str, str]   # (api, lawd, yyyymm)

# Security LOW-1: limits for reading yesterday's cache (about 11,300 files are
# expected; a month has at most 10,000 rows of about 90 bytes).
MAX_FILE_BYTES = 1024 * 1024
MAX_ROWS = MAX_MONTH_ROWS
MAX_FILES = 40_000


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


def _fetched_ok(fetched: object, ym: str, today: datetime.date) -> bool:
    """LOW-2: a real date on or after the first day of the month, not after today."""
    if not isinstance(fetched, str) or not _DATE.fullmatch(fetched):
        return False
    try:
        day = datetime.date.fromisoformat(fetched)
    except ValueError:
        return False
    return datetime.date(int(ym[:4]), int(ym[4:]), 1) <= day <= today


def parse_month(path: str, data: object, today: datetime.date) -> Month | None:
    match = _PATH.fullmatch(path)
    if not match or not isinstance(data, dict):
        return None
    api, lawd, ym = match.groups()
    if api not in RTMS_APIS or data.get("schemaVersion") != SCHEMA_VERSION:
        return None
    if (data.get("api"), data.get("lawd"), data.get("ym")) != (api, lawd, ym):
        return None
    rows = data.get("rows")
    if not _fetched_ok(data.get("fetched"), ym, today) or not isinstance(rows, list) or len(rows) > MAX_ROWS:
        return None
    if not all(_row_ok(api, row) for row in rows):
        return None
    return Month(api, lawd, ym, data["fetched"], tuple(rows))


def read_json(path: str, max_bytes: int) -> object:
    """JSON of a small trusted-shape file; ValueError when too big, broken or too deeply nested."""
    if os.path.getsize(path) > max_bytes:
        raise ValueError("file too large")
    with open(path, encoding="utf-8") as handle:
        try:
            return json.load(handle)
        except RecursionError:
            raise ValueError("JSON nested too deeply") from None


def _root(folder: str) -> str:
    """LOW-3: `cache/rtms` must be a real folder inside [folder] (no symlinks on the way)."""
    cache, base = os.path.join(folder, "cache"), os.path.join(folder, *ROOT.split("/"))
    if os.path.islink(cache) or os.path.islink(base):
        raise ValueError("cache/rtms must not be a symlink")
    real_folder, real_base = os.path.realpath(folder), os.path.realpath(base)
    if os.path.commonpath([real_folder, real_base]) != real_folder:
        raise ValueError("cache/rtms is outside the previous copy")
    return base


def _load_file(folder: str, full: str, today: datetime.date) -> Month | None:
    if os.path.islink(full):
        return None
    rel = os.path.relpath(full, folder).replace(os.sep, "/")
    try:
        return parse_month(rel, read_json(full, MAX_FILE_BYTES), today)
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def load_folder(folder: str, today: datetime.date) -> tuple[dict[Slot, Month], int]:
    """(cache, number of skipped files) from `<folder>/cache/rtms/`.

    ValueError (the run fails) for a symlinked or escaping root or more than
    MAX_FILES files; a single bad, oversized or odd file is skipped and counted.
    """
    base = _root(folder)
    cache: dict[Slot, Month] = {}
    bad = seen = 0
    for current, dirs, names in os.walk(base):
        dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(current, d)))
        for name in sorted(names):
            seen += 1
            if seen > MAX_FILES:
                raise ValueError(f"more than {MAX_FILES} files under {ROOT}")
            entry = _load_file(folder, os.path.join(current, name), today)
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
