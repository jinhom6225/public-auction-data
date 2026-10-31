"""Fake transports and Onbid page builders shared by the collector tests."""

from __future__ import annotations

import json
import urllib.parse
from typing import Callable

from collector.http_client import Response

FAKE_KEY = "FAKE+key/with=chars%41-not-real"


def page_body(rows: list[dict], total: int, page_no: int = 1, num_rows: int = 1000) -> bytes:
    """A successful getRlstCltrList2 JSON body."""
    body = {
        "header": {"resultCode": "00", "resultMsg": "NORMAL_CODE"},
        "body": {
            "items": {"item": rows},
            "numOfRows": num_rows,
            "pageNo": page_no,
            "totalCount": total,
        },
    }
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def page_no_of(url: str) -> int:
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    return int(query["pageNo"][0])


class RecordingTransport:
    """Answers with [handler(url, call_index)] and records every URL."""

    def __init__(self, handler: Callable[[str, int], Response]):
        self.handler = handler
        self.urls: list[str] = []

    def __call__(self, url: str, timeout: float, max_bytes: int) -> Response:
        self.urls.append(url)
        return self.handler(url, len(self.urls) - 1)


def ok(body: bytes) -> Response:
    return Response(status=200, body=body)


class FakeClock:
    """Monotonic clock whose sleep() just advances time."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def raw_row(key: str = "2025-09941-003", **overrides) -> dict:
    """One Onbid row (a single round) shaped like the real API."""
    row = {
        "onbidCltrno": 1573873, "cltrMngNo": key, "pbctCdtnNo": 3912726,
        "onbidPbancNo": 662306, "pbctNo": 9621163, "pbctNsq": "2", "pbctsn": "1",
        "prptDivCd": "0007", "prptDivNm": "압류재산", "dspsMthodCd": "0001",
        "dspsMthodNm": "매각", "cltrUsgLclsCtgrId": "10000",
        "cltrUsgMclsCtgrId": "10200", "cltrUsgSclsCtgrId": "10201",
        "cltrUsgLclsCtgrNm": "부동산", "cltrUsgMclsCtgrNm": "주거용건물",
        "cltrUsgSclsCtgrNm": "아파트",
        "onbidCltrNm": "서울특별시 종로구 청운동 12-3 청운아파트 101동 1401호",
        "usbdNft": 0, "pvctTrgtYn": "N",
        "cltrBidBgngDt": "202611021000", "cltrBidEndDt": "202611041700",
        "apslEvlAmt": 950000000, "lowstBidPrcIndctCont": "665000000",
        "ltnoPnu": "1111010100100120003", "lctnSdnm": "서울특별시",
        "lctnSggnm": "종로구", "lctnEmdNm": "청운동",
        "landSqms": 32.1, "bldSqms": 84.9, "alcYn": "N",
        "thnlImgUrlAdr": "https://www.onbid.co.kr/op/cm/syc/filemng/x.do?a=1",
        "pbctStatCd": "0001", "pbctStatNm": "입찰준비중",
    }
    return {**row, **overrides}


def item_record(key: str = "2025-09941-003", sgg: str = "11110", **overrides) -> dict:
    """A finished output record that passes every D13 rule."""
    record = {
        "key": key, "round": "2", "name": "서울 종로구 청운동 12-3",
        "pnu": "1111010100100120003", "sgg": sgg, "umd": None, "emdName": "청운동",
        "usage": {"l": "부동산", "m": "주거용건물", "s": "아파트", "group": "apt"},
        "assetType": "압류재산", "landM2": 32.1, "bldgM2": 84.9, "share": False,
        "appraisal": 950000000, "lowestBid": 665000000, "lowestBidText": None, "ratioToAppraisal": 70,
        "failCount": 3, "bidStart": "2026-11-23T10:00:00+09:00", "bidEnd": "2026-11-25T17:00:00+09:00",
        "thumb": "https://www.onbid.co.kr/x", "onbidUrl": "https://www.onbid.co.kr/y",
        "firstSeen": "2026-11-02", "priceDropAt": None, "prevLowestBid": None,
        "compare": {"kind": "none"}, "nextRounds": [],
    }
    return {**record, **overrides}


def rtms_item(**fields) -> dict:
    """One MOLIT <item> with the fields of every API (strings, like the XML)."""
    row = {
        "sggCd": "11500", "umdNm": "화곡동", "jibun": "645-32", "dealYear": "2026", "dealMonth": "9",
        "dealDay": "1", "dealAmount": "22,000", "cdealType": " ", "cdealDay": " ", "floor": "6",
        "excluUseAr": "28.02", "dealArea": "100", "totalFloorAr": "108.37", "plottageAr": "113",
        "buildingAr": "59.21", "buildingUse": "제2종근린생활", "jimok": "전", "shareDealingType": " ",
    }
    return {**row, **fields}


def rtms_body(rows: list[dict], total: int | None = None, page: int = 1) -> bytes:
    """A successful MOLIT XML page."""
    from xml.sax.saxutils import escape
    items = "".join("<item>" + "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in row.items()) + "</item>"
                    for row in rows)
    text = ('<?xml version="1.0" encoding="utf-8" standalone="yes"?><response><header><resultCode>000'
            '</resultCode><resultMsg>OK</resultMsg></header><body><items>' + items + '</items><numOfRows>1000'
            f'</numOfRows><pageNo>{page}</pageNo><totalCount>{len(rows) if total is None else total}'
            '</totalCount></body></response>')
    return text.encode("utf-8")


RTMS_PREFIX = "https://apis.data.go.kr/1613000/"


def is_rtms(url: str) -> bool:
    return url.startswith(RTMS_PREFIX)


def with_rtms(handler: Callable[[str, int], Response],
              rtms: Callable[[str, int], Response] | None = None) -> Callable[[str, int], Response]:
    """Routes MOLIT URLs to [rtms] (default: an empty month) and the rest to [handler]."""
    def route(url: str, i: int) -> Response:
        if is_rtms(url):
            return rtms(url, i) if rtms else ok(rtms_body([]))
        return handler(url, i)
    return route
