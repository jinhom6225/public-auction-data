import unittest
import urllib.parse

import netguard  # noqa: F401  (blocks the network)
from fakes import FAKE_KEY, FakeClock, RecordingTransport, ok, rtms_body, rtms_item

from collector import rtms_client
from collector.budget import Budget
from collector.errors import ApiError
from collector.http_client import ApiClient, Response
from collector.region_index import RegionIndex

INDEX = RegionIndex.load()


def read_raw(name):
    with open(netguard.fixture_path("raw", name), "rb") as handle:
        return handle.read()


def client(transport, clock=None):
    clock = clock or FakeClock()
    return ApiClient(transport, FAKE_KEY, tps=20, monotonic=clock.monotonic, sleep=clock.sleep)


def budget(**used):
    return Budget.load({"date": "2026-11-02", "used": used}, "2026-11-02")


class StrictXmlTest(unittest.TestCase):
    def test_real_page_parses(self):
        page = rtms_client.parse_page(read_raw("rtms_apt_12210_202608.xml"), "op")
        self.assertEqual(page.total, 78)
        self.assertEqual(len(page.rows), 78)
        self.assertEqual(page.rows[0]["umdNm"], "학동")
        self.assertEqual(page.rows[0]["cdealType"], "")       # ' ' is stripped

    def test_empty_old_code_page(self):
        page = rtms_client.parse_page(read_raw("rtms_apt_29110_202605.xml"), "op")
        self.assertEqual((page.total, page.rows), (0, ()))

    def test_uppercase_utf8_declaration_is_accepted(self):
        body = rtms_body([]).replace(b'encoding="utf-8"', b'encoding="UTF-8"')
        self.assertEqual(rtms_client.parse_page(body, "op").total, 0)

    def test_no_declaration_is_accepted(self):
        body = rtms_body([]).split(b"?>", 1)[1]
        self.assertEqual(rtms_client.parse_page(body, "op").total, 0)

    def assert_refused(self, body, reason):
        with self.assertRaises(ApiError) as caught:
            rtms_client.parse_page(body, "op")
        self.assertIn(reason, str(caught.exception))

    def test_bom_is_refused(self):
        self.assert_refused(b"\xef\xbb\xbf" + rtms_body([]), "BOM")

    def test_other_encoding_declaration_is_refused(self):
        body = rtms_body([]).replace(b'encoding="utf-8"', b'encoding="euc-kr"')
        self.assert_refused(body, "encoding")

    def test_non_utf8_bytes_are_refused(self):
        self.assert_refused(rtms_body([rtms_item(umdNm="학동")]).replace("학동".encode(), "학동".encode("cp949")),
                            "UTF-8")

    def test_utf16_doctype_is_refused(self):
        text = '<?xml version="1.0" encoding="utf-16"?><!DOCTYPE r [<!ENTITY a "b">]><response>&a;</response>'
        for codec in ("utf-16", "utf-16-le", "utf-16-be"):
            with self.assertRaises(ApiError, msg=codec):
                rtms_client.parse_page(text.encode(codec), "op")

    def test_nul_and_illegal_control_characters_are_refused(self):
        self.assert_refused(rtms_body([]).replace(b"<header>", b"<header>\x00"), "control")
        for char in ("\x01", "\x08", "\x0b", "\x1f", "\ufffe"):
            self.assert_refused(rtms_body([rtms_item(umdNm="화곡" + char)]), "control")
        self.assertEqual(rtms_client.parse_page(rtms_body([rtms_item(umdNm="a\tb")]), "op").total, 1)

    def test_body_must_start_with_a_tag(self):
        for prefix in (b" ", b"\n", b"x"):
            self.assert_refused(prefix + rtms_body([]), "start")

    def test_item_with_too_many_fields_is_refused(self):
        wide = rtms_item(**{f"f{i}": "x" for i in range(60)})
        self.assert_refused(rtms_body([wide]), "fields")
        self.assertEqual(rtms_client.parse_page(rtms_body([rtms_item(**{f"f{i}": "x" for i in range(40)})]),
                         "op").total, 1)

    def test_expat_is_recent_enough(self):
        import pyexpat
        version = tuple(int(part) for part in pyexpat.EXPAT_VERSION.split("_")[1].split("."))
        self.assertGreaterEqual(version, (2, 4, 1))

    def test_doctype_and_entity_in_decoded_text_are_refused(self):
        for extra in ('<!DOCTYPE response>', '<!ENTITY x "y">', "<! doctype response>"):
            body = rtms_body([]).replace(b"<response>", extra.encode() + b"<response>")
            self.assert_refused(body, "DOCTYPE")

    def test_key_error_envelope_gives_its_code(self):
        with self.assertRaises(ApiError) as caught:
            rtms_client.parse_page(read_raw("error_rtms_rh_fake_key.xml"), "op")
        self.assertEqual(caught.exception.result_code, "30")

    def test_non_ok_result_code(self):
        body = rtms_body([]).replace(b"<resultCode>000<", b"<resultCode>99<")
        with self.assertRaises(ApiError) as caught:
            rtms_client.parse_page(body, "op")
        self.assertEqual(caught.exception.result_code, "99")

    def test_garbage_is_refused(self):
        for body in (b"", b"{}", b"<response><header>", b"<other/>"):
            with self.assertRaises(ApiError):
                rtms_client.parse_page(body, "op")


