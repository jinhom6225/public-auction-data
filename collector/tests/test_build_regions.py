import json
import os
import unittest

import netguard  # noqa: F401  (blocks the network)

from collector import build_regions

CSV = (
    "﻿법정동코드,시도명,시군구명,읍면동명,리명,순번,생성일자\r\n"
    "1100000000,서울특별시,,,,11,1988-04-23\r\n"
    "1111000000,서울특별시,종로구,,,1,1988-04-23\r\n"
    "1111010100,서울특별시,종로구,청운동,,1,1988-04-23\r\n"
    "4100000000,경기도,,,,41,1988-04-23\r\n"
    "4111000000,경기도,수원시,,,1,1988-04-23\r\n"
    "4111100000,경기도,수원시장안구,,,1,1988-04-23\r\n"
    "4111112900,경기도,수원시장안구,파장동,,1,1988-04-23\r\n"
    "4182025000,경기도,가평군,가평읍,,1,1988-04-23\r\n"
    "4182025021,경기도,가평군,가평읍,대곡리,1,1988-04-23\r\n"
    "4182000000,경기도,가평군,,,1,1988-04-23\r\n"
)


class BuildRegionsTest(unittest.TestCase):
    def test_builds_sidos_sggs_and_emds(self):
        built = build_regions.build(CSV, source={"file": "x.csv"})
        self.assertEqual(built["sidos"], {"11": "서울특별시", "41": "경기도"})
        self.assertEqual(built["sggs"]["11110"], {"name": "종로구", "sido": "11", "parent": False})
        self.assertTrue(built["sggs"]["41110"]["parent"])
        self.assertFalse(built["sggs"]["41111"]["parent"])
        self.assertEqual(built["emds"], {
            "1111010100": "청운동", "4111112900": "파장동", "4182025000": "가평읍",
        })
        self.assertEqual(built["ris"], {"4182025021": "대곡리"})
        self.assertEqual(built["source"]["dataset"], "15063424")

    def test_rejects_unknown_header(self):
        with self.assertRaises(ValueError):
            build_regions.build("a,b,c\r\n1,2,3\r\n", source={})

    def test_rejects_dong_without_district(self):
        bad = CSV.replace("1111000000,서울특별시,종로구,,,1,1988-04-23\r\n", "")
        with self.assertRaises(ValueError):
            build_regions.build(bad, source={})

    def test_output_is_deterministic(self):
        built = build_regions.build(CSV, source={})
        self.assertEqual(build_regions.dumps(built), build_regions.dumps(build_regions.build(CSV, source={})))
        self.assertTrue(build_regions.dumps(built).endswith("\n"))


class CommittedRegionsTest(unittest.TestCase):
    """The committed regions.json matches the app bundle's source CSV."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(netguard.REPO_ROOT, "collector", "data", "regions.json")
        with open(path, encoding="utf-8") as handle:
            cls.regions = json.load(handle)

    def test_source_is_the_app_bundle_csv(self):
        self.assertEqual(
            self.regions["source"]["sha256"],
            "e5657d4b53a16f72e42e9c0d91e84ff2d647e70dc56b9408da6fd057d0da45c3",
        )
        self.assertEqual(self.regions["source"]["dataDate"], "2026-06-30")

    def test_current_codes_only(self):
        sidos = self.regions["sidos"]
        self.assertEqual(len(sidos), 16)
        for old in ("29", "42", "45", "46"):
            self.assertNotIn(old, sidos)
        for new in ("12", "51", "52"):
            self.assertIn(new, sidos)

    def test_district_counts_match_the_app_bundle(self):
        sggs = self.regions["sggs"]
        parents = [code for code, sgg in sggs.items() if sgg["parent"]]
        self.assertEqual(len(sggs), 269)
        self.assertEqual(len(parents), 13)
        self.assertIn("36110", sggs)
        for split_code in ("28125", "28155", "28275", "28290", "41591", "41597"):
            self.assertIn(split_code, sggs)
        for gone in ("28110", "28140", "28260"):
            self.assertNotIn(gone, sggs)

    def test_dong_count(self):
        self.assertEqual(len(self.regions["emds"]), 5067)
        self.assertGreater(len(self.regions["ris"]), 10000)
        for code in self.regions["ris"]:
            self.assertIn(code[:8] + "00", self.regions["emds"])


if __name__ == "__main__":
    unittest.main()
