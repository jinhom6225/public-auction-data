import datetime
import unittest

import netguard  # noqa: F401  (blocks the network)

from collector import validate
from collector.region_index import RegionIndex

INDEX = RegionIndex.load()
NOW = datetime.datetime(2026, 11, 1, 20, 10, tzinfo=datetime.timezone.utc)


def good(**overrides):
    record = {
        "key": "2025-09941-003", "round": "2", "name": "서울 종로구 청운동 12-3",
        "pnu": "1111010100100120003", "sgg": "11110", "umd": "1111010100", "emdName": "청운동",
        "usage": {"l": "부동산", "m": "주거용건물", "s": "아파트", "group": "apt"},
        "assetType": "압류재산", "landM2": 32.1, "bldgM2": 84.9, "share": False,
        "appraisal": 950000000, "lowestBid": 665000000, "lowestBidText": None, "ratioToAppraisal": 70,
        "failCount": 3, "bidStart": "2026-11-23T10:00:00+09:00", "bidEnd": "2026-11-25T17:00:00+09:00",
        "thumb": "https://www.onbid.co.kr/x", "onbidUrl": "https://www.onbid.co.kr/y",
        "firstSeen": "2026-11-02", "priceDropAt": None, "prevLowestBid": None,
        "compare": {"kind": "none"}, "nextRounds": [],
    }
    return {**record, **overrides}


class RecordRulesTest(unittest.TestCase):
    def check(self, **overrides):
        return validate.record_problems(good(**overrides), "11", INDEX)

    def test_good_record(self):
        self.assertEqual(self.check(), [])

    def test_key_rules(self):
        self.assertEqual(self.check(key="TX_0432191020260902002864"), [])
        for bad in ("", "a b", "a/b", "x" * 41, None, "../x"):
            self.assertIn("invalid key", self.check(key=bad))

    def test_region_rules(self):
        self.assertIn("invalid sgg", self.check(sgg="41110"))     # parent city, not selectable
        self.assertIn("invalid sgg", self.check(sgg="29110"))     # old code
        self.assertIn("invalid sgg", self.check(sgg="26110"))     # other province than the file
        self.assertIn("invalid umd", self.check(umd="2611010100"))
        self.assertEqual(self.check(umd=None, pnu=None), [])

    def test_amount_and_count_ranges(self):
        for field in ("appraisal", "lowestBid", "prevLowestBid"):
            self.assertIn(f"invalid {field}", self.check(**{field: 0}))
            self.assertIn(f"invalid {field}", self.check(**{field: 10**13 + 1}))
        self.assertIn("invalid failCount", self.check(failCount=-1))
        self.assertEqual(self.check(failCount=108), [])
        self.assertIn("invalid failCount", self.check(failCount=1000))
        self.assertIn("invalid bldgM2", self.check(bldgM2=0))

    def test_dates(self):
        self.assertIn("invalid bidStart", self.check(bidStart="2026-11-23 10:00"))
        self.assertIn("bidStart after bidEnd", self.check(bidStart="2026-11-26T10:00:00+09:00"))
        self.assertIn("bidStart and bidEnd", self.check(bidStart=None)[0])
        self.assertEqual(self.check(bidStart=None, bidEnd=None), [])
        self.assertIn("invalid firstSeen", self.check(firstSeen=None))

    def test_urls_https_only(self):
        self.assertIn("invalid thumb", self.check(thumb="http://www.onbid.co.kr/x"))
        self.assertEqual(self.check(thumb=None, onbidUrl=None), [])

    def test_text_limits(self):
        self.assertIn("invalid emdName", self.check(emdName="가" * 21))
        self.assertIn("invalid name", self.check(name=""))
        self.assertEqual(self.check(name="가" * 301), [])
        self.assertEqual(self.check(name="가" * 602), [])
        self.assertIn("invalid name", self.check(name="가" * 603))
        self.assertEqual(self.check(round="1" * 10), [])
        self.assertIn("invalid round", self.check(round="1" * 11))

    def test_links_must_be_on_the_onbid_host(self):
        # S-L4
        for field in ("thumb", "onbidUrl"):
            for bad in ("https://evil.example/x", "https://www.onbid.co.kr.evil.example/x",
                        "https://user@www.onbid.co.kr/x"):
                self.assertIn(f"invalid {field}", self.check(**{field: bad}))

    def test_usage_and_compare(self):
        self.assertIn("invalid usage", self.check(usage={"l": None, "m": None, "s": None, "group": "boat"}))
        self.assertIn("invalid compare", self.check(compare={"kind": "maybe"}))

    def test_next_rounds(self):
        rounds = [{"bidStart": None, "bidEnd": None, "lowestBid": None}] * 4
        self.assertIn("invalid nextRounds", self.check(nextRounds=rounds))
        self.assertIn("invalid nextRounds", self.check(nextRounds=[{"bidStart": "x", "bidEnd": None, "lowestBid": 1}]))

    def test_unexpected_or_missing_fields(self):
        record = good()
        del record["share"]
        self.assertIn("missing share", validate.record_problems(record, "11", INDEX))
        self.assertIn("unexpected extra field", validate.record_problems(good(extra=1), "11", INDEX))


