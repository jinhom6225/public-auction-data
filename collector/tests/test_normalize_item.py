import datetime
import json
import unittest

import netguard  # noqa: F401  (blocks the network)
from fakes import raw_row

from collector import normalize_item
from collector.config import KST
from collector.normalize_item import Context

NOW = datetime.datetime(2026, 11, 1, 5, 0, tzinfo=KST)
CTX = Context.load(NOW)


def one(**overrides):
    result = normalize_item.normalize_all([raw_row(**overrides)], CTX)
    return result.items[0].record if result.items else None, result


class RecordShapeTest(unittest.TestCase):
    def test_full_record(self):
        record, _ = one()
        self.assertEqual(record, {
            "key": "2025-09941-003", "round": "2",
            "name": "서울특별시 종로구 청운동 12-3 청운아파트 101동 1401호",
            "pnu": "1111010100100120003", "sgg": "11110", "umd": "1111010100", "emdName": "청운동",
            "usage": {"l": "부동산", "m": "주거용건물", "s": "아파트", "group": "apt"},
            "assetType": "압류재산", "landM2": 32.1, "bldgM2": 84.9, "share": False,
            "appraisal": 950000000, "lowestBid": 665000000, "lowestBidText": None, "ratioToAppraisal": 70,
            "failCount": 0, "bidStart": "2026-11-02T10:00:00+09:00", "bidEnd": "2026-11-04T17:00:00+09:00",
            "thumb": "https://www.onbid.co.kr/op/cm/syc/filemng/x.do?a=1",
            "onbidUrl": "https://www.onbid.co.kr/op/cltrpbancinf/cltrdtl/CltrDtlController/mvmnCltrDtl.do"
                        "?onbidCltrno=1573873&onbidPbancNo=662306&pbctNo=9621163&pbctCdtnNo=3912726",
            "nextRounds": [],
        })

    def test_private_price_share_and_zero_area(self):
        record, _ = one(lowstBidPrcIndctCont="비공개", alcYn="Y", bldSqms=0, apslEvlAmt=None)
        self.assertEqual((record["lowestBid"], record["lowestBidText"]), (None, "비공개"))
        self.assertTrue(record["share"])
        self.assertIsNone(record["bldgM2"])
        self.assertIsNone(record["appraisal"])
        self.assertIsNone(record["ratioToAppraisal"])

    def test_placeholder_schedule(self):
        record, _ = one(cltrBidBgngDt="299912301000", cltrBidEndDt="299912301600")
        self.assertIsNone(record["bidStart"])
        self.assertIsNone(record["bidEnd"])

    def test_one_sided_placeholder_schedule_keeps_the_item(self):
        record, result = one(cltrBidBgngDt="202611021000", cltrBidEndDt="299912301600")
        self.assertEqual((record["bidStart"], record["bidEnd"]), (None, None))
        self.assertEqual(dict(result.dropped), {})

    def test_http_thumbnail_is_dropped_field_only(self):
        record, _ = one(thnlImgUrlAdr="http://www.onbid.co.kr/x.jpg")
        self.assertIsNone(record["thumb"])
        self.assertEqual(record["key"], "2025-09941-003")

    def test_name_whitespace_is_collapsed(self):
        record, _ = one(onbidCltrNm="  서울특별시   종로구 청운동 12-3  ")
        self.assertEqual(record["name"], "서울특별시 종로구 청운동 12-3")

    def test_old_code_pnu_becomes_current(self):
        record, _ = one(lctnSdnm="전남광주통합특별시", lctnSggnm="동구", lctnEmdNm="대인동",
                        ltnoPnu="2911010100100010000", onbidCltrNm="전남광주통합특별시 동구 대인동 1")
        self.assertEqual((record["sgg"], record["umd"], record["pnu"]), ("12210", "1221010100", "1221010100100010000"))

    def test_pnu_from_name_when_missing(self):
        record, _ = one(ltnoPnu=None, onbidCltrNm="서울특별시 종로구 청운동 산 7-1 임야")
        self.assertEqual(record["pnu"], "1111010100100070001")

    def test_unknown_usage_is_other_and_counted(self):
        record, result = one(cltrUsgMclsCtgrId="10900", cltrUsgSclsCtgrId="19999", cltrUsgSclsCtgrNm="신규")
        self.assertEqual(record["usage"]["group"], "other")
        self.assertEqual(dict(result.unknown_usage), {"19999": 1})

    def test_output_is_json_serialisable(self):
        record, _ = one()
        json.dumps(record)


