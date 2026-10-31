import unittest

import netguard  # noqa: F401  (blocks the network)

from collector import pnu


class ItemKeyTest(unittest.TestCase):
    def test_drops_the_eleventh_digit(self):
        self.assertEqual(pnu.item_key("1111010100100120003"), "111101010000120003")

    def test_mountain_flag_zero_and_one_give_the_same_key(self):
        self.assertEqual(pnu.item_key("1111010100000120003"), pnu.item_key("1111010100100120003"))

    def test_invalid_pnu_has_no_key(self):
        for bad in (None, "", "123", "11110101001001200031", "11110101001001200a3", 1111010100100120003):
            self.assertIsNone(pnu.item_key(bad))


class LotTest(unittest.TestCase):
    def test_main_and_sub(self):
        self.assertEqual(pnu.jibun_lot("645-32"), ("0645", "0032"))

    def test_main_only(self):
        self.assertEqual(pnu.jibun_lot("1022"), ("1022", "0000"))

    def test_mountain_lot_ignores_the_flag(self):
        self.assertEqual(pnu.jibun_lot("산1-9"), ("0001", "0009"))
        self.assertEqual(pnu.jibun_lot("산 12"), ("0012", "0000"))

    def test_masked_lots_are_ignored(self):
        for masked in ("2**", "산1*", "1***", "*", "9*-1", "", " ", None, "12-", "-3", "12345", "１２"):
            self.assertIsNone(pnu.jibun_lot(masked), masked)

    def test_apt_lot_from_bonbun_bubun(self):
        self.assertEqual(pnu.apt_lot({"bonbun": "1337", "bubun": "0002", "jibun": "1337-2"}), ("1337", "0002"))

    def test_apt_lot_falls_back_to_jibun(self):
        self.assertEqual(pnu.apt_lot({"bonbun": "", "bubun": "", "jibun": "664-6"}), ("0664", "0006"))

    def test_apt_lot_zero_main_is_unknown(self):
        self.assertIsNone(pnu.apt_lot({"bonbun": "0000", "bubun": "0000", "jibun": ""}))


class BuildingKeyTest(unittest.TestCase):
    def test_reconstructed_row_key_matches_the_item(self):
        # 쌍촌동 1337-2 (광주 서구 12240 + umdCd 11800), Onbid PNU with flag 0.
        row_key = pnu.building_key("1224011800", ("1337", "0002"))
        self.assertEqual(row_key, pnu.item_key("1224011800013370002"))

    def test_different_sub_lot_does_not_match(self):
        row_key = pnu.building_key("1224011800", ("1337", "0003"))
        self.assertNotEqual(row_key, pnu.item_key("1224011800013370002"))

    def test_mountain_item_matches_a_mountain_row(self):
        self.assertEqual(pnu.building_key("4146125021", pnu.jibun_lot("산1-9")),
                         pnu.item_key("4146125021100010009"))

    def test_missing_parts_give_no_key(self):
        self.assertIsNone(pnu.building_key(None, ("0001", "0000")))
        self.assertIsNone(pnu.building_key("4146125021", None))
        self.assertIsNone(pnu.building_key("41461", ("0001", "0000")))


if __name__ == "__main__":
    unittest.main()
