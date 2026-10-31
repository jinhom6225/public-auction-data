import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import netguard  # noqa: F401  (blocks the network)
from fakes import FAKE_KEY, RecordingTransport, is_rtms, ok, page_body, page_no_of, raw_row, rtms_body, rtms_item
from fakes import with_rtms

from collector import collect, rtms_cache, rtms_client, write_output
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


def paged(rows, page_size=1000, fail_page=None, rtms=None):
    def handler(url, i):
        page = page_no_of(url)
        if page == fail_page:
            return Response(status=500, body=b"")
        chunk = rows[(page - 1) * page_size: page * page_size]
        return ok(page_body(chunk, len(rows), page, page_size))
    return RecordingTransport(with_rtms(handler, rtms))


def same_building_deals(url, i):
    """Three deals in the building of raw_row() (청운동 12-3) every month."""
    deals = [rtms_item(sggCd="11110", umdCd="10100", umdNm="청운동", bonbun="0012", bubun="0003",
                       jibun="12-3", excluUseAr="84.9", dealAmount=f"{90000 + 1000 * k:,}", dealDay=str(k + 1))
             for k in range(3)]
    return ok(rtms_body(deals))


def snapshot(folder):
    found = {}
    for root, _, names in os.walk(folder):
        for name in names:
            path = os.path.join(root, name)
            with open(path, "rb") as handle:
                found[os.path.relpath(path, folder)] = handle.read()
    return found


RTMS_DIR = os.path.join("cache", "rtms")


def pinned(snap):
    """Golden view of a run: every file but cache/rtms/, plus the rtms manifest."""
    view = {path: data for path, data in snap.items() if not path.startswith(RTMS_DIR + os.sep)}
    rtms = {path.replace(os.sep, "/"): data for path, data in snap.items() if path.startswith(RTMS_DIR + os.sep)}
    return view, rtms_cache.manifest(rtms)


def golden(*parts):
    snap = snapshot(netguard.fixture_path(*parts))
    manifest = json.loads(snap.pop("rtms_manifest.json"))
    return snap, manifest


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
        self.assertEqual(budget, {"date": "2026-11-02", "used": {"apt": 12, "onbid": 1}})   # 1 pair x 12 months
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
        self.assertEqual(pinned(snapshot(self.out)), golden("golden"))

    def test_boundary_fixture_two_days_match_the_golden_copies(self):
        """M6: hand-made edge cases; the app's items_sample/boundary_day2 copies day 2."""
        base = netguard.fixture_path("boundary")
        code, _, err = self.run_collect(None, "--first-run", "--offline-fixtures", os.path.join(base, "day1"),
                                        env={}, now="2026-10-31T20:10:00Z")
        self.assertEqual(code, 0, err)
        self.assertEqual(pinned(snapshot(self.out)), golden("golden_boundary", "day1"))
        os.rename(self.out, self.prev)
        code, _, err = self.run_collect(None, "--prev", self.prev, "--offline-fixtures",
                                        os.path.join(base, "day2"), env={}, now="2026-11-01T20:10:00Z")
        self.assertEqual(code, 0, err)
        day2 = snapshot(self.out)
        self.assertEqual(pinned(day2), golden("golden_boundary", "day2"))
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


class FixtureTransportTest(unittest.TestCase):
    def test_molit_urls_map_to_fixture_files_and_missing_months_are_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "rtms_rh_11500_202609.xml"), "wb") as handle:
                handle.write(rtms_body([rtms_item()]))
            transport = collect.fixture_transport(folder)
            base = "https://apis.data.go.kr/1613000/RTMSDataSvcRHTrade2/getRTMSDataSvcRHTrade2?serviceKey=x&"
            found = transport(base + "LAWD_CD=11500&DEAL_YMD=202609&pageNo=1", 1, 10**6)
            self.assertIn(b"<totalCount>1<", found.body)
            missing = transport(base + "LAWD_CD=11500&DEAL_YMD=202608&pageNo=1", 1, 10**6)
            self.assertEqual(missing.body, rtms_client.empty_body())
            odd = transport(base + "LAWD_CD=../../x&DEAL_YMD=202608&pageNo=1", 1, 10**6)
            self.assertEqual(odd.body, rtms_client.empty_body())
        self.assertEqual(collect._rtms_fixture_name(base + "LAWD_CD=11500&DEAL_YMD=202609&pageNo=2"),
                         "rtms_rh_11500_202609_p2.xml")
        self.assertIsNone(collect._rtms_fixture_name("https://apis.data.go.kr/B010003/x?pageNo=1"))


