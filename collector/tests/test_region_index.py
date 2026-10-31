import unittest

import netguard  # noqa: F401  (blocks the network)
from fakes import raw_row

from collector.region_index import Location, RegionIndex

INDEX = RegionIndex.load()


def locate(**overrides):
    return INDEX.locate(raw_row(**overrides))


class AddressFirstTest(unittest.TestCase):
    def test_plain_address(self):
        self.assertEqual(locate(), Location("11110", "1111010100", "청운동"))

    def test_general_district_name_with_space(self):
        found = locate(lctnSdnm="경기도", lctnSggnm="수원시 영통구", lctnEmdNm="매탄동", ltnoPnu=None)
        self.assertEqual(found.sgg, "41117")
        self.assertIsNotNone(found.umd)

    def test_sejong_has_no_district_name(self):
        found = locate(lctnSdnm="세종특별자치시", lctnSggnm=None, lctnEmdNm="반곡동", ltnoPnu=None)
        self.assertEqual(found, Location("36110", "3611010100", "반곡동"))

    def test_split_sibling_resolves_dong(self):
        # Onbid still files 왕길동 under 서해구; the 2026-06-30 file has it in 검단구.
        found = locate(lctnSdnm="인천광역시", lctnSggnm="서해구", lctnEmdNm="왕길동", ltnoPnu=None)
        self.assertEqual(found, Location("28290", "2829010900", "왕길동"))

    def test_renamed_dong_keeps_district(self):
        found = locate(lctnSdnm="경기도", lctnSggnm="용인시 처인구", lctnEmdNm="양지면", ltnoPnu=None)
        self.assertEqual(found, Location("41461", None, "양지면"))

    def test_parent_city_resolves_by_dong(self):
        found = locate(lctnSdnm="경기도", lctnSggnm="화성시", lctnEmdNm="남양읍", ltnoPnu=None)
        self.assertEqual(found, Location("41591", "4159125600", "남양읍"))

    def test_parent_city_with_unknown_dong_is_not_filed_under_the_city(self):
        self.assertIsNone(locate(lctnSdnm="경기도", lctnSggnm="화성시", lctnEmdNm="없는동", ltnoPnu=None,
                                 onbidCltrNm="물건"))


class PnuFallbackTest(unittest.TestCase):
    def test_current_pnu(self):
        found = locate(lctnSdnm=None, lctnSggnm=None, lctnEmdNm=None, ltnoPnu="1111010100100120003")
        self.assertEqual(found, Location("11110", "1111010100", "청운동"))

    def test_old_code_pnu_one_to_one(self):
        for old in ("2911010100100010000",):
            found = locate(lctnSdnm=None, lctnSggnm=None, lctnEmdNm=None, ltnoPnu=old, onbidCltrNm="x")
            self.assertEqual(found, Location("12210", "1221010100", "대인동"))

    def test_old_split_code_without_dong_name_is_dropped(self):
        self.assertIsNone(locate(lctnSdnm=None, lctnSggnm=None, lctnEmdNm=None,
                                 ltnoPnu="2826011200100010000", onbidCltrNm="x"))

    def test_old_split_code_with_dong_name(self):
        found = locate(lctnSdnm=None, lctnSggnm=None, lctnEmdNm="가좌동", ltnoPnu="2826011200100010000",
                       onbidCltrNm="x")
        self.assertEqual(found, Location("28275", "2827511000", "가좌동"))


class RegionMapTableTest(unittest.TestCase):
    """Every old code in region_map.json lands on a current, selectable district."""

    def test_every_one_to_one_target_is_current(self):
        for old, new in INDEX.one_to_one.items():
            with self.subTest(old):
                self.assertFalse(INDEX.is_selectable(old))
                self.assertTrue(INDEX.is_selectable(new), new)
                self.assertEqual(INDEX.sggs[new]["sido"], {"29": "12", "46": "12", "42": "51", "45": "52"}[old[:2]])

    def test_every_split_target_is_current(self):
        for old, targets in INDEX.split.items():
            with self.subTest(old):
                for target in targets:
                    self.assertTrue(INDEX.is_selectable(target), target)

    def test_one_pnu_per_old_province_family(self):
        for old in ("29110", "46110", "42110", "45111"):
            new = INDEX.one_to_one[old]
            suffix = next(code[5:] for code in INDEX.emds if code[:5] == new)
            found = locate(lctnSdnm=None, lctnSggnm=None, lctnEmdNm=None, onbidCltrNm="x",
                           ltnoPnu=old + suffix + "100010000")
            with self.subTest(old):
                self.assertEqual(found.sgg, new)
                self.assertEqual(found.umd, new + suffix)


class NameFallbackTest(unittest.TestCase):
    def test_address_from_item_name(self):
        found = locate(lctnSdnm=None, lctnSggnm=None, lctnEmdNm=None, ltnoPnu=None,
                       onbidCltrNm="전남광주통합특별시 여수시 화양면 서촌리 1431-1  ")
        self.assertEqual(found, Location("12130", "1213033000", "화양면"))

    def test_two_word_district_in_name(self):
        found = locate(lctnSdnm=None, lctnSggnm=None, lctnEmdNm=None, ltnoPnu=None,
                       onbidCltrNm="경기도 수원시 영통구 매탄동 1 아파트")
        self.assertEqual(found.sgg, "41117")

    def test_nothing_usable(self):
        self.assertIsNone(locate(lctnSdnm=None, lctnSggnm=None, lctnEmdNm=None, ltnoPnu=None,
                                 onbidCltrNm="충남 태안군 남면 신온리 810"))


class LookupTest(unittest.TestCase):
    def test_selectable_and_names(self):
        self.assertTrue(INDEX.is_selectable("41111"))
        self.assertFalse(INDEX.is_selectable("41110"))
        self.assertFalse(INDEX.is_selectable("29110"))
        self.assertEqual(INDEX.sido_name("12"), "전남광주통합특별시")
        self.assertEqual(len(INDEX.sido_codes()), 16)


if __name__ == "__main__":
    unittest.main()