class SummaryTest(unittest.TestCase):
    def summary(self, api, lawd, **fields):
        return rtms_client.summarize(api, lawd, rtms_item(**fields), INDEX)

    def test_apt_row(self):
        row = self.summary("apt", "12240", umdCd="11800", umdNm="쌍촌동", bonbun="1337", bubun="0002",
                           jibun="1337-2", landCd="1", dealAmount="30,650", excluUseAr="84.7555",
                           floor="17", dealDay="13")
        self.assertEqual(row, {"d": 13, "p": 306500000, "a": 84.7555, "f": 17, "u": "1224011800",
                               "k": "122401180013370002"})

    def test_apt_row_ignores_the_response_sgg_code(self):
        # D8: always the queried current code; aptSeq/sggCd may still carry 29110.
        row = self.summary("apt", "12210", sggCd="29110", umdCd="12200", umdNm="학동", bonbun="1022",
                           bubun="0000", aptSeq="29110-346")
        self.assertEqual(row["u"], "1221012200")
        self.assertEqual(row["k"], "122101220010220000")

    def test_rowhouse_row_with_village_and_mountain_lot(self):
        row = self.summary("rh", "41461", umdNm="원삼면 맹리", jibun="산1-9", excluUseAr="59.9")
        ri = INDEX.ri_code(INDEX.emd_code("41461", "원삼면"), "맹리")
        self.assertEqual(row["k"], ri + "00010009")
        self.assertEqual(row["u"], ri[:8] + "00")

    def test_unknown_dong_keeps_the_row_without_codes(self):
        row = self.summary("offi", "11500", umdNm="없는동", jibun="1-1")
        self.assertEqual((row["u"], row["k"]), (None, None))

    def test_cancelled_deal_is_excluded(self):
        self.assertIsNone(self.summary("rh", "11500", umdNm="화곡동", cdealType="O", cdealDay="26.10.30"))

    def test_share_deal_is_excluded(self):
        self.assertIsNone(self.summary("land", "41461", umdNm="삼가동", shareDealingType="지분"))

    def test_land_row_with_masked_lot(self):
        row = self.summary("land", "41461", umdNm="삼가동", jibun="산7*", jimok="임야", dealArea="496",
                           dealAmount="9,750")
        self.assertEqual(row, {"d": 1, "p": 97500000, "a": 496.0, "u": INDEX.emd_code("41461", "삼가동"),
                               "j": "임야"})

    def test_house_uses_total_floor_area(self):
        row = self.summary("sh", "41461", umdNm="김량장동", totalFloorAr="108.37", plottageAr="113")
        self.assertEqual(row["a"], 108.37)
        self.assertNotIn("f", row)

    def test_commercial_and_factory_keep_building_use(self):
        row = self.summary("nrg", "11500", umdNm="화곡동", buildingAr="59.21", buildingUse="제2종근린생활",
                           floor=" ")
        self.assertEqual((row["a"], row["b"], row["f"]), (59.21, "제2종근린생활", None))
        row = self.summary("indu", "41461", umdNm="이동읍 덕성리", buildingAr="4635.85", buildingUse="공장",
                           dealAmount="1,110,000")
        self.assertEqual((row["p"], row["b"]), (11100000000, "공장"))

    def test_basement_floor_is_negative(self):
        self.assertEqual(self.summary("rh", "11500", umdNm="화곡동", floor="-1")["f"], -1)

    def test_man_won_conversion(self):
        self.assertEqual(rtms_client.won("65,300"), 653000000)
        self.assertEqual(rtms_client.won("500"), 5000000)
        for bad in ("", " ", "6,53,00", "65.3", "-1", "0", "１２", None, "1" * 20):
            self.assertIsNone(rtms_client.won(bad), bad)

    def test_unusable_amount_or_area_is_excluded(self):
        self.assertIsNone(self.summary("rh", "11500", umdNm="화곡동", dealAmount=""))
        self.assertIsNone(self.summary("rh", "11500", umdNm="화곡동", excluUseAr="0"))
        self.assertIsNone(self.summary("rh", "11500", umdNm="화곡동", dealDay="x"))

    def test_real_pages_give_rows_for_every_api(self):
        for name, api, lawd in (("rtms_apt_12210_202608.xml", "apt", "12210"),
                                ("rtms_rh_11530_202609.xml", "rh", "11530"),
                                ("rtms_offi_11545_202609.xml", "offi", "11545"),
                                ("rtms_land_41461_202609.xml", "land", "41461"),
                                ("rtms_sh_41461_202609.xml", "sh", "41461"),
                                ("rtms_nrg_11500_202609.xml", "nrg", "11500"),
                                ("rtms_indu_41461_202609.xml", "indu", "41461")):
            page = rtms_client.parse_page(read_raw(name), "op")
            rows = [rtms_client.summarize(api, lawd, row, INDEX) for row in page.rows]
            kept = [row for row in rows if row is not None]
            self.assertTrue(kept, name)
            self.assertTrue(all(row["u"] for row in kept), name)
            self.assertEqual(set(kept[0]), set(rtms_client.ROW_FIELDS[api]), name)
            if api in ("apt", "rh", "offi"):
                self.assertGreater(sum(1 for row in kept if row["k"]), len(kept) * 0.9, name)
            excluded = sum(1 for row in page.rows
                           if row.get("cdealType") or row.get("shareDealingType") == "지분")
            self.assertEqual(len(kept), len(page.rows) - excluded, name)