class RealTransactionStageTest(CollectCase):
    """Phase 3 wiring: cache on the data branch, refresh vs backfill, failure rule."""

    def items(self):
        return {i["key"]: i for i in json.loads(snapshot(self.out)[os.path.join("v1", "items", "11.json")])["items"]}

    def rtms_files(self, folder):
        return {path: data for path, data in snapshot(folder).items() if path.startswith(os.path.join("cache", "rtms"))}

    def test_first_run_fills_compare_and_the_cache(self):
        code, out, err = self.run_collect(paged(rows_for(1), rtms=same_building_deals), "--first-run")
        self.assertEqual(code, 0, err)
        compare = self.items()["2025-00000-000"]["compare"]
        self.assertEqual((compare["kind"], compare["n"], len(compare["deals"])), ("sameBuilding", 36, 10))
        self.assertEqual((compare["estimate"], compare["ratio"]), (910000000, 73))   # 665,000,000 / 910,000,000
        files = self.rtms_files(self.out)
        self.assertEqual(len(files), 12)
        self.assertIn(os.path.join("cache", "rtms", "apt", "11110", "202611.json"), files)
        state = json.loads(snapshot(self.out)[os.path.join("cache", "backfill_state.json")])
        self.assertEqual(state["apis"]["apt"]["cached"], 12)
        self.assertIn("rtms apt: pairs=1 cached=12/12 fetched=12", out)

    def test_v1_carries_no_raw_molit_fields(self):
        self.run_collect(paged(rows_for(2), rtms=same_building_deals), "--first-run")
        v1 = b"".join(data for path, data in snapshot(self.out).items() if path.startswith("v1"))
        for field in (b"dealAmount", b"umdNm", b"jibun", b"excluUseAr", b"cdealType", b"aptNm", b'"k":'):
            self.assertNotIn(field, v1)

    def test_next_day_refreshes_two_months_and_carries_the_rest(self):
        self.first_run_into_prev_with(same_building_deals)
        transport = paged(rows_for(1), rtms=same_building_deals)
        code, _, err = self.run_collect(transport, "--prev", self.prev, now=NEXT_DAY)
        self.assertEqual(code, 0, err)
        months = sorted(url.split("DEAL_YMD=")[1][:6] for url in transport.urls if is_rtms(url))
        self.assertEqual(months, ["202610", "202611"])
        before, after = self.rtms_files(self.prev), self.rtms_files(self.out)
        changed = sorted(path for path in after if after[path] != before.get(path))
        self.assertEqual([os.path.basename(path) for path in changed], ["202610.json", "202611.json"])
        budget = json.loads(snapshot(self.out)[os.path.join("cache", "budget.json")])
        self.assertEqual(budget["used"]["apt"], 2)

    def test_month_rollover_drops_the_thirteenth_month(self):
        self.first_run_into_prev_with(same_building_deals)          # window 202512 ... 202611
        december = [{**row, "cltrBidBgngDt": "202612091000", "cltrBidEndDt": "202612111700"} for row in rows_for(1)]
        code, _, err = self.run_collect(paged(december, rtms=same_building_deals), "--prev", self.prev,
                                        now="2026-11-30T16:00:00Z")   # 2026-12-01 KST
        self.assertEqual(code, 0, err)
        names = sorted(os.path.basename(path) for path in self.rtms_files(self.out))
        self.assertEqual((names[0], names[-1], len(names)), ("202601.json", "202612.json", 12))

    def test_failed_month_keeps_yesterday_and_small_failure_rate_passes(self):
        self.first_run_into_prev_with(same_building_deals)
        rows = rows_for(1) + [raw_row(key=f"2025-11140-{i:03d}", lctnSggnm="중구", lctnEmdNm="무교동",
                                      ltnoPnu=None, onbidCltrNm="서울특별시 중구 무교동 1",
                                      cltrBidEndDt="202611111700") for i in range(1)]

        def flaky(url, i):
            if "LAWD_CD=11110" in url and "DEAL_YMD=202611" in url:
                return Response(status=500, body=b"")
            return same_building_deals(url, i) if "LAWD_CD=11110" in url else ok(rtms_body([]))
        code, out, err = self.run_collect(paged(rows, rtms=flaky), "--prev", self.prev, now=NEXT_DAY)
        self.assertEqual(code, 0, err)                                  # 1 of 14 months = 7%
        key = os.path.join("cache", "rtms", "apt", "11110", "202611.json")
        self.assertEqual(self.rtms_files(self.out)[key], self.rtms_files(self.prev)[key])
        self.assertIn("failed=1", out)

    def test_failed_months_exit_1_write_nothing_and_stop_calling_early(self):
        before = self.seed_out()
        transport = paged(rows_for(1), rtms=lambda url, i: Response(status=500, body=b""))
        code, _, err = self.run_collect(transport, "--first-run")
        self.assertEqual(code, 1)
        self.assertEqual(snapshot(self.out), before)
        self.assertIn("real-transaction months failed — apt: 2 of 2", err)
        # HIGH-1: two failed months pass the failure budget (10% of 12): no further calls (2 months x 2 tries)
        self.assertEqual(sum(1 for url in transport.urls if is_rtms(url)), 4)

    def run_with_clock(self, later, *args):
        readings = iter([0.0])                     # collector start; every later reading = [later]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = collect.main(["collect", "--out", self.out, "--now", NOW, *args], env=ENV,
                                transport=paged(rows_for(1), rtms=same_building_deals),
                                sleep=lambda seconds: None, clock=lambda: next(readings, later))
        return code, stdout.getvalue()

    def test_backfill_stops_at_40_minutes_but_refresh_runs(self):
        code, _ = self.run_with_clock(45 * 60.0, "--first-run")
        self.assertEqual(code, 0)
        names = sorted(os.path.basename(path) for path in self.rtms_files(self.out))
        self.assertEqual(names, ["202610.json", "202611.json"])
        state = json.loads(snapshot(self.out)[os.path.join("cache", "backfill_state.json")])
        self.assertEqual(state["apis"]["apt"]["deferred"], 10)

    def test_nothing_is_fetched_after_50_minutes_and_yesterday_stays(self):
        self.first_run_into_prev_with(same_building_deals)
        code, out = self.run_with_clock(55 * 60.0, "--prev", self.prev)
        self.assertEqual(code, 0)
        self.assertEqual(self.rtms_files(self.out), self.rtms_files(self.prev))
        self.assertIn("fetched=0 failed=0 deferred=2", out)
        self.assertIn("rtms manifest: files=12", out)

    def test_gone_pair_keeps_its_months_for_14_days(self):
        def deals(url, i):
            return same_building_deals(url, i) if "LAWD_CD=11110" in url else ok(rtms_body([]))
        december = {"cltrBidBgngDt": "202612091000", "cltrBidEndDt": "202612111700"}
        busan = [{**row, **december} for row in busan_rows(1)]
        seoul = [{**row, **december} for row in rows_for(9)]
        code, _, err = self.run_collect(paged(seoul + busan, rtms=deals), "--first-run")   # 2026-11-02 KST
        self.assertEqual(code, 0, err)
        busan_dir = os.path.join("cache", "rtms", "apt", "26110") + os.sep
        for day, kept in (("2026-11-15T20:10:00Z", 12), ("2026-11-16T20:10:00Z", 0)):   # KST +14, +15 days
            os.rename(self.out, self.prev)
            code, _, err = self.run_collect(paged(seoul, rtms=deals), "--prev", self.prev, now=day)
            self.assertEqual(code, 0, err)
            self.assertEqual(sum(1 for p in self.rtms_files(self.out) if p.startswith(busan_dir)), kept, day)
            shutil.rmtree(self.prev)

    def test_missing_rtms_cache_in_prev_is_a_full_backfill(self):
        self.first_run_into_prev(rows_for(1))
        import shutil
        shutil.rmtree(os.path.join(self.prev, "cache", "rtms"))
        transport = paged(rows_for(1))
        code, _, err = self.run_collect(transport, "--prev", self.prev, now=NEXT_DAY)
        self.assertEqual(code, 0, err)
        self.assertEqual(sum(1 for url in transport.urls if is_rtms(url)), 12)

    def first_run_into_prev_with(self, rtms):
        code, _, err = self.run_collect(paged(rows_for(1), rtms=rtms), "--first-run")
        self.assertEqual(code, 0, err)
        os.rename(self.out, self.prev)


if __name__ == "__main__":
    unittest.main()
