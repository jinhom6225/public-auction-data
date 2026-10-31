import json
import os
import tempfile
import unittest

import netguard  # noqa: F401  (blocks the network)

from collector import previous_copy
from collector.errors import ValidationFailed

INDEX = {"regions": [{"sido": "11", "count": 1}]}
SEEN = {"schemaVersion": 1, "items": {}}


class LoadTest(unittest.TestCase):
    """Security LOW-1: size caps and deep nesting in yesterday's copy fail cleanly (exit 2, not a crash)."""

    def folder(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for path, data in files.items():
            target = os.path.join(tmp.name, *path.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(data if isinstance(data, bytes) else json.dumps(data).encode())
        return tmp.name

    def base(self, **files):
        return {"v1/index.json": INDEX, "cache/seen.json": SEEN, **files}

    def test_good_copy(self):
        previous = previous_copy.load(self.folder(self.base(**{"cache/backfill_state.json": {"x": 1}})))
        self.assertEqual((previous.total, previous.backfill_state), (1, {"x": 1}))

    def test_deeply_nested_history_fails_cleanly(self):
        with self.assertRaises(ValidationFailed):
            previous_copy.load(self.folder(self.base(**{"cache/seen.json": b"[" * 50000 + b"]" * 50000})))

    def test_oversized_index_fails_cleanly(self):
        big = json.dumps(INDEX).encode()[:-1] + b" " * (300 * 1024) + b"}"
        with self.assertRaises(ValidationFailed):
            previous_copy.load(self.folder(self.base(**{"v1/index.json": big})))

    def test_odd_optional_files_are_ignored(self):
        files = self.base(**{"cache/budget.json": b"[" * 50000, "cache/backfill_state.json": b" " * (2 * 1024 * 1024)})
        previous = previous_copy.load(self.folder(files))
        self.assertEqual((previous.budget, previous.backfill_state), (None, None))

    def test_symlinked_rtms_cache_fails_cleanly(self):
        folder = self.folder(self.base())
        outside = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, outside)
        os.symlink(outside, os.path.join(folder, "cache", "rtms"))
        with self.assertRaises(ValidationFailed):
            previous_copy.load(folder)


if __name__ == "__main__":
    unittest.main()
