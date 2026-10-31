import unittest
from types import SimpleNamespace

import netguard  # noqa: F401  (blocks the network)
from fakes import item_record, rtms_item

from collector import compare, rtms_client
from collector.region_index import RegionIndex
from collector.rtms_cache import Month

PARAMS = compare.Params.load()
PNU = "1150010300106450032"            # 화곡동 645-32, flag 1 (Onbid "산" bit is ignored)
KEY = "115001030006450032"
UMD = "1150010300"


def deal(ym="202609", d=1, p=300000000, a=84.9, f=5, k=KEY, u=UMD, **extra):
    return {"ym": ym, "row": {"d": d, "p": p, "a": a, "f": f, "u": u, "k": k, **extra}}


def cache_of(api, lawd, deals):
    months = {}
    for entry in deals:
        months.setdefault(entry["ym"], []).append(entry["row"])
    return {(api, lawd, ym): Month(api, lawd, ym, "2026-10-31", tuple(rows)) for ym, rows in months.items()}


def item(api="rh", code="10206", **fields):
    base = {"pnu": PNU, "umd": UMD, "emdName": "화곡동", "bldgM2": 84.9, "landM2": 20.0,
            "lowestBid": 200000000, "share": False}
    record = item_record(sgg="11500", **{**base, **fields})
    return SimpleNamespace(record=record, rtms=api, usage_code=code)


def compare_one(it, deals, api="rh"):
    index = compare.deals_index(cache_of(api, "11500", deals))
    return compare.compare_item(it, index, PARAMS)


class SameBuildingTest(unittest.TestCase):
    def test_three_deals_give_estimate_and_ratio(self):
        deals = [deal(p=254700000), deal(p=297150000, d=2), deal(p=339600000, d=3)]   # 3.0/3.5/4.0M per m2
        result = compare_one(item(), deals)
        self.assertEqual(result["kind"], "sameBuilding")
        self.assertEqual((result["months"], result["n"]), (12, 3))
        self.assertEqual(result["medianPerM2"], 3500000)
        self.assertEqual(result["estimate"], 297150000)
        self.assertEqual(result["ratio"], 67)                     # 200,000,000 / 297,150,000
        self.assertEqual(result["deals"][0], {"ym": "2026-09", "m2": 84.9, "floor": 5, "amount": 339600000})

    def test_even_count_uses_the_middle_average(self):
        deals = [deal(p=84900000 * k, d=k) for k in (1, 2, 3, 10)]                  # 1, 2, 3, 10 M per m2
        result = compare_one(item(lowestBid=None), deals)
        self.assertEqual(result["medianPerM2"], 2500000)
        self.assertEqual(result["estimate"], 212250000)
        self.assertIsNone(result["ratio"])                           # lowestBid null

    def test_share_item_has_no_ratio(self):
        result = compare_one(item(share=True), [deal(), deal(d=2), deal(d=3)])
        self.assertEqual(result["kind"], "sameBuilding")
        self.assertIsNone(result["ratio"])

    def test_area_boundary_is_inclusive(self):
        edge = [deal(a=84.9 * 1.10, d=1), deal(a=84.9 * 0.90, d=2), deal(d=3)]
        self.assertEqual(compare_one(item(), edge)["n"], 3)
        outside = [deal(a=84.9 * 1.1001, d=1), deal(a=84.9 * 0.90, d=2), deal(d=3)]
        self.assertEqual(compare_one(item(), outside)["kind"], "sameDong")

    def test_two_deals_fall_back_to_same_dong(self):
        result = compare_one(item(), [deal(), deal(d=2)])
        self.assertEqual(result["kind"], "sameDong")
        self.assertEqual(result["n"], 2)
        self.assertNotIn("ratio", result)
        self.assertNotIn("estimate", result)

    def test_other_building_does_not_count(self):
        others = [deal(k="115001030006450033", d=i) for i in range(1, 4)]
        self.assertEqual(compare_one(item(), others)["kind"], "sameDong")

    def test_item_without_pnu_goes_to_same_dong(self):
        self.assertEqual(compare_one(item(pnu=None), [deal(), deal(d=2), deal(d=3)])["kind"], "sameDong")

    def test_n_counts_all_matches_but_deals_shows_ten_newest(self):
        deals = [deal(ym=f"2026{m:02d}", d=d) for m in range(1, 7) for d in (1, 2)]       # 12 deals
        result = compare_one(item(), deals)
        self.assertEqual((result["n"], len(result["deals"])), (12, 10))
        self.assertEqual([d["ym"] for d in result["deals"]][:2], ["2026-06", "2026-06"])
        self.assertEqual(result["deals"][-1]["ym"], "2026-02")

    def test_deals_order_is_deterministic(self):
        deals = [deal(p=310000000, f=3), deal(p=300000000, f=None), deal(p=300000000, f=2)]
        one = compare_one(item(), deals)
        two = compare_one(item(), list(reversed(deals)))
        self.assertEqual(one, two)
        self.assertEqual([d["floor"] for d in one["deals"]], [None, 2, 3])

    def test_cancelled_deals_never_reach_the_comparison(self):
        index = RegionIndex.load()
        raw = [rtms_item(umdNm="화곡동", jibun="645-32", excluUseAr="84.9", cdealType="O")] * 3
        rows = tuple(r for r in (rtms_client.summarize("rh", "11500", row, index) for row in raw) if r)
        result = compare.compare_item(item(), compare.deals_index({("rh", "11500", "202609"): Month(
            "rh", "11500", "202609", "2026-10-31", rows)}), PARAMS)
        self.assertEqual(result, {"kind": "none"})


