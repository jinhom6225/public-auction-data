import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import netguard  # noqa: F401  (blocks the network)
from fakes import FAKE_KEY, RecordingTransport, ok, page_body, page_no_of, raw_row

from collector import collect, write_output
from collector.http_client import Response

NOW = "2026-11-01T20:10:00Z"          # 2026-11-02 05:10 KST
NEXT_DAY = "2026-11-02T20:10:00Z"
ENV = {"DATA_GO_KR_SERVICE_KEY": FAKE_KEY}


def rows_for(count, price=665000000):
    return [raw_row(key=f"2025-00000-{i:03d}", lowstBidPrcIndctCont=str(price),
                    cltrBidBgngDt="202611091000", cltrBidEndDt="202611111700") for i in range(count)]


def busan_rows(count, start=0):
    return [raw_row(key=f"2025-26000-{i:03d}", lctnSdnm="부산광역시", lctnSggnm="중구", lctnEmdNm="영주동",
                    ltnoPnu=None, onbidCltrNm="부산광역시 중구 영주동 1", cltrBidBgngDt="202611091000",
                    cltrBidEndDt="202611111700") for i in range(start, start + count)]


def paged(rows, page_size=1000, fail_page=None):
    def handler(url, i):
        page = page_no_of(url)
        if page == fail_page:
            return Response(status=500, body=b"")
        chunk = rows[(page - 1) * page_size: page * page_size]
        return ok(page_body(chunk, len(rows), page, page_size))
    return RecordingTransport(handler)


def snapshot(folder):
    found = {}
    for root, _, names in os.walk(folder):
        for name in names:
            path = os.path.join(root, name)
            with open(path, "rb") as handle:
                found[os.path.relpath(path, folder)] = handle.read()
    return found


class CollectCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.out = os.path.join(self.root, "out")
        self.prev = os.path.join(self.root, "prev")

    def tearDown(self):
        self._tmp.cleanup()

    def run_collect(self, transport, *args, env=ENV, now=NOW):
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["collect", "--out", self.out, "--now", now, *args]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = collect.main(argv, env=env, transport=transport, sleep=lambda seconds: None)
        return code, stdout.getvalue(), stderr.getvalue()

    def first_run_into_prev(self, rows):
        code, _, _ = self.run_collect(paged(rows), "--first-run")
        self.assertEqual(code, 0)
        os.rename(self.out, self.prev)

    def seed_out(self):
        os.makedirs(os.path.join(self.out, "v1"))
        with open(os.path.join(self.out, "v1", "index.json"), "w") as handle:
            handle.write("yesterday")
        return snapshot(self.out)