class DropLimitTest(unittest.TestCase):
    def test_share_and_count(self):
        self.assertFalse(validate.too_many_dropped(dropped=20, total=100))
        self.assertTrue(validate.too_many_dropped(dropped=21, total=100))
        self.assertFalse(validate.too_many_dropped(dropped=500, total=10000))
        self.assertTrue(validate.too_many_dropped(dropped=501, total=10000))
        self.assertFalse(validate.too_many_dropped(dropped=0, total=0))


class DocumentRulesTest(unittest.TestCase):
    def test_generated_at_rules(self):
        self.assertEqual(validate.generated_at_problems("2026-11-01T20:10:00Z", NOW), [])
        self.assertTrue(validate.generated_at_problems("2026-11-03T20:10:00Z", NOW))
        self.assertTrue(validate.generated_at_problems("2026-11-01 20:10", NOW))

    def test_region_file_pattern(self):
        self.assertTrue(validate.region_file_ok({"sido": "11", "file": "items/11.json"}))
        for entry in ({"sido": "11", "file": "items/26.json"}, {"sido": "11", "file": "../items/11.json"},
                      {"sido": "1", "file": "items/1.json"}, {"sido": "11", "file": "items/11.json/"}):
            self.assertFalse(validate.region_file_ok(entry))


if __name__ == "__main__":
    unittest.main()


DEAL = {"ym": "2026-09", "m2": 84.75, "floor": 12, "amount": 306500000}
SAME_BUILDING = {"kind": "sameBuilding", "months": 12, "n": 3, "medianPerM2": 3616000, "estimate": 307000000,
                 "ratio": 65, "deals": [DEAL, {**DEAL, "floor": None}, {**DEAL, "floor": -1}]}
SAME_DONG = {"kind": "sameDong", "months": 12, "n": 1, "medianPerM2": 3616000, "deals": [DEAL]}


