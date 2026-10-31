"""Regenerate the golden copies pinned by test_collect (on purpose only).

    python3 -I collector/dev/regen_golden.py

Runs the collector offline on the fixture folders and writes:
  fixtures/golden/                    real pages, first run at 2026-10-31T20:10:00Z
  fixtures/golden_boundary/day1, day2 hand-made edge cases, day 2 with --prev day 1
Each golden keeps `v1/` and `cache/*.json` byte for byte; the many
`cache/rtms/` month files are pinned by `rtms_manifest.json`
(rtms_cache.manifest) instead of being copied.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")))

from collector import collect, rtms_cache  # noqa: E402

FIXTURES = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests", "fixtures"))
MANIFEST = "rtms_manifest.json"


def read_tree(folder: str) -> dict[str, bytes]:
    files = {}
    for root, _, names in os.walk(folder):
        for name in names:
            path = os.path.join(root, name)
            with open(path, "rb") as handle:
                files[os.path.relpath(path, folder).replace(os.sep, "/")] = handle.read()
    return files


def write_golden(out: str, golden: str) -> None:
    files = read_tree(out)
    shutil.rmtree(golden, ignore_errors=True)
    for path, data in files.items():
        if path.startswith(rtms_cache.ROOT + "/"):
            continue
        target = os.path.join(golden, *path.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(data)
    with open(os.path.join(golden, MANIFEST), "w", encoding="utf-8") as handle:
        json.dump(rtms_cache.manifest(files), handle, ensure_ascii=False, indent=1, sort_keys=True)
        handle.write("\n")


def run(*args: str) -> None:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        code = collect.main(["collect", *args], env={})
    if code != 0:
        raise SystemExit(f"collect failed ({code}):\n{buffer.getvalue()}")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        real = os.path.join(tmp, "real")
        run("--out", real, "--first-run", "--offline-fixtures", os.path.join(FIXTURES, "onbid_pages"),
            "--now", "2026-10-31T20:10:00Z")
        write_golden(real, os.path.join(FIXTURES, "golden"))
        day1, day2 = os.path.join(tmp, "day1"), os.path.join(tmp, "day2")
        boundary = os.path.join(FIXTURES, "boundary")
        run("--out", day1, "--first-run", "--offline-fixtures", os.path.join(boundary, "day1"),
            "--now", "2026-10-31T20:10:00Z")
        run("--out", day2, "--prev", day1, "--offline-fixtures", os.path.join(boundary, "day2"),
            "--now", "2026-11-01T20:10:00Z")
        write_golden(day1, os.path.join(FIXTURES, "golden_boundary", "day1"))
        write_golden(day2, os.path.join(FIXTURES, "golden_boundary", "day2"))
    print("regen_golden: golden, golden_boundary/day1, golden_boundary/day2 written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
