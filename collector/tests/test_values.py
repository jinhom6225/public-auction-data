import unittest

import netguard  # noqa: F401  (blocks the network)
from fakes import raw_row

from collector import values
from collector.usage import UsageTable


class PriceTest(unittest.TestCase):
    def test_digits_become_a_number(self):
        self.assertEqual(values.lowest_bid("665000000"), (665000000, None))

    def test_private_and_odd_text_keep_the_text(self):
        self.assertEqual(values.lowest_bid("비공개"), (None, "비공개"))
        self.assertEqual(values.lowest_bid("1,000원"), (None, "1,000원"))
        self.assertEqual(values.lowest_bid(" 비공개 "), (None, "비공개"))

    def test_empty_and_zero(self):
        self.assertEqual(values.lowest_bid(""), (None, None))
        self.assertEqual(values.lowest_bid(None), (None, None))
        self.assertEqual(values.lowest_bid("0"), (None, None))

    def test_long_text_is_cut_to_the_limit(self):
        number, text = values.lowest_bid("가" * 40)
        self.assertIsNone(number)
        self.assertEqual(len(text), 30)

    def test_amount(self):
        self.assertEqual(values.amount(950000000), 950000000)
        for bad in (0, None, -1, "12", 1.5, True, 10**13 + 1):
            self.assertIsNone(values.amount(bad))

    def test_ratio_half_up(self):
        self.assertEqual(values.ratio(665000000, 950000000), 70)
        self.assertEqual(values.ratio(1, 200), 1)       # 0.5 -> 1 (half up)
        self.assertEqual(values.ratio(373248000, 256000000), 146)
        self.assertIsNone(values.ratio(None, 100))
        self.assertIsNone(values.ratio(100, None))


class AreaTest(unittest.TestCase):
    def test_area(self):
        self.assertEqual(values.area(84.9), 84.9)
        self.assertEqual(values.area(62), 62)
        for bad in (0, -3, None, "84", True, 1e7 + 1):
            self.assertIsNone(values.area(bad))


class DateTimeTest(unittest.TestCase):
    def test_kst_iso(self):
        self.assertEqual(values.bid_time("202611021000"), "2026-11-02T10:00:00+09:00")

    def test_placeholder_and_garbage(self):
        for bad in ("299912301000", "", None, "20261302", "202613021000", "abcd"):
            self.assertIsNone(values.bid_time(bad))


class UrlTest(unittest.TestCase):
    def test_thumbnail_https_onbid_only(self):
        good = "https://www.onbid.co.kr/op/cm/x.do?a=1"
        self.assertEqual(values.onbid_https(good), good)
        for bad in ("http://www.onbid.co.kr/x", "https://evil.example/x", "https://www.onbid.co.kr.evil/x",
                    "", None, "https://www.onbid.co.kr/" + "a" * 3000, "https://user@www.onbid.co.kr/x"):
            self.assertIsNone(values.onbid_https(bad))

    def test_item_link(self):
        url = values.item_link(raw_row())
        self.assertEqual(
            url,
            "https://www.onbid.co.kr/op/cltrpbancinf/cltrdtl/CltrDtlController/mvmnCltrDtl.do"
            "?onbidCltrno=1573873&onbidPbancNo=662306&pbctNo=9621163&pbctCdtnNo=3912726",
        )

    def test_item_link_needs_all_four_numbers(self):
        self.assertIsNone(values.item_link(raw_row(pbctNo=None)))
        self.assertIsNone(values.item_link(raw_row(onbidCltrno="12&x=1")))


class UsageTest(unittest.TestCase):
    table = UsageTable.load()

    def test_each_known_group(self):
        cases = {"10201": "apt", "10206": "rowhouse", "10503": "officetel", "10204": "house",
                 "10101": "land", "10301": "commercial", "10401": "other", "10219": "rowhouse",
                 "10202": "apt", "10215": "house", "10331": "commercial", "10504": "commercial"}
        for code, group in cases.items():
            self.assertEqual(self.table.group_of("10000", "10200", code), group, code)

    def test_unknown_small_code_uses_mid_then_other(self):
        self.assertEqual(self.table.group_of("10000", "10100", "19999"), "land")
        self.assertEqual(self.table.group_of("10000", "99999", "19999"), "other")
        self.assertEqual(self.table.group_of(None, None, None), "other")

    def test_usage_record_and_unknown_flag(self):
        usage, known = self.table.usage(raw_row())
        self.assertEqual(usage, {"l": "부동산", "m": "주거용건물", "s": "아파트", "group": "apt"})
        self.assertTrue(known)
        _, known = self.table.usage(raw_row(cltrUsgSclsCtgrId="19999"))
        self.assertFalse(known)

    def test_rtms_alias_for_phase_3(self):
        self.assertEqual(self.table.rtms_of("10201"), "apt")
        self.assertIsNone(self.table.rtms_of("10211"))


if __name__ == "__main__":
    unittest.main()
