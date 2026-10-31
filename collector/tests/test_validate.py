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
