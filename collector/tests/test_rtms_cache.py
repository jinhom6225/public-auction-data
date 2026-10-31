import datetime
import json
import os
import tempfile
import unittest

import netguard  # noqa: F401  (blocks the network)

from collector import rtms_cache
from collector.rtms_cache import Month

ROW = {"d": 3, "p": 306500000, "a": 84.7555, "f": 17, "u": "1224011800", "k": "122401180013370002"}
LAND_ROW = {"d": 1, "p": 97500000, "a": 496.0, "u": "4146111000", "j": "임야"}


def month(api="apt", lawd="12240", ym="202609", fetched="2026-10-31", rows=(ROW,)):
    return Month(api, lawd, ym, fetched, tuple(rows))


class WindowTest(unittest.TestCase):
    def test_twelve_months_newest_first(self):
        window = rtms_cache.window(datetime.date(2026, 10, 31), 12)
        self.assertEqual(window[0], "202610")
        self.assertEqual(window[-1], "202511")
        self.assertEqual(len(window), 12)

    def test_window_crosses_the_year(self):
        self.assertEqual(rtms_cache.window(datetime.date(2026, 1, 1), 3), ("202601", "202512", "202511"))

    def test_window_moves_and_drops_the_thirteenth_month(self):
        cache = {m.slot: m for m in (month(ym="202511"), month(ym="202512"), month(ym="202611"))}
        kept = rtms_cache.in_window(cache, rtms_cache.window(datetime.date(2026, 11, 2), 12))
        self.assertEqual(sorted(slot[2] for slot in kept), ["202512", "202611"])
        files = rtms_cache.render(kept)
        self.assertNotIn("cache/rtms/apt/12240/202511.json", files)


class RenderLoadTest(unittest.TestCase):
    def test_path_and_document(self):
        files = rtms_cache.render({month().slot: month()})
        self.assertEqual(list(files), ["cache/rtms/apt/12240/202609.json"])
        doc = json.loads(files["cache/rtms/apt/12240/202609.json"])
        self.assertEqual(doc, {"schemaVersion": 1, "api": "apt", "lawd": "12240", "ym": "202609",
                               "fetched": "2026-10-31", "rows": [ROW]})

    def test_rows_are_sorted_for_identical_bytes(self):
        other = {**ROW, "d": 1}
        one = rtms_cache.render({month().slot: month(rows=(ROW, other))})
        two = rtms_cache.render({month().slot: month(rows=(other, ROW))})
        self.assertEqual(one, two)

    def test_round_trip_through_a_folder(self):
        cache = {m.slot: m for m in (month(), month(api="land", lawd="41461", rows=(LAND_ROW,)),
                                     month(ym="202608", rows=()))}
        with tempfile.TemporaryDirectory() as folder:
            self.write(folder, rtms_cache.render(cache))
            loaded, bad = rtms_cache.load_folder(folder)
        self.assertEqual(bad, 0)
        self.assertEqual(rtms_cache.render(loaded), rtms_cache.render(cache))

    def test_missing_cache_folder_is_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(rtms_cache.load_folder(folder), ({}, 0))

    def test_broken_or_odd_files_are_skipped_and_counted(self):
        good = rtms_cache.render({month().slot: month()})
        bad_rows = json.loads(good["cache/rtms/apt/12240/202609.json"])
        bad_rows["rows"][0]["p"] = -1
        files = {
            **good,
            "cache/rtms/apt/12240/202608.json": b"{not json",
            "cache/rtms/apt/12240/202607.json": json.dumps(bad_rows).encode(),
            "cache/rtms/apt/12240/202606.json": json.dumps({**bad_rows, "rows": [], "ym": "202605"}).encode(),
            "cache/rtms/apt/12240/202605.json": json.dumps({**bad_rows, "rows": [{**ROW, "j": "x"}]}).encode(),
            "cache/rtms/car/12240/202609.json": good["cache/rtms/apt/12240/202609.json"],
            "cache/rtms/apt/12240/notes.txt": b"x",
        }
        with tempfile.TemporaryDirectory() as folder:
            self.write(folder, files)
            loaded, bad = rtms_cache.load_folder(folder)
        self.assertEqual(list(loaded), [("apt", "12240", "202609")])
        self.assertEqual(bad, 6)

    def test_symlinked_file_is_not_read(self):
        with tempfile.TemporaryDirectory() as folder:
            self.write(folder, rtms_cache.render({month().slot: month()}))
            outside = os.path.join(folder, "outside.json")
            with open(outside, "w") as handle:
                handle.write("{}")
            os.symlink(outside, os.path.join(folder, "cache", "rtms", "apt", "12240", "202608.json"))
            loaded, bad = rtms_cache.load_folder(folder)
        self.assertEqual((list(loaded), bad), ([("apt", "12240", "202609")], 1))

    @staticmethod
    def write(folder, files):
        for path, data in files.items():
            target = os.path.join(folder, *path.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(data)


if __name__ == "__main__":
    unittest.main()