class SuccessTest(CollectCase):
    def test_first_run_writes_valid_output(self):
        code, out, _ = self.run_collect(paged(rows_for(3)), "--first-run")
        self.assertEqual(code, 0)
        files = snapshot(self.out)
        self.assertIn(os.path.join("v1", "index.json"), files)
        seoul = json.loads(files[os.path.join("v1", "items", "11.json")])
        self.assertEqual(len(seoul["items"]), 3)
        for item in seoul["items"]:
            self.assertEqual((item["firstSeen"], item["priceDropAt"]), ("2026-11-02", None))
            self.assertEqual(item["compare"], {"kind": "none"})
        budget = json.loads(files[os.path.join("cache", "budget.json")])
        self.assertEqual(budget, {"date": "2026-11-02", "used": {"onbid": 1}})
        self.assertIn("items=3", out)

    def test_second_day_marks_new_items_and_price_drops(self):
        self.first_run_into_prev(rows_for(3))
        today = rows_for(3)
        today[0] = {**today[0], "lowstBidPrcIndctCont": "600000000"}
        today.append(raw_row(key="2025-00000-999", cltrBidBgngDt="202611091000", cltrBidEndDt="202611111700"))
        code, _, _ = self.run_collect(paged(today), "--prev", self.prev, now=NEXT_DAY)
        self.assertEqual(code, 0)
        items = {i["key"]: i for i in json.loads(snapshot(self.out)[os.path.join("v1", "items", "11.json")])["items"]}
        self.assertEqual(items["2025-00000-000"]["priceDropAt"], "2026-11-03")
        self.assertEqual(items["2025-00000-000"]["prevLowestBid"], 665000000)
        self.assertEqual(items["2025-00000-000"]["firstSeen"], "2026-11-02")
        self.assertEqual(items["2025-00000-999"]["firstSeen"], "2026-11-03")
        self.assertIsNone(items["2025-00000-001"]["priceDropAt"])

    def test_same_input_twice_gives_identical_bytes(self):
        self.run_collect(paged(rows_for(5)), "--first-run")
        first = snapshot(self.out)
        self.run_collect(paged(list(reversed(rows_for(5)))), "--first-run")
        self.assertEqual(snapshot(self.out), first)

    def test_fixture_run_matches_the_golden_copy(self):
        """Regression pin; the app's items_sample is a copy of this golden."""
        fixtures = netguard.fixture_path("onbid_pages")
        code, _, err = self.run_collect(None, "--first-run", "--offline-fixtures", fixtures, env={},
                                        now="2026-10-31T20:10:00Z")
        self.assertEqual(code, 0, err)
        self.assertEqual(snapshot(self.out), snapshot(netguard.fixture_path("golden")))

    def test_boundary_fixture_two_days_match_the_golden_copies(self):
        """M6: hand-made edge cases; the app's items_sample/boundary_day2 copies day 2."""
        base = netguard.fixture_path("boundary")
        code, _, err = self.run_collect(None, "--first-run", "--offline-fixtures", os.path.join(base, "day1"),
                                        env={}, now="2026-10-31T20:10:00Z")
        self.assertEqual(code, 0, err)
        self.assertEqual(snapshot(self.out), snapshot(netguard.fixture_path("golden_boundary", "day1")))
        os.rename(self.out, self.prev)
        code, _, err = self.run_collect(None, "--prev", self.prev, "--offline-fixtures",
                                        os.path.join(base, "day2"), env={}, now="2026-11-01T20:10:00Z")
        self.assertEqual(code, 0, err)
        day2 = snapshot(self.out)
        self.assertEqual(day2, snapshot(netguard.fixture_path("golden_boundary", "day2")))
        items = {i["key"]: i for i in json.loads(day2[os.path.join("v1", "items", "11.json")])["items"]}
        self.assertEqual(len(items["B-NAME-602"]["name"]), 602)
        self.assertNotIn("B-NAME-603", items)
        self.assertEqual(items["B-FAIL-108"]["failCount"], 108)
        self.assertEqual((items["B-ONESIDE"]["bidStart"], items["B-ONESIDE"]["bidEnd"]), (None, None))
        self.assertEqual((items["B-DROP"]["priceDropAt"], items["B-DROP"]["prevLowestBid"]), ("2026-11-02", 1000000))
        self.assertEqual(items["B-NEW"]["firstSeen"], "2026-11-02")
        self.assertEqual(items["B-DROP"]["firstSeen"], "2026-11-01")

    def test_offline_fixtures_need_no_key(self):
        fixtures = netguard.fixture_path("onbid_pages")
        code, _, err = self.run_collect(None, "--first-run", "--offline-fixtures", fixtures, env={})
        self.assertEqual(code, 0, err)
        self.assertEqual(collect.main(["validate"], env={}), 64)


