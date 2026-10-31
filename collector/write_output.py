"""Deterministic output files (plan D10, D29 ④⑥, research §3.3).

`render` turns the final records into bytes in memory:
    v1/index.json            every current province, sha256 of each file
    v1/items/<sido>.json     {"schemaVersion", "generatedAt", "sido", "items"}
    cache/seen.json          D16 history
    cache/budget.json        D17 call counts
JSON is compact, keys sorted, items sorted by `key`, final newline. All
16 provinces are listed (an empty one has count 0), so the app always
finds an entry for every condition province. `commit` writes the files
only after everything passed, via a staging folder.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from typing import Iterable, Mapping

from collector import seen_history
from collector.budget import Budget
from collector.config import MAX_FILE_BYTES, MAX_INDEX_BYTES, SCHEMA_VERSION, SOURCE_LABEL, SOURCE_URL
from collector.errors import ValidationFailed
from collector.region_index import RegionIndex

OWNED_TOP = ("v1", "cache")
_SAFE_PATH = re.compile(r"(v1|cache)(/[A-Za-z0-9_\-]+)*/[A-Za-z0-9_\-]+\.json")


def dumps(document: object) -> bytes:
    text = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return text.encode("utf-8") + b"\n"


def _by_province(records: Iterable[Mapping], index: RegionIndex) -> dict[str, list[Mapping]]:
    grouped: dict[str, list[Mapping]] = {sido: [] for sido in index.sido_codes()}
    for record in records:
        sido = record["sgg"][:2]
        if sido not in grouped:
            raise ValidationFailed(f"record {record['key']} is in unknown province {sido}")
        grouped[sido].append(record)
    return {sido: sorted(items, key=lambda r: r["key"]) for sido, items in grouped.items()}


def _capped(path: str, data: bytes, cap: int) -> bytes:
    if len(data) > cap:
        raise ValidationFailed(f"{path} is {len(data)} bytes, over the {cap}-byte cap")
    return data


def render(records: Iterable[Mapping], generated_at: str, index: RegionIndex,
           seen: Mapping[str, Mapping], budget: Budget) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    regions = []
    for sido, items in _by_province(records, index).items():
        name = f"items/{sido}.json"
        doc = {"schemaVersion": SCHEMA_VERSION, "generatedAt": generated_at, "sido": sido, "items": items}
        data = _capped(name, dumps(doc), MAX_FILE_BYTES)
        files["v1/" + name] = data
        regions.append({"sido": sido, "name": index.sido_name(sido), "file": name, "count": len(items),
                        "sha256": hashlib.sha256(data).hexdigest()})
    index_doc = {"schemaVersion": SCHEMA_VERSION, "generatedAt": generated_at,
                 "source": {"label": SOURCE_LABEL, "url": SOURCE_URL}, "regions": regions}
    files["v1/index.json"] = _capped("index.json", dumps(index_doc), MAX_INDEX_BYTES)
    files["cache/seen.json"] = dumps(seen_history.render(seen))
    files["cache/budget.json"] = dumps(budget.to_json())
    return files


def _check_paths(files: Mapping[str, bytes]) -> None:
    for path in files:
        if not _SAFE_PATH.fullmatch(path) or ".." in path:
            raise ValueError("refusing an output path outside v1/ and cache/")


def commit(files: Mapping[str, bytes], out_dir: str) -> None:
    """Write [files] under out_dir, replacing its v1/ and cache/ folders."""
    _check_paths(files)
    os.makedirs(out_dir, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=".staging-", dir=out_dir)
    try:
        for path, data in files.items():
            target = os.path.join(staging, *path.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(data)
        for top in OWNED_TOP:
            _swap(os.path.join(staging, top), os.path.join(out_dir, top))
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _swap(new: str, target: str) -> None:
    if not os.path.exists(new):
        return
    if os.path.exists(target):
        shutil.rmtree(target)
    os.replace(new, target)
