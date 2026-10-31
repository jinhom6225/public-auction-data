"""Whole-output check before publishing (plan D11, D13, D29 ④).

Same rules the app applies when it reads the copy: index shape and
`schemaVersion`, `generatedAt`, `regions[].file`, sha256, per-file caps,
item counts, keys sorted and unique, and zero record problems. collect.py
runs it before writing; the workflow runs it again as its own step:

    python3 -m collector.validate_output <out_dir>
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import sys
from typing import Mapping

from collector.config import MAX_FILE_BYTES, MAX_INDEX_BYTES, SCHEMA_VERSION
from collector.region_index import RegionIndex
from collector.validate import generated_at_problems, record_problems, region_file_ok

INDEX_PATH = "v1/index.json"


def _json(data: bytes, path: str, problems: list[str]) -> object:
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        problems.append(f"{path} is not JSON")
        return None


def _items_problems(path: str, data: bytes, entry: Mapping, generated: str, index: RegionIndex) -> list[str]:
    problems: list[str] = []
    if len(data) > MAX_FILE_BYTES:
        problems.append(f"{path} over the 5MB cap")
    if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
        problems.append(f"{path} sha256 differs from index")
    doc = _json(data, path, problems)
    if not isinstance(doc, dict) or not isinstance(doc.get("items"), list):
        return problems + [f"{path} has no items list"]
    if doc.get("schemaVersion") != SCHEMA_VERSION or doc.get("sido") != entry["sido"]:
        problems.append(f"{path} has a wrong schemaVersion or sido")
    if doc.get("generatedAt") != generated:
        problems.append(f"{path} generatedAt differs from index")
    items = doc["items"]
    if entry.get("count") != len(items):
        problems.append(f"{path} count differs from index")
    keys = [item.get("key") if isinstance(item, dict) else None for item in items]
    if not all(isinstance(k, str) for k in keys) or keys != sorted(set(keys)):
        problems.append(f"{path} keys are not sorted and unique")
    for item, key in zip(items, keys):
        problems += [f"{path} {key}: {p}" for p in record_problems(item, entry["sido"], index)]
    return problems


def _index_problems(doc: object, now: datetime.datetime, index: RegionIndex) -> list[str]:
    if not isinstance(doc, dict) or not isinstance(doc.get("regions"), list):
        return ["index has no regions list"]
    problems = generated_at_problems(doc.get("generatedAt"), now)
    if doc.get("schemaVersion") != SCHEMA_VERSION:
        problems.append("index schemaVersion is not 1")
    sidos = [e.get("sido") if isinstance(e, dict) else None for e in doc["regions"]]
    if sidos != list(index.sido_codes()):
        problems.append("index regions must list every current province once, in order")
    for entry in doc["regions"]:
        if not region_file_ok(entry):
            problems.append(f"bad regions[].file entry {str(entry)[:80]}")
        elif entry.get("name") != index.sidos.get(entry["sido"]):
            problems.append(f"index name of {entry['sido']} differs")
    return problems


def tree_problems(files: Mapping[str, bytes], now: datetime.datetime, index: RegionIndex) -> list[str]:
    """Problems of the `v1/` files in [files] (paths relative to the out dir)."""
    if INDEX_PATH not in files:
        return [f"missing {INDEX_PATH}"]
    problems: list[str] = []
    if len(files[INDEX_PATH]) > MAX_INDEX_BYTES:
        problems.append("index over the 256KB cap")
    doc = _json(files[INDEX_PATH], INDEX_PATH, problems)
    problems += _index_problems(doc, now, index)
    if not isinstance(doc, dict) or not isinstance(doc.get("regions"), list):
        return problems
    expected = {INDEX_PATH}
    for entry in filter(region_file_ok, doc["regions"]):
        path = "v1/" + entry["file"]
        expected.add(path)
        if path not in files:
            problems.append(f"missing {path}")
            continue
        problems += _items_problems(path, files[path], entry, doc.get("generatedAt"), index)
    extra = sorted(p for p in files if p.startswith("v1/") and p not in expected)
    return problems + [f"unexpected file {p}" for p in extra]


def read_tree(out_dir: str) -> dict[str, bytes]:
    files = {}
    for folder, _, names in os.walk(os.path.join(out_dir, "v1")):
        for name in names:
            path = os.path.join(folder, name)
            with open(path, "rb") as handle:
                files[os.path.relpath(path, out_dir).replace(os.sep, "/")] = handle.read()
    return files


def main(argv: list[str], now: datetime.datetime | None = None) -> int:
    if len(argv) != 2:
        print("usage: python3 -m collector.validate_output <out_dir>", file=sys.stderr)
        return 64
    files = read_tree(argv[1])
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    problems = tree_problems(files, moment, RegionIndex.load())
    for problem in problems[:50]:
        print(f"invalid output: {problem}", file=sys.stderr)
    print(f"validate_output: {len(files)} files, {len(problems)} problems")
    return 2 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