class FailureRulesTest(CollectCase):
    def assert_untouched(self, before, code, expected):
        self.assertEqual(code, expected)
        self.assertEqual(snapshot(self.out), before)

    def test_page_failing_twice_exits_1_and_writes_nothing(self):
        before = self.seed_out()
        code, _, err = self.run_collect(paged(rows_for(1500), fail_page=2), "--first-run")
        self.assert_untouched(before, code, 1)
        self.assertIn("getRlstCltrList2 failed: HTTP 500", err)

    def test_drop_of_more_than_40_percent_exits_2(self):
        self.first_run_into_prev(rows_for(10))
        before = self.seed_out()
        code, _, err = self.run_collect(paged(rows_for(5)), "--prev", self.prev, now=NEXT_DAY)
        self.assert_untouched(before, code, 2)
        self.assertIn("40%", err)

    def test_drop_of_exactly_40_percent_passes(self):
        self.first_run_into_prev(rows_for(10))
        code, _, _ = self.run_collect(paged(rows_for(6)), "--prev", self.prev, now=NEXT_DAY)
        self.assertEqual(code, 0)

    def test_file_over_5mb_exits_2(self):
        before = self.seed_out()
        with mock.patch.object(write_output, "MAX_FILE_BYTES", 1000):
            code, _, err = self.run_collect(paged(rows_for(3)), "--first-run")
        self.assert_untouched(before, code, 2)
        self.assertIn("cap", err)

    def test_daily_cap_stops_before_calling(self):
        self.first_run_into_prev(rows_for(2))
        with open(os.path.join(self.prev, "cache", "budget.json"), "w") as handle:
            json.dump({"date": "2026-11-03", "used": {"onbid": 899}}, handle)
        before = self.seed_out()
        transport = paged(rows_for(1500))
        code, _, err = self.run_collect(transport, "--prev", self.prev, now=NEXT_DAY)
        self.assert_untouched(before, code, 1)
        self.assertEqual(len(transport.urls), 1)
        self.assertIn("daily call cap", err)

    def test_too_many_dropped_records_exits_2(self):
        bad = [raw_row(key=f"K{i}", lctnSdnm=None, lctnSggnm=None, lctnEmdNm=None, ltnoPnu=None,
                       onbidCltrNm="x", cltrBidEndDt="202611111700") for i in range(3)]
        before = self.seed_out()
        code, _, err = self.run_collect(paged(rows_for(7) + bad), "--first-run")
        self.assert_untouched(before, code, 2)
        self.assertIn("no location", err)

    def test_missing_key_exits_1(self):
        before = self.seed_out()
        code, _, err = self.run_collect(paged(rows_for(1)), "--first-run", env={})
        self.assert_untouched(before, code, 1)
        self.assertIn("DATA_GO_KR_SERVICE_KEY is not set", err)

    def test_previous_copy_without_history_exits_2(self):
        os.makedirs(os.path.join(self.prev, "v1"))
        with open(os.path.join(self.prev, "v1", "index.json"), "w") as handle:
            handle.write("{}")
        before = self.seed_out()
        code, _, _ = self.run_collect(paged(rows_for(1)), "--prev", self.prev)
        self.assert_untouched(before, code, 2)

    def test_prev_or_first_run_is_required(self):
        code, _, _ = self.run_collect(paged(rows_for(1)))
        self.assertEqual(code, 64)
        code, _, _ = self.run_collect(paged(rows_for(1)), "--first-run", "--prev", self.prev)
        self.assertEqual(code, 64)

    def test_unexpected_crash_logs_a_redacted_traceback_and_exits_3(self):
        # M2: collector bugs get their own exit code and a traceback without the key.
        before = self.seed_out()
        url = "https://apis.data.go.kr/x?serviceKey=" + FAKE_KEY + "&pageNo=1"
        with mock.patch.object(collect, "_collect", side_effect=KeyError("boom " + FAKE_KEY + " " + url)):
            code, out, err = self.run_collect(paged(rows_for(1)), "--first-run")
        self.assert_untouched(before, code, 3)
        self.assertIn("Traceback", err)
        self.assertIn("KeyError", err)
        self.assertNotIn(FAKE_KEY, out + err)
        self.assertNotIn("serviceKey=", out + err)

    def test_log_redacts_the_stripped_key(self):
        # S-L6: the environment value may carry spaces; the client uses the stripped key.
        with mock.patch.object(collect, "_collect", side_effect=KeyError(FAKE_KEY)):
            code, out, err = self.run_collect(paged(rows_for(1)), "--first-run",
                                              env={"DATA_GO_KR_SERVICE_KEY": "  " + FAKE_KEY + "\n"})
        self.assertEqual(code, 3)
        self.assertNotIn(FAKE_KEY, out + err)

    def test_province_falling_more_than_60_percent_exits_2(self):
        # M3: 부산 stays, 서울 (50 yesterday) falls to 19 -> national -21%, 서울 -62%.
        self.first_run_into_prev(rows_for(50) + busan_rows(100))
        before = self.seed_out()
        code, _, err = self.run_collect(paged(rows_for(19) + busan_rows(100)), "--prev", self.prev, now=NEXT_DAY)
        self.assert_untouched(before, code, 2)
        self.assertIn("11", err)

    def test_province_guard_boundaries(self):
        self.first_run_into_prev(rows_for(50) + busan_rows(100))
        code, _, _ = self.run_collect(paged(rows_for(20) + busan_rows(100)), "--prev", self.prev, now=NEXT_DAY)
        self.assertEqual(code, 0)    # exactly -60% passes

    def test_small_province_is_not_guarded(self):
        self.first_run_into_prev(rows_for(49) + busan_rows(100))
        code, _, _ = self.run_collect(paged(rows_for(1) + busan_rows(100)), "--prev", self.prev, now=NEXT_DAY)
        self.assertEqual(code, 0)

    def test_history_file_is_validated_before_writing(self):
        # L1
        before = self.seed_out()
        with mock.patch("collector.seen_history.render", return_value={"schemaVersion": 1, "items": {"A": {}}}):
            code, _, err = self.run_collect(paged(rows_for(2)), "--first-run")
        self.assert_untouched(before, code, 2)
        self.assertIn("seen.json", err)


if __name__ == "__main__":
    unittest.main()
