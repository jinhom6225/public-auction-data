"""Onbid real-estate list (15157207 getRlstCltrList2): every page or nothing.

D29 (1): a page that fails twice fails the whole run, because a missing
page would make its items look "gone". The page count comes from the first
page's totalCount; it must not change between pages and the rows collected
must add up to it.
"""

from __future__ import annotations

import json
import math
import re
import urllib.parse
from dataclasses import dataclass

from collector.budget import Budget
from collector.config import (
    ONBID_MAX_BYTES, ONBID_MAX_PAGES, ONBID_OPERATION, ONBID_PAGE_SIZE, ONBID_PARAMS, ONBID_URL,
)
from collector.errors import ApiError, BudgetExceeded
from collector.http_client import ApiClient, result_code_of

API = "onbid"
OK_CODE = "00"
RETRY_WAIT_SECONDS = 3.0
_ASCII_DIGITS = re.compile(r"[0-9]+")


@dataclass(frozen=True)
class Page:
    total: int
    num_rows: int
    rows: tuple[dict, ...]


def page_number_of(url: str) -> int:
    """pageNo of a list request URL (used by the offline fixture transport)."""
    values = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("pageNo", ["0"])
    return int(values[0]) if _ASCII_DIGITS.fullmatch(values[0]) else 0


def _fail(reason: str, code: str | None = None) -> ApiError:
    return ApiError(ONBID_OPERATION, status=200, result_code=code, reason=reason)


def _items_of(body: dict) -> tuple[dict, ...]:
    items = body.get("items")
    if items in (None, "", {}):
        return ()
    item = items.get("item") if isinstance(items, dict) else None
    if isinstance(item, dict):
        item = [item]
    if not isinstance(item, list) or not all(isinstance(row, dict) for row in item):
        raise _fail("unexpected items shape")
    return tuple(item)


def _count(body: dict, name: str) -> int:
    value = body.get(name)
    if isinstance(value, str) and _ASCII_DIGITS.fullmatch(value):
        value = int(value)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _fail(f"bad {name}")
    return value


def parse_page(raw: bytes) -> Page:
    """One JSON page; any non-success shape raises ApiError."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise _fail("body is not JSON") from None
    code = result_code_of(raw)
    if not isinstance(data, dict) or code != OK_CODE or not isinstance(data.get("body"), dict):
        raise _fail("unexpected result", code)
    body = data["body"]
    return Page(total=_count(body, "totalCount"), num_rows=_count(body, "numOfRows"), rows=_items_of(body))


def _fetch_page(client: ApiClient, budget: Budget, page_no: int, page_size: int) -> tuple[Page, Budget]:
    params = {**ONBID_PARAMS, "pageNo": str(page_no), "numOfRows": str(page_size)}
    last_error: ApiError | None = None
    for attempt in range(2):
        if attempt:
            client.pause(RETRY_WAIT_SECONDS)
        budget = budget.spend(API)
        try:
            return parse_page(client.get(ONBID_OPERATION, ONBID_URL, params, ONBID_MAX_BYTES)), budget
        except ApiError as error:
            last_error = error
    assert last_error is not None
    raise last_error


def fetch_all(client: ApiClient, budget: Budget, page_size: int = ONBID_PAGE_SIZE,
              max_pages: int = ONBID_MAX_PAGES) -> tuple[list[dict], Budget]:
    """All rows of the list, and the budget after the calls."""
    first, budget = _fetch_page(client, budget, 1, page_size)
    per_page = first.num_rows or page_size
    pages = max(1, math.ceil(first.total / per_page))
    if pages > max_pages:
        raise _fail("too many pages")
    if not budget.can_spend(API, pages - 1):
        raise BudgetExceeded(API)
    rows = list(first.rows)
    for page_no in range(2, pages + 1):
        page, budget = _fetch_page(client, budget, page_no, page_size)
        if page.total != first.total:
            raise _fail("totalCount changed")
        rows.extend(page.rows)
    if len(rows) != first.total:
        raise _fail("row count mismatch")
    if len({(row.get("cltrMngNo"), row.get("pbctCdtnNo")) for row in rows}) != first.total:
        raise _fail("duplicate rows across pages")
    return rows, budget