class DropTest(unittest.TestCase):
    def test_unresolvable_location_is_dropped(self):
        record, result = one(lctnSdnm=None, lctnSggnm=None, lctnEmdNm=None, ltnoPnu=None, onbidCltrNm="물건")
        self.assertIsNone(record)
        self.assertEqual(dict(result.dropped), {"no location": 1})

    def test_bad_key_is_dropped(self):
        record, result = one(key="bad key!")
        self.assertIsNone(record)
        self.assertEqual(dict(result.dropped), {"invalid key": 1})

    def test_closed_only_item_is_left_out_but_not_a_drop(self):
        record, result = one(pbctStatCd="0003")
        self.assertIsNone(record)
        self.assertEqual(result.closed, 1)
        self.assertEqual(dict(result.dropped), {})

    def test_keyless_rows_count_once_per_item(self):
        # L7: rows without cltrMngNo are grouped by onbidCltrno before counting.
        rows = [raw_row(key=None, onbidCltrno=1), raw_row(key=None, onbidCltrno=1),
                raw_row(key="", onbidCltrno=2), raw_row(key="C")]
        result = normalize_item.normalize_all(rows, CTX)
        self.assertEqual(dict(result.dropped), {"no key": 2})
        self.assertEqual(result.candidates, 3)

    def test_lease_and_negotiated_rows_are_filtered(self):
        rows = [raw_row(key="A", dspsMthodCd="0002"), raw_row(key="B", pvctTrgtYn="Y"), raw_row(key="C")]
        result = normalize_item.normalize_all(rows, CTX)
        self.assertEqual([i.record["key"] for i in result.items], ["C"])
        self.assertEqual(result.filtered_rows, 2)


class RoundsTest(unittest.TestCase):
    def test_many_rows_become_one_item(self):
        rows = [
            raw_row(pbctCdtnNo=2, cltrBidBgngDt="202611091000", cltrBidEndDt="202611111700",
                    lowstBidPrcIndctCont="600000000", usbdNft=1, pbctNsq="3"),
            raw_row(pbctCdtnNo=1, cltrBidBgngDt="202611021000", cltrBidEndDt="202611041700", pbctStatCd="0002"),
            raw_row(pbctCdtnNo=0, cltrBidBgngDt="202610261000", cltrBidEndDt="202610281700", pbctStatCd="0003"),
        ]
        result = normalize_item.normalize_all(rows, CTX)
        self.assertEqual(len(result.items), 1)
        record = result.items[0].record
        self.assertEqual(record["lowestBid"], 665000000)
        self.assertEqual(record["onbidUrl"][-len("pbctCdtnNo=1"):], "pbctCdtnNo=1")
        self.assertEqual(record["nextRounds"], [{"bidStart": "2026-11-09T10:00:00+09:00",
                                                 "bidEnd": "2026-11-11T17:00:00+09:00",
                                                 "lowestBid": 600000000}])

    def test_phase_3_metadata_is_kept_outside_the_record(self):
        item = normalize_item.normalize_all([raw_row()], CTX).items[0]
        self.assertEqual((item.sido, item.usage_code, item.rtms), ("11", "10201", "apt"))
        self.assertNotIn("rtms", item.record)


if __name__ == "__main__":
    unittest.main()


class ContactInNameDropTest(unittest.TestCase):
    """Security review M1: counted under its own drop reason."""

    def test_item_with_a_phone_number_in_its_name_is_dropped(self):
        record, result = one(onbidCltrNm="서울특별시 종로구 청운동 12-3 청운아파트 문의 010-1234-5678")
        self.assertIsNone(record)
        self.assertEqual(result.dropped, {"contact in name": 1})