def query(url):
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}


class FetchMonthTest(unittest.TestCase):
    def test_one_page(self):
        transport = RecordingTransport(lambda url, i: ok(rtms_body([rtms_item(), rtms_item(dealDay="2")])))
        result = rtms_client.fetch_month(client(transport), "rh", "11500", "202609", budget(), INDEX)
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.rows), 2)
        self.assertEqual(result.budget.used_of("rh"), 1)
        params = query(transport.urls[0])
        self.assertEqual((params["LAWD_CD"], params["DEAL_YMD"], params["numOfRows"], params["pageNo"]),
                         ("11500", "202609", "1000", "1"))
        self.assertTrue(transport.urls[0].startswith(rtms_client.ENDPOINTS["rh"].url + "?"))

    def test_pages_until_total(self):
        def handler(url, i):
            page = int(query(url)["pageNo"])
            rows = [rtms_item(dealDay=str(page))] * (1000 if page < 3 else 5)
            return ok(rtms_body(rows, total=2005, page=page))
        transport = RecordingTransport(handler)
        result = rtms_client.fetch_month(client(transport), "apt", "11500", "202609", budget(), INDEX)
        self.assertEqual((result.status, len(result.rows), result.budget.used_of("apt")), ("ok", 2005, 3))

    def test_total_changing_between_pages_fails(self):
        def handler(url, i):
            page = int(query(url)["pageNo"])
            return ok(rtms_body([rtms_item()] * (1000 if page == 1 else 3), total=1003 + i))
        result = rtms_client.fetch_month(client(RecordingTransport(handler)), "apt", "11500", "202609",
                                         budget(), INDEX)
        self.assertEqual(result.status, "failed")

    def test_short_page_fails(self):
        transport = RecordingTransport(lambda url, i: ok(rtms_body([rtms_item()], total=2)))
        result = rtms_client.fetch_month(client(transport), "rh", "11500", "202609", budget(), INDEX)
        self.assertEqual(result.status, "failed")

    def test_failed_call_is_retried_once(self):
        clock = FakeClock()
        transport = RecordingTransport(lambda url, i: Response(500, b"") if i == 0 else ok(rtms_body([rtms_item()])))
        result = rtms_client.fetch_month(client(transport, clock), "rh", "11500", "202609", budget(), INDEX)
        self.assertEqual((result.status, len(transport.urls), result.budget.used_of("rh")), ("ok", 2, 2))
        self.assertIn(2.0, clock.sleeps)

    def test_failing_twice_fails_the_month(self):
        transport = RecordingTransport(lambda url, i: Response(500, b""))
        result = rtms_client.fetch_month(client(transport), "rh", "11500", "202609", budget(), INDEX)
        self.assertEqual((result.status, len(transport.urls), result.rows), ("failed", 2, ()))
        self.assertIn("HTTP 500", result.error)

    def test_budget_is_checked_before_calling(self):
        transport = RecordingTransport(lambda url, i: ok(rtms_body([])))
        result = rtms_client.fetch_month(client(transport), "land", "41461", "202609", budget(land=900), INDEX)
        self.assertEqual((result.status, transport.urls), ("capped", []))

    def test_cap_reached_mid_month_drops_the_month(self):
        transport = RecordingTransport(lambda url, i: ok(rtms_body([rtms_item()] * 1000, total=1500)))
        result = rtms_client.fetch_month(client(transport), "land", "41461", "202609", budget(land=899), INDEX)
        self.assertEqual((result.status, result.rows, result.budget.used_of("land")), ("capped", (), 900))

    def test_daily_limit_code_stops_without_retry(self):
        body = (b'<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>LIMITED</errMsg>'
                b'<returnReasonCode>22</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>')
        transport = RecordingTransport(lambda url, i: Response(200, body))
        result = rtms_client.fetch_month(client(transport), "land", "41461", "202609", budget(), INDEX)
        self.assertEqual((result.status, len(transport.urls)), ("quota", 1))

    def test_old_or_parent_codes_are_never_called(self):
        transport = RecordingTransport(lambda url, i: ok(rtms_body([])))
        for lawd in ("29110", "41110", "1150", "x"):
            with self.assertRaises(ValueError):
                rtms_client.fetch_month(client(transport), "apt", lawd, "202609", budget(), INDEX)
        with self.assertRaises(ValueError):
            rtms_client.fetch_month(client(transport), "car", "11500", "202609", budget(), INDEX)
        self.assertEqual(transport.urls, [])

    def test_body_over_2mb_fails(self):
        big = rtms_body([]) + b" " * (2 * 1024 * 1024)
        transport = RecordingTransport(lambda url, i: ok(big))
        result = rtms_client.fetch_month(client(transport), "rh", "11500", "202609", budget(), INDEX)
        self.assertEqual(result.status, "failed")
        self.assertIn("too large", result.error)

    def test_too_many_rows_on_a_page_fails(self):
        transport = RecordingTransport(lambda url, i: ok(rtms_body([rtms_item()] * 1001, total=1001)))
        result = rtms_client.fetch_month(client(transport), "rh", "11500", "202609", budget(), INDEX)
        self.assertEqual(result.status, "failed")
        self.assertIn("more rows than", result.error)

    def test_total_above_the_page_limit_fails_after_one_page(self):
        transport = RecordingTransport(lambda url, i: ok(rtms_body([rtms_item()] * 1000, total=10001)))
        result = rtms_client.fetch_month(client(transport), "rh", "11500", "202609", budget(), INDEX)
        self.assertEqual((result.status, len(transport.urls)), ("failed", 1))

    def test_http_429_is_the_daily_limit(self):
        transport = RecordingTransport(lambda url, i: Response(429, b""))
        result = rtms_client.fetch_month(client(transport), "apt", "11500", "202609", budget(), INDEX)
        self.assertEqual((result.status, len(transport.urls)), ("quota", 1))

    def test_auth_codes_stop_without_retry(self):
        for code in ("30", "31", "32"):
            body = read_raw("error_rtms_rh_fake_key.xml").replace(b">30<", f">{code}<".encode())
            for status in (403, 200):
                transport = RecordingTransport(lambda url, i: Response(status, body))
                result = rtms_client.fetch_month(client(transport), "rh", "11500", "202609", budget(), INDEX)
                self.assertEqual((result.status, len(transport.urls)), ("auth", 1), (code, status))

    def test_errors_carry_no_key(self):
        transport = RecordingTransport(lambda url, i: Response(403, read_raw("error_rtms_rh_fake_key.xml")))
        result = rtms_client.fetch_month(client(transport), "rh", "11500", "202609", budget(), INDEX)
        self.assertEqual(result.status, "auth")
        self.assertNotIn(FAKE_KEY, result.error)
        self.assertNotIn("serviceKey", result.error)
        self.assertIn("resultCode 30", result.error)


if __name__ == "__main__":
    unittest.main()
