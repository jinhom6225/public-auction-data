import datetime
import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

import netguard  # noqa: F401  (blocks the network)
from fakes import item_record

from collector import write_output
from collector.budget import Budget
from collector.errors import ValidationFailed
from collector.region_index import RegionIndex
from collector.validate_output import tree_problems

INDEX = RegionIndex.load()
GENERATED = "2026-11-01T20:10:00Z"
NOW = datetime.datetime(2026, 11, 1, 20, 10, tzinfo=datetime.timezone.utc)


def records():
    return [item_record("B-2"), item_record("A-1"), item_record("C-3", sgg="26110", pnu=None, umd=None)]


def render(recs=None):
    return write_output.render(recs if recs is not None else records(), GENERATED, INDEX,
                               seen={}, budget=Budget.load(None, "2026-11-02"))


class RenderTest(unittest.TestCase):
    def test_same_input_same_bytes(self):
        self.assertEqual(render(), render())
        self.assertEqual(render(), render(list(reversed(records()))))

    def test_index_lists_every_province_with_matching_sha(self):
        files = render()
        index = json.loads(files["v1/index.json"])
        self.assertEqual(index["schemaVersion"], 1)
        self.assertEqual(index["generatedAt"], GENERATED)
        self.assertEqual(len(index["regions"]), 16)
        for entry in index["regions"]:
            self.assertEqual(entry["file"], f"items/{entry['sido']}.json")
            data = files["v1/" + entry["file"]]
            self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest())
        seoul = next(e for e in index["regions"] if e["sido"] == "11")
        self.assertEqual((seoul["name"], seoul["count"]), ("서울특별시", 2))

    def test_items_sorted_by_key_and_compact(self):
        files = render()
        seoul = json.loads(files["v1/items/11.json"])
        self.assertEqual([i["key"] for i in seoul["items"]], ["A-1", "B-2"])
        self.assertEqual(seoul["sido"], "11")
        self.assertTrue(files["v1/items/11.json"].endswith(b"\n"))
        self.assertNotIn(b": ", files["v1/items/11.json"])

    def test_cache_files(self):
        files = render()
        self.assertEqual(json.loads(files["cache/budget.json"]), {"date": "2026-11-02", "used": {}})
        self.assertEqual(json.loads(files["cache/seen.json"]), {"schemaVersion": 1, "items": {}})

    def test_output_passes_tree_validation(self):
        self.assertEqual(tree_problems(render(), NOW, INDEX), [])

    def test_file_over_the_cap_fails(self):
        with mock.patch.object(write_output, "MAX_FILE_BYTES", 500):
            with self.assertRaises(ValidationFailed) as ctx:
                render()
        self.assertIn("items/11.json", str(ctx.exception))

    def test_exactly_at_the_cap_passes(self):
        size = len(render()["v1/items/11.json"])
        with mock.patch.object(write_output, "MAX_FILE_BYTES", size):
            render()

    def test_record_outside_known_provinces_fails(self):
        with self.assertRaises(ValidationFailed):
            render([item_record("X", sgg="99110")])


class CommitTest(unittest.TestCase):
    def test_replaces_owned_folders_and_keeps_others(self):
        with tempfile.TemporaryDirectory() as out:
            os.makedirs(os.path.join(out, "v1", "items"))
            with open(os.path.join(out, "v1", "items", "stale.json"), "w") as handle:
                handle.write("old")
            with open(os.path.join(out, "README.md"), "w") as handle:
                handle.write("keep")
            write_output.commit(render(), out)
            self.assertFalse(os.path.exists(os.path.join(out, "v1", "items", "stale.json")))
            self.assertTrue(os.path.exists(os.path.join(out, "v1", "items", "11.json")))
            self.assertTrue(os.path.exists(os.path.join(out, "README.md")))
            self.assertEqual(sorted(os.listdir(out)), ["README.md", "cache", "v1"])

    def test_refuses_unsafe_paths(self):
        with tempfile.TemporaryDirectory() as out:
            for bad in ("../x.json", "v1/../../x.json", "/abs.json", "other/x.json"):
                with self.assertRaises(ValueError):
                    write_output.commit({bad: b"{}"}, out)
            self.assertEqual(os.listdir(out), [])


if __name__ == "__main__":
    unittest.main()
