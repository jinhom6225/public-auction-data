import contextlib
import datetime
import hashlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import netguard  # noqa: F401  (blocks the network)
from fakes import item_record

from collector import validate_output, write_output
from collector.budget import Budget
from collector.region_index import RegionIndex

INDEX = RegionIndex.load()
NOW = datetime.datetime(2026, 11, 1, 20, 10, tzinfo=datetime.timezone.utc)


def files():
    return write_output.render([item_record("A-1"), item_record("B-2")], "2026-11-01T20:10:00Z", INDEX,
                               seen={}, budget=Budget.load(None, "2026-11-02"))


def with_items(base, sido, items):
    """Replace one province file's items and fix up its index entry."""
    out = dict(base)
    doc = json.loads(out[f"v1/items/{sido}.json"])
    data = write_output.dumps({**doc, "items": items})
    out[f"v1/items/{sido}.json"] = data
    index = json.loads(out["v1/index.json"])
    for entry in index["regions"]:
        if entry["sido"] == sido:
            entry["sha256"] = hashlib.sha256(data).hexdigest()
            entry["count"] = len(items)
    out["v1/index.json"] = write_output.dumps(index)
    return out


class TreeProblemsTest(unittest.TestCase):
    def problems(self, tree):
        return validate_output.tree_problems(tree, NOW, INDEX)

    def test_clean_tree(self):
        self.assertEqual(self.problems(files()), [])

    def test_sha_mismatch(self):
        tree = files()
        tree["v1/items/11.json"] = tree["v1/items/11.json"].replace(b"A-1", b"A-9")
        self.assertTrue(any("sha256" in p for p in self.problems(tree)))

    def test_missing_and_extra_files(self):
        tree = files()
        del tree["v1/items/26.json"]
        tree["v1/items/99.json"] = b"{}"
        problems = self.problems(tree)
        self.assertTrue(any("missing v1/items/26.json" in p for p in problems))
        self.assertTrue(any("unexpected file v1/items/99.json" in p for p in problems))

    def test_unsorted_and_duplicate_keys(self):
        unsorted = with_items(files(), "11", [item_record("B-2"), item_record("A-1")])
        self.assertTrue(any("sorted" in p for p in self.problems(unsorted)))
        dup = with_items(files(), "11", [item_record("A-1"), item_record("A-1")])
        self.assertTrue(any("sorted" in p for p in self.problems(dup)))

    def test_invalid_record(self):
        tree = with_items(files(), "11", [item_record("A-1", failCount=-1)])
        self.assertTrue(any("invalid failCount" in p for p in self.problems(tree)))

    def test_count_and_schema(self):
        tree = files()
        index = json.loads(tree["v1/index.json"])
        index["regions"][0]["count"] = 99
        index["schemaVersion"] = 2
        tree["v1/index.json"] = write_output.dumps(index)
        problems = self.problems(tree)
        self.assertTrue(any("schemaVersion" in p for p in problems))
        self.assertTrue(any("count" in p for p in problems))

    def test_bad_region_file_entry(self):
        tree = files()
        index = json.loads(tree["v1/index.json"])
        index["regions"][0]["file"] = "../items/11.json"
        tree["v1/index.json"] = write_output.dumps(index)
        self.assertTrue(any("regions[].file" in p for p in self.problems(tree)))

    def test_size_caps(self):
        with mock.patch.object(validate_output, "MAX_FILE_BYTES", 100):
            self.assertTrue(any("5MB" in p or "cap" in p for p in self.problems(files())))
        with mock.patch.object(validate_output, "MAX_INDEX_BYTES", 100):
            self.assertTrue(any("index" in p for p in self.problems(files())))

    def test_not_json(self):
        tree = files()
        tree["v1/index.json"] = b"nope"
        self.assertTrue(self.problems(tree))


class CliTest(unittest.TestCase):
    def test_exit_codes(self):
        with tempfile.TemporaryDirectory() as out, contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            write_output.commit(files(), out)
            self.assertEqual(validate_output.main(["validate_output", out], now=NOW), 0)
            with open(os.path.join(out, "v1", "items", "11.json"), "ab") as handle:
                handle.write(b" ")
            self.assertEqual(validate_output.main(["validate_output", out], now=NOW), 2)
            self.assertEqual(validate_output.main(["validate_output"]), 64)


if __name__ == "__main__":
    unittest.main()
