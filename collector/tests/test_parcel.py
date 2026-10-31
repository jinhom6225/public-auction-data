import unittest

import netguard  # noqa: F401  (blocks the network)

from collector import parcel
from collector.region_index import Location, RegionIndex

INDEX = RegionIndex.load()
CHEONGUN = Location("11110", "1111010100", "청운동")


class OnbidPnuTest(unittest.TestCase):
    def test_current_pnu_is_kept(self):
        self.assertEqual(parcel.from_onbid_pnu("1111010100100120003", CHEONGUN, INDEX), "1111010100100120003")

    def test_old_prefix_is_replaced(self):
        location = Location("12210", "1221010100", "대인동")
        self.assertEqual(parcel.from_onbid_pnu("2911010100100010000", location, INDEX), "1221010100100010000")

    def test_split_code_takes_the_dong_from_the_address(self):
        location = Location("28275", "2827511000", "가좌동")
        self.assertEqual(parcel.from_onbid_pnu("2826011200100010002", location, INDEX), "2827511000100010002")

    def test_village_code_is_kept(self):
        location = Location("12130", "1213033000", "화양면")
        self.assertEqual(parcel.from_onbid_pnu("1213033027114310001", location, INDEX), "1213033027114310001")

    def test_bad_values(self):
        for bad in (None, "", "12345", "111101010010012000X", "11110101001001200031"):
            self.assertIsNone(parcel.from_onbid_pnu(bad, CHEONGUN, INDEX))

    def test_old_split_without_dong_gives_none(self):
        self.assertIsNone(parcel.from_onbid_pnu("2826011200100010002", Location("28275", None, None), INDEX))


class NameJibunTest(unittest.TestCase):
    def test_dong_lot(self):
        location = Location("41220", "4122010200", "장당동")
        pnu = parcel.from_item_name("경기도 평택시 장당동 483-6 201호 근린생활시설", location, INDEX)
        self.assertEqual(pnu, "4122010200004830006")

    def test_village_and_mountain_lot(self):
        location = Location("51830", "5183025000", "양양읍")
        pnu = parcel.from_item_name("강원특별자치도 양양군 양양읍 청곡리 산 119-2 외 10개필지", location, INDEX)
        self.assertEqual(pnu, "5183025026101190002")

    def test_village_without_sub_number(self):
        location = Location("12130", "1213033000", "화양면")
        pnu = parcel.from_item_name("전남광주통합특별시 여수시 화양면 서촌리 1431", location, INDEX)
        self.assertEqual(pnu, "1213033027014310000")

    def test_unknown_village_or_no_lot(self):
        location = Location("12130", "1213033000", "화양면")
        self.assertIsNone(parcel.from_item_name("여수시 화양면 없는리 1", location, INDEX))
        self.assertIsNone(parcel.from_item_name("여수시 화양면 서촌리 일원", location, INDEX))
        self.assertIsNone(parcel.from_item_name("장당동 483-6", Location("41220", None, "장당동"), INDEX))

    def test_lot_number_longer_than_four_digits_is_ignored(self):
        location = Location("41220", "4122010200", "장당동")
        self.assertIsNone(parcel.from_item_name("평택시 장당동 12345", location, INDEX))


if __name__ == "__main__":
    unittest.main()