class SameDongTest(unittest.TestCase):
    def test_same_dong_uses_the_ten_newest_within_30_percent(self):
        deals = [deal(k=None, ym=f"2026{m:02d}", a=84.9 * 1.3, p=110000000) for m in range(1, 10)]
        deals += [deal(k=None, ym="202610", a=84.9 * 0.7, d=d, p=1000000 * d) for d in (1, 2)]
        deals += [deal(k=None, ym="202610", a=84.9 * 1.31, d=3)]                       # too large
        deals += [deal(k=None, ym="202610", u="1150010400", d=4)]                      # other dong
        result = compare_one(item(), deals)
        self.assertEqual(result["kind"], "sameDong")
        self.assertEqual((result["n"], len(result["deals"])), (10, 10))
        self.assertEqual(set(result), {"kind", "months", "n", "medianPerM2", "deals"})
        self.assertEqual(result["deals"][-1]["ym"], "2026-02")

    def test_land_needs_the_same_jimok(self):
        land = item(api="land", code="10103", landM2=100.0)        # 전
        deals = [deal(k=None, a=100.0, j="전"), deal(k=None, a=100.0, d=2, j="답")]
        for d in deals:
            del d["row"]["k"], d["row"]["f"]
        result = compare_one(land, deals, api="land")
        self.assertEqual((result["kind"], result["n"]), ("sameDong", 1))
        self.assertIsNone(result["deals"][0]["floor"])
        self.assertEqual(result["deals"][0]["m2"], 100.0)

    def test_unmapped_land_or_commercial_code_has_no_comparison(self):
        land = item(api="land", code="10129", landM2=100.0)        # 기타토지
        self.assertEqual(compare_one(land, [deal(k=None, a=100.0, j="전")], api="land"), {"kind": "none"})
        shop = item(api="nrg", code="10504")
        self.assertEqual(compare_one(shop, [deal(k=None, b="업무")], api="nrg"), {"kind": "none"})

    def test_commercial_needs_the_same_building_use(self):
        shop = item(api="nrg", code="10301")
        deals = [deal(k=None, b="제1종근린생활"), deal(k=None, d=2, b="제2종근린생활"), deal(k=None, d=3, b="업무")]
        for d in deals:
            del d["row"]["k"]
        self.assertEqual(compare_one(shop, deals, api="nrg")["n"], 2)

    def test_house_compares_floor_area(self):
        house = item(api="sh", code="10204", bldgM2=108.0)
        deals = [deal(k=None, a=108.37)]
        for d in deals:
            del d["row"]["k"], d["row"]["f"]
        self.assertEqual(compare_one(house, deals, api="sh")["kind"], "sameDong")

    def test_no_deal_no_area_no_api(self):
        self.assertEqual(compare_one(item(), []), {"kind": "none"})
        self.assertEqual(compare_one(item(bldgM2=None), [deal()] * 3), {"kind": "none"})
        self.assertEqual(compare.compare_item(item(api=None), {}, PARAMS), {"kind": "none"})
        self.assertEqual(compare_one(item(umd=None, pnu=None), [deal()]), {"kind": "none"})


class ParamsTest(unittest.TestCase):
    def test_starting_values_from_the_plan(self):
        self.assertEqual((PARAMS.months, PARAMS.building_tolerance, PARAMS.building_min), (12, 0.10, 3))
        self.assertEqual((PARAMS.dong_tolerance, PARAMS.dong_max), (0.30, 10))
        self.assertEqual(PARAMS.building_apis, frozenset({"apt", "rh", "offi"}))

    def test_every_land_and_commercial_code_is_known_or_explicitly_unmapped(self):
        import json
        import os
        from collector.config import DATA_DIR
        with open(os.path.join(DATA_DIR, "usage_groups.json"), encoding="utf-8") as handle:
            table = json.load(handle)["bySmallCode"]
        land = {code for code, v in table.items() if v.get("rtms") == "land"}
        self.assertEqual(land - set(PARAMS.land_jimok), {"10129", "31254", "31255"})
        uses = {code for code, v in table.items() if v.get("rtms") in ("nrg", "indu")}
        self.assertEqual(uses - set(PARAMS.building_use), {"10320", "10327", "10328", "10501", "10504"})


if __name__ == "__main__":
    unittest.main()