class CompareRulesTest(unittest.TestCase):
    """Phase 3: exact `compare` shapes; the app (Phase 4) applies the same rules."""

    def problems(self, compare):
        return validate.record_problems(good(compare=compare), "11", INDEX)

    def test_good_shapes(self):
        for compare in ({"kind": "none"}, SAME_BUILDING, {**SAME_BUILDING, "ratio": None}, SAME_DONG):
            self.assertEqual(self.problems(compare), [], compare)

    def test_bad_shapes(self):
        bad = [
            {"kind": "none", "n": 0},
            {**SAME_BUILDING, "extra": 1},
            {k: v for k, v in SAME_BUILDING.items() if k != "estimate"},
            {**SAME_DONG, "ratio": 50},
            {**SAME_BUILDING, "ratio": -1}, {**SAME_BUILDING, "ratio": 100001}, {**SAME_BUILDING, "ratio": 6.5},
            {**SAME_BUILDING, "estimate": 0}, {**SAME_BUILDING, "medianPerM2": 0}, {**SAME_BUILDING, "n": 0},
            {**SAME_BUILDING, "n": True}, {**SAME_BUILDING, "months": 0}, {**SAME_BUILDING, "months": 25},
            {**SAME_BUILDING, "n": 2, "deals": [DEAL] * 3},
            {**SAME_BUILDING, "n": 2, "deals": [DEAL] * 2},                 # LOW-1: below minDeals 3
            {**SAME_BUILDING, "n": 25, "deals": [DEAL] * 3},                # deals must be min(n, 10)
            {**SAME_BUILDING, "n": 4, "deals": [DEAL] * 3},
            {**SAME_DONG, "deals": []},
            {**SAME_DONG, "n": 11, "deals": [DEAL] * 11},
            {**SAME_DONG, "deals": [{**DEAL, "ym": "2026-9"}]},
            {**SAME_DONG, "deals": [{**DEAL, "ym": "2026-13"}]},
            {**SAME_DONG, "deals": [{**DEAL, "m2": 0}]},
            {**SAME_DONG, "deals": [{**DEAL, "floor": 1.5}]},
            {**SAME_DONG, "deals": [{**DEAL, "amount": 0}]},
            {**SAME_DONG, "deals": [{**DEAL, "jibun": "1-1"}]},
            {**SAME_DONG, "deals": [{k: v for k, v in DEAL.items() if k != "floor"}]},
            "none",
        ]
        for compare in bad:
            self.assertIn("invalid compare", self.problems(compare), compare)

    def test_same_building_lists_the_ten_newest_of_n(self):
        self.assertEqual(self.problems({**SAME_BUILDING, "n": 25, "deals": [DEAL] * 10}), [])

    def test_share_or_private_price_needs_a_null_ratio(self):
        for overrides in ({"share": True}, {"lowestBid": None, "lowestBidText": "비공개", "ratioToAppraisal": None}):
            record = good(compare=SAME_BUILDING, **overrides)
            self.assertIn("invalid compare", validate.record_problems(record, "11", INDEX), overrides)
            record = good(compare={**SAME_BUILDING, "ratio": None}, **overrides)
            self.assertEqual(validate.record_problems(record, "11", INDEX), [], overrides)

    def test_min_deals_matches_compare_params(self):
        from collector import compare
        params = compare.Params.load()
        self.assertEqual((validate.MIN_SAME_BUILDING, validate.MAX_DEALS), (params.building_min, params.building_shown))
        self.assertEqual(validate.MAX_DEALS, params.dong_max)


class ContactInNameTest(unittest.TestCase):
    """Security review M1: names carrying phone numbers or links are dropped (the app has the same rule)."""

    BASE = "서울특별시 강서구 화곡동 645-32 "

    def problems(self, name):
        return validate.record_problems(good(name=name), "11", INDEX)

    def test_phone_numbers_and_links_are_rejected(self):
        for tail in ("문의 010-1234-5678", "01012345678", "010 1234 5678", "010.1234.5678", "011-123-4567",
                     "(02-123-4567)", "031-1234-5678", "연락처 0505-123-4567", "http://example.test/a",
                     "HTTPS 안내", "www.example", "hxxp://x", "블로그 example.com", "abc.co.kr 참고",
                     "site.net/a", "foo.kr", "EXAMPLE.COM", "ftp://x"):
            self.assertEqual(self.problems(self.BASE + tail)[:1], ["contact in name"], tail)

    def test_lot_numbers_and_ordinary_text_pass(self):
        for name in ("서울특별시 강서구 화곡동 123-45", "경기도 양평군 산 1234-5678", "화곡동 1234-5678번지",
                     "인천광역시 미추홀구 도화동 624-226 외 2필지 더머뭄 401호", "제101동 제14층 제1401호",
                     "대지 1,234.5㎡ 중 1/2 지분", "B동 101호 (도로명: 화곡로 12-3)", "중구 무교동 1",
                     "1012-3456 외 3필지", "강서구 등촌동 10-12 3층", "전남 순천시 해룡면 상삼리 693 금당그리안아파트"):
            self.assertEqual(self.problems(name), [], name)

    def test_contact_is_the_drop_reason_even_with_other_problems(self):
        self.assertEqual(validate.record_problems(good(name="x 010-1234-5678", failCount=-1), "11", INDEX)[0],
                         "contact in name")
