"""Hand-made boundary fixture: two days of Onbid pages (review M6).

Dev tool. Writes `<out>/day1/onbid_p1.json` and `<out>/day2/onbid_p1.json`
(서울 종로구 only, rows shaped like the real API). Run the collector on
day 1 with `--first-run` and on day 2 with `--prev <day-1 output>` to get
the golden copies; the app's `items_sample/boundary_day2/` is day 2.

    python3 -I collector/dev/make_boundary_fixture.py collector/tests/fixtures/boundary

Cases: name of 601 / 602 / 603 characters (603 is dropped by D13),
failCount 108, both dates 2999 ("일정 미정"), only the end 2999 (both
null, A7 ③), private price, share, five scheduled rounds (nextRounds 3),
a price drop on day 2 (the open round ended, the next round is cheaper),
a price rise, an item gone on day 2 and an item new on day 2.
"""

from __future__ import annotations

import json
import os
import sys

PREFIX = "서울특별시 종로구 청운동 1 "
BASE = {
    "onbidPbancNo": 700000, "pbctNsq": "1", "pbctsn": "1", "prptDivCd": "0007", "prptDivNm": "압류재산",
    "dspsMthodCd": "0001", "dspsMthodNm": "매각", "cltrUsgLclsCtgrId": "10000", "cltrUsgMclsCtgrId": "10200",
    "cltrUsgSclsCtgrId": "10201", "cltrUsgLclsCtgrNm": "부동산", "cltrUsgMclsCtgrNm": "주거용건물",
    "cltrUsgSclsCtgrNm": "아파트", "usbdNft": 0, "pvctTrgtYn": "N", "apslEvlAmt": 2000000,
    "lowstBidPrcIndctCont": "1400000", "ltnoPnu": "1111010100100010000", "lctnSdnm": "서울특별시",
    "lctnSggnm": "종로구", "lctnEmdNm": "청운동", "landSqms": 10.5, "bldSqms": 59.94, "alcYn": "N",
    "thnlImgUrlAdr": "https://www.onbid.co.kr/op/cm/syc/filemng/filemngprcs/FileMngPrcsController/dnldFile.do?atchFileLstNo=1",
    "pbctStatCd": "0001", "pbctStatNm": "입찰준비중",
}
WEEK = ("202611091000", "202611111700")


def _name(length: int) -> str:
    return PREFIX + "가" * (length - len(PREFIX))


def _row(number: int, key: str, start: str = WEEK[0], end: str = WEEK[1], **fields) -> dict:
    return {**BASE, "onbidCltrno": 900000 + number // 10, "cltrMngNo": key, "pbctCdtnNo": 800000 + number,
            "pbctNo": 600000 + number, "onbidCltrNm": PREFIX + key, "cltrBidBgngDt": start,
            "cltrBidEndDt": end, **fields}


def _common() -> list[dict]:
    return [
        _row(10, "B-NAME-601", onbidCltrNm=_name(601)),
        _row(20, "B-NAME-602", onbidCltrNm=_name(602)),
        _row(30, "B-NAME-603", onbidCltrNm=_name(603)),
        _row(40, "B-FAIL-108", usbdNft=108),
        _row(50, "B-TBD", start="299912301000", end="299912301600"),
        _row(60, "B-ONESIDE", end="299912301600"),
        _row(70, "B-PRIVATE", lowstBidPrcIndctCont="비공개"),
        _row(80, "B-SHARE", alcYn="Y", bldSqms=29.97, onbidCltrNm=PREFIX + "B-SHARE 1/2 지분"),
        *[_row(90 + i, "B-MULTI", start=f"2026111{i}1000", end=f"2026111{i}1700",
               lowstBidPrcIndctCont=str(1400000 - i * 140000), usbdNft=i) for i in range(5)],
        # Open round ends 2026-11-01 17:00 KST: after day 1, before day 2.
        _row(100, "B-DROP", start="202610291000", end="202611011700", pbctStatCd="0002",
             pbctStatNm="입찰진행중", lowstBidPrcIndctCont="1000000"),
        _row(101, "B-DROP", lowstBidPrcIndctCont="900000", usbdNft=1),
    ]


def day1() -> list[dict]:
    return [*_common(), _row(110, "B-RISE", lowstBidPrcIndctCont="500000"), _row(120, "B-GONE")]


def day2() -> list[dict]:
    return [*_common(), _row(110, "B-RISE", lowstBidPrcIndctCont="600000"), _row(130, "B-NEW")]


def _write(folder: str, rows: list[dict]) -> None:
    os.makedirs(folder, exist_ok=True)
    page = {"header": {"resultCode": "00", "resultMsg": "NORMAL_CODE"},
            "body": {"items": {"item": rows}, "numOfRows": 1000, "pageNo": 1, "totalCount": len(rows)}}
    with open(os.path.join(folder, "onbid_p1.json"), "w", encoding="utf-8") as handle:
        json.dump(page, handle, ensure_ascii=False, indent=1)
        handle.write("\n")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 64
    _write(os.path.join(argv[1], "day1"), day1())
    _write(os.path.join(argv[1], "day2"), day2())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
