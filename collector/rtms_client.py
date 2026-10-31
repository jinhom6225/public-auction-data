"""MOLIT real-transaction APIs (research §3.1, plan Phase 3, D17, D27, D29 ②).

Seven endpoints, called only with current, selectable LAWD_CD codes (old
codes answer `000` with zero rows, A3/A4) and one contract month. Bodies
must be strict UTF-8 (no BOM, no other `encoding=` declaration) and carry
no DOCTYPE/ENTITY in the decoded text before ElementTree sees them.

`fetch_month` pages through a month (1000 rows a page, totalCount stable,
rows must add up), retrying a failed page once. It never raises for API
trouble: the result says ok / failed / capped (our daily cap, D17) /
quota (gateway code 22, the provider's own daily limit), so the caller can
keep yesterday's cache for that month (D29 ②).

`summarize` keeps only what the comparison needs (research §3.2), drops
cancelled (`cdealType`) and share (`shareDealingType=지분`) deals, turns
만원 into 원 and resolves the dong (and building key for apt/rh/offi).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from collector import pnu
from collector.budget import Budget
from collector.config import (
    RTMS_APIS, RTMS_MAX_BYTES, RTMS_MAX_PAGES, RTMS_PAGE_SIZE, RTMS_RETRY_WAIT_SECONDS,
)
from collector.errors import ApiError
from collector.http_client import ApiClient
from collector.region_index import RegionIndex
from collector.values import MAX_AMOUNT, MAX_AREA


@dataclass(frozen=True)
class Endpoint:
    operation: str
    url: str


_BASE = "https://apis.data.go.kr/1613000/"
ENDPOINTS: Mapping[str, Endpoint] = MappingProxyType({
    "apt": Endpoint("getRTMSDataSvcAptTradeDev", _BASE + "RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"),
    "rh": Endpoint("getRTMSDataSvcRHTrade2", _BASE + "RTMSDataSvcRHTrade2/getRTMSDataSvcRHTrade2"),
    "offi": Endpoint("getRTMSDataSvcOffiTrade", _BASE + "RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade"),
    "land": Endpoint("getRTMSDataSvcLandTrade", _BASE + "RTMSDataSvcLandTrade/getRTMSDataSvcLandTrade"),
    "sh": Endpoint("getRTMSDataSvcSHTrade", _BASE + "RTMSDataSvcSHTrade/getRTMSDataSvcSHTrade"),
    "nrg": Endpoint("getRTMSDataSvcNrgTrade", _BASE + "RTMSDataSvcNrgTrade/getRTMSDataSvcNrgTrade"),
    "indu": Endpoint("getRTMSDataSvcInduTrade", _BASE + "RTMSDataSvcInduTrade/getRTMSDataSvcInduTrade"),
})
assert tuple(ENDPOINTS) == RTMS_APIS

OK_CODE = "000"
QUOTA_CODES = frozenset({"22"})          # provider's daily limit (expected per data.go.kr, not yet observed)
QUOTA_STATUS = 429
AUTH_CODES = frozenset({"30", "31", "32"})   # unregistered / expired / unapproved key: no retry today
MAX_ITEM_FIELDS = 60
MAX_MONTH_ROWS = RTMS_PAGE_SIZE * RTMS_MAX_PAGES
BUILDING_APIS = frozenset({"apt", "rh", "offi"})

# MOLIT area field per API (research §3.4: 전용면적 for apt/rh/offi).
AREA_FIELD: Mapping[str, str] = MappingProxyType({
    "apt": "excluUseAr", "rh": "excluUseAr", "offi": "excluUseAr", "land": "dealArea",
    "sh": "totalFloorAr", "nrg": "buildingAr", "indu": "buildingAr",
})
# Summary row fields: d day, p amount (원), a area (㎡), f floor, u dong
# (읍면동 level, for same-dong), k building key, j 지목, b building use.
ROW_FIELDS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "apt": ("d", "p", "a", "f", "u", "k"), "rh": ("d", "p", "a", "f", "u", "k"),
    "offi": ("d", "p", "a", "f", "u", "k"), "land": ("d", "p", "a", "u", "j"),
    "sh": ("d", "p", "a", "u"), "nrg": ("d", "p", "a", "f", "u", "b"), "indu": ("d", "p", "a", "f", "u", "b"),
})
TEXT_LIMIT = 30

_DECLARATION = re.compile(r"<\?xml\b[^>]*\?>")
_ENCODING = re.compile(r"""encoding\s*=\s*["']([^"']*)["']""")
_DTD = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")   # not allowed in XML 1.0
_CODE = re.compile(r"[0-9A-Za-z_]{1,10}")
_COUNT = re.compile(r"[0-9]{1,9}")
_MAN_WON = re.compile(r"[0-9]{1,3}(?:,[0-9]{3})*|[0-9]+")
_AREA = re.compile(r"[0-9]{1,8}(?:\.[0-9]{1,6})?")
_INT = re.compile(r"-?[0-9]{1,3}")
_LAWD = re.compile(r"[0-9]{5}")
_YM = re.compile(r"[0-9]{4}(0[1-9]|1[0-2])")


@dataclass(frozen=True)
class Page:
    total: int
    rows: tuple[Mapping[str, str], ...]


@dataclass(frozen=True)
class MonthResult:
    status: str                       # ok | failed | capped | quota | auth
    rows: tuple[Mapping[str, str], ...]
    budget: Budget
    error: str | None = None


# XML ---------------------------------------------------------------------

def _refuse(operation: str, reason: str, code: str | None = None) -> ApiError:
    return ApiError(operation, status=200, result_code=code, reason=reason)


def _decoded(body: bytes, operation: str) -> str:
    if body.startswith(b"\xef\xbb\xbf"):
        raise _refuse(operation, "BOM refused")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise _refuse(operation, "body is not UTF-8") from None
    if not text.startswith("<"):
        raise _refuse(operation, "body does not start with a tag")
    if _ILLEGAL.search(text):
        raise _refuse(operation, "illegal control character")
    declaration = _DECLARATION.match(text)
    if declaration:
        encoding = _ENCODING.search(declaration.group())
        if encoding and encoding.group(1).lower() not in ("utf-8", "utf8"):
            raise _refuse(operation, "declared encoding is not UTF-8")
        text = text[declaration.end():]
    if _DTD.search(text):
        raise _refuse(operation, "DOCTYPE or ENTITY")
    return text


def _code(text: str | None) -> str | None:
    value = (text or "").strip()
    return value if _CODE.fullmatch(value) else None


def _rows(root: ET.Element) -> tuple[Mapping[str, str], ...]:
    items = root.find("body/items")
    if items is None:
        return ()
    if any(len(item) > MAX_ITEM_FIELDS for item in items.iter("item")):
        raise _refuse("rtms", "item has too many fields")
    return tuple(MappingProxyType({child.tag: (child.text or "").strip() for child in item})
                 for item in items.findall("item"))


def parse_page(body: bytes, operation: str) -> Page:
    """One MOLIT page; ApiError for anything but a well-formed `000` page."""
    try:
        root = ET.fromstring(_decoded(body, operation))
    except ET.ParseError:
        raise _refuse(operation, "XML does not parse") from None
    if root.tag == "OpenAPI_ServiceResponse":
        raise _refuse(operation, "gateway error", _code(root.findtext("cmmMsgHeader/returnReasonCode")))
    if root.tag != "response":
        raise _refuse(operation, "unexpected XML root")
    code = (root.findtext("header/resultCode") or "").strip()
    if code != OK_CODE:
        raise _refuse(operation, "result code is not OK", _code(code))
    total = (root.findtext("body/totalCount") or "").strip()
    if not _COUNT.fullmatch(total):
        raise _refuse(operation, "no totalCount")
    return Page(int(total), _rows(root))


# Summary rows ------------------------------------------------------------

def won(text: object) -> int | None:
    """"65,300" (만원) -> 653,000,000 (원); ASCII digits only."""
    if not isinstance(text, str) or not _MAN_WON.fullmatch(text.strip()):
        return None
    value = int(text.strip().replace(",", "")) * 10_000
    return value if 0 < value <= MAX_AMOUNT else None


def _area(text: object) -> float | None:
    if not isinstance(text, str) or not _AREA.fullmatch(text.strip()):
        return None
    value = float(text.strip())
    return value if 0 < value <= MAX_AREA else None


def _small_int(text: object) -> int | None:
    return int(text.strip()) if isinstance(text, str) and _INT.fullmatch(text.strip()) else None


def _text(value: object) -> str | None:
    return value.strip()[:TEXT_LIMIT] if isinstance(value, str) and value.strip() else None


def _by_name(lawd: str, name: str, index: RegionIndex) -> tuple[str | None, str | None]:
    words = name.split()
    emd = index.emd_code(lawd, words[0]) if words else None
    if emd is None or len(words) > 2:
        return None, None
    if len(words) == 1:
        return emd, emd
    return index.ri_code(emd, words[1]), emd


def dong_codes(api: str, lawd: str, row: Mapping[str, str], index: RegionIndex) -> tuple[str | None, str | None]:
    """(parcel dong incl. 리, 읍면동 code). Always the queried LAWD_CD (D8)."""
    if api == "apt":
        code = lawd + row.get("umdCd", "")
        if re.fullmatch(r"[0-9]{10}", code) and (code in index.emds or code in index.ris):
            return code, code[:8] + "00"
    return _by_name(lawd, row.get("umdNm", ""), index)


def _lot(api: str, row: Mapping[str, str]) -> pnu.Lot | None:
    return pnu.apt_lot(row) if api == "apt" else pnu.jibun_lot(row.get("jibun"))


def summarize(api: str, lawd: str, row: Mapping[str, str], index: RegionIndex) -> dict | None:
    """Summary row, or None for a cancelled, share or unusable deal."""
    if row.get("cdealType", "").strip() or row.get("shareDealingType", "").strip() == "지분":
        return None
    amount, area, day = won(row.get("dealAmount")), _area(row.get(AREA_FIELD[api])), _small_int(row.get("dealDay"))
    if amount is None or area is None or day is None or not 1 <= day <= 31:
        return None
    parcel_dong, emd = dong_codes(api, lawd, row, index)
    extra = {
        "f": _small_int(row.get("floor")), "u": emd,
        "k": pnu.building_key(parcel_dong, _lot(api, row)),
        "j": _text(row.get("jimok")), "b": _text(row.get("buildingUse")),
    }
    summary = {"d": day, "p": amount, "a": area, **extra}
    return {field: summary[field] for field in ROW_FIELDS[api]}


# Calls -------------------------------------------------------------------

def _check_request(api: str, lawd: str, ym: str, index: RegionIndex) -> None:
    if api not in ENDPOINTS:
        raise ValueError("unknown real-transaction API")
    if not _LAWD.fullmatch(lawd) or not index.is_selectable(lawd):
        raise ValueError("LAWD_CD must be a current, selectable code (old codes answer zero rows)")
    if not _YM.fullmatch(ym):
        raise ValueError("DEAL_YMD must be yyyymm")


def _call_page(client: ApiClient, api: str, lawd: str, ym: str, page: int) -> Page:
    endpoint = ENDPOINTS[api]
    params = {"LAWD_CD": lawd, "DEAL_YMD": ym, "numOfRows": str(RTMS_PAGE_SIZE), "pageNo": str(page)}
    body = client.get(endpoint.operation, endpoint.url, params, RTMS_MAX_BYTES)
    return parse_page(body, endpoint.operation)


def _page_with_retry(client: ApiClient, api: str, lawd: str, ym: str, page: int,
                     budget: Budget) -> tuple[Page | MonthResult, Budget]:
    error: ApiError | None = None
    for attempt in range(2):
        if not budget.can_spend(api, 1):
            return MonthResult("capped", (), budget), budget
        if attempt:
            client.pause(RTMS_RETRY_WAIT_SECONDS)
        budget = budget.spend(api)
        try:
            return _call_page(client, api, lawd, ym, page), budget
        except ApiError as failure:
            if failure.result_code in QUOTA_CODES or failure.status == QUOTA_STATUS:
                return MonthResult("quota", (), budget, str(failure)), budget
            if failure.result_code in AUTH_CODES:
                return MonthResult("auth", (), budget, str(failure)), budget
            error = failure
    return MonthResult("failed", (), budget, str(error)), budget


def _page_problem(page: Page, total: int | None) -> str | None:
    if len(page.rows) > RTMS_PAGE_SIZE:
        return "page has more rows than numOfRows"
    if total is None and page.total > MAX_MONTH_ROWS:
        return "totalCount above the page limit"
    if total is not None and page.total != total:
        return "totalCount changed between pages"
    return None


def fetch_month(client: ApiClient, api: str, lawd: str, ym: str, budget: Budget,
                index: RegionIndex) -> MonthResult:
    """Every raw row of one sgg-month, or a non-ok status (nothing partial)."""
    _check_request(api, lawd, ym, index)
    operation = ENDPOINTS[api].operation
    rows: list[Mapping[str, str]] = []
    total: int | None = None
    for page_no in range(1, RTMS_MAX_PAGES + 1):
        page, budget = _page_with_retry(client, api, lawd, ym, page_no, budget)
        if isinstance(page, MonthResult):
            return page
        problem = _page_problem(page, total)
        if problem:
            return MonthResult("failed", (), budget, str(_refuse(operation, problem)))
        total = page.total
        rows.extend(page.rows)
        if len(rows) >= total or len(page.rows) < RTMS_PAGE_SIZE:
            break
    if len(rows) != total:
        return MonthResult("failed", (), budget, str(_refuse(operation, "row count differs from totalCount")))
    return MonthResult("ok", tuple(rows), budget)


def empty_body() -> bytes:
    """A valid zero-row page; the offline fixture transport serves it for months without a file."""
    return ('<?xml version="1.0" encoding="utf-8" standalone="yes"?><response><header><resultCode>000'
            '</resultCode><resultMsg>OK</resultMsg></header><body><items></items><numOfRows>1000</numOfRows>'
            '<pageNo>1</pageNo><totalCount>0</totalCount></body></response>').encode("utf-8")
