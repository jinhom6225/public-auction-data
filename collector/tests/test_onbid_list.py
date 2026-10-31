import json
import unittest

import netguard  # noqa: F401  (blocks the network)
from fakes import FakeClock, RecordingTransport, ok, page_body, page_no_of, raw_row, FAKE_KEY

from collector import onbid_list
from collector.budget import Budget
from collector.errors import ApiError, BudgetExceeded
from collector.http_client import ApiClient, Response

TODAY = "2026-11-01"


def make_client(handler):
    clock = FakeClock()
    transport = RecordingTransport(handler)
    return ApiClient(transport, FAKE_KEY, monotonic=clock.monotonic, sleep=clock.sleep), transport


def rows(n, start=0):
    return [raw_row(key=f"2025-00000-{i:03d}") for i in range(start, start + n)]


class ParsePageTest(unittest.TestCase):
    def test_real_fixture_parses(self):
        with open(netguard.fixture_path("raw", "onbid_list_sale_p1_n10.json"), "rb") as handle:
            page = onbid_list.parse_page(handle.read())
        self.assertEqual(len(page.rows), 10)
        self.assertGreater(page.total, 10)

    def test_error_shapes_raise_with_result_code(self):
        for name, code in (("error_onbid_no_prptDivCd.json", "11"), ("error_onbid_pvctTrgtYn_YN.json", "99")):
            with open(netguard.fixture_path("raw", name), "rb") as handle:
                with self.assertRaises(ApiError) as ctx:
                    onbid_list.parse_page(handle.read())
            self.assertEqual(ctx.exception.result_code, code)

    def test_single_item_object_and_empty_items(self):
        single = json.loads(page_body([raw_row()], 1))
        single["body"]["items"]["item"] = raw_row()
        self.assertEqual(len(onbid_list.parse_page(json.dumps(single).encode()).rows), 1)
        empty = json.loads(page_body([], 0))
        empty["body"]["items"] = ""
        self.assertEqual(onbid_list.parse_page(json.dumps(empty).encode()).rows, ())

    def test_not_json_is_an_error(self):
        with self.assertRaises(ApiError):
            onbid_list.parse_page(b"<html>")


class FetchAllTest(unittest.TestCase):
    def test_request_parameters(self):
        api, transport = make_client(lambda url, i: ok(page_body(rows(2), 2)))
        onbid_list.fetch_all(api, Budget.load(None, TODAY))
        url = transport.urls[0]
        for part in ("pvctTrgtYn=N", "dspsMthodCd=0001", "resultType=json", "numOfRows=1000",
                     "prptDivCd=0007%2C0010%2C0005%2C0002%2C0003%2C0006%2C0008%2C0011%2C0013"):
            self.assertIn(part, url)
        self.assertNotIn("bidPrd", url)

    def test_walks_every_page(self):
        def handler(url, i):
            page = page_no_of(url)
            return ok(page_body(rows(1000 if page < 3 else 500, (page - 1) * 1000), 2500, page))

        api, transport = make_client(handler)
        result, budget = onbid_list.fetch_all(api, Budget.load(None, TODAY))
        self.assertEqual(len(result), 2500)
        self.assertEqual([page_no_of(u) for u in transport.urls], [1, 2, 3])
        self.assertEqual(budget.used_of("onbid"), 3)

    def test_page_failure_is_retried_once(self):
        def handler(url, i):
            if i == 1:
                return Response(status=500, body=b"")
            page = page_no_of(url)
            return ok(page_body(rows(1000 if page == 1 else 1, (page - 1) * 1000), 1001, page))

        api, transport = make_client(handler)
        result, budget = onbid_list.fetch_all(api, Budget.load(None, TODAY))
        self.assertEqual(len(result), 1001)
        self.assertEqual(budget.used_of("onbid"), 3)

    def test_page_failing_twice_fails_everything(self):
        def handler(url, i):
            if page_no_of(url) == 2:
                return Response(status=500, body=b"")
            return ok(page_body(rows(1000), 1001, 1))

        api, transport = make_client(handler)
        with self.assertRaises(ApiError):
            onbid_list.fetch_all(api, Budget.load(None, TODAY))
        self.assertEqual([page_no_of(u) for u in transport.urls], [1, 2, 2])

    def test_total_count_change_between_pages_fails(self):
        def handler(url, i):
            page = page_no_of(url)
            return ok(page_body(rows(1000), 2000 if page == 1 else 1990, page))

        api, _ = make_client(handler)
        with self.assertRaises(ApiError) as ctx:
            onbid_list.fetch_all(api, Budget.load(None, TODAY))
        self.assertEqual(ctx.exception.reason, "totalCount changed")

    def test_short_page_fails(self):
        api, _ = make_client(lambda url, i: ok(page_body(rows(999), 1000)))
        with self.assertRaises(ApiError) as ctx:
            onbid_list.fetch_all(api, Budget.load(None, TODAY))
        self.assertEqual(ctx.exception.reason, "row count mismatch")

    def test_stops_before_the_daily_cap(self):
        api, transport = make_client(lambda url, i: ok(page_body(rows(1000), 49000, page_no_of(url))))
        budget = Budget.load({"date": TODAY, "used": {"onbid": 870}}, TODAY)
        with self.assertRaises(BudgetExceeded):
            onbid_list.fetch_all(api, budget)
        self.assertEqual(len(transport.urls), 1)

    def test_too_many_pages_is_refused(self):
        api, transport = make_client(lambda url, i: ok(page_body(rows(1), 10_000_000)))
        with self.assertRaises(ApiError) as ctx:
            onbid_list.fetch_all(api, Budget.load(None, TODAY))
        self.assertEqual(ctx.exception.reason, "too many pages")


if __name__ == "__main__":
    unittest.main()
