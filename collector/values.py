"""Field conversions for one Onbid row (plan A7, D13, D32/A5)."""

from __future__ import annotations

import datetime
import re
import urllib.parse
from typing import Mapping

from collector.config import KST, MAX_URL_LENGTH, ONBID_HOST, ONBID_ITEM_URL

MAX_AMOUNT = 10**13
MAX_AREA = 10**7
MAX_RATIO = 100_000
LOWEST_BID_TEXT_LIMIT = 30
PLACEHOLDER_YEAR = 2900
_DIGITS = re.compile(r"[0-9]+")
_BID_TIME = re.compile(r"([0-9]{4})([0-9]{2})([0-9]{2})([0-9]{2})([0-9]{2})")
_LINK_FIELDS = ("onbidCltrno", "onbidPbancNo", "pbctNo", "pbctCdtnNo")


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def amount(value: object) -> int | None:
    """Won amount: integer, 0 < x <= 1e13, else None."""
    if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= MAX_AMOUNT:
        return value
    return None


def lowest_bid(text: object) -> tuple[int | None, str | None]:
    """(lowestBid, lowestBidText): digits only -> number, other text kept."""
    if not isinstance(text, str) or not text.strip():
        return None, None
    cleaned = text.strip()
    if _DIGITS.fullmatch(cleaned):
        return amount(int(cleaned)), None
    return None, cleaned[:LOWEST_BID_TEXT_LIMIT]


def ratio(lowest: int | None, appraisal: int | None) -> int | None:
    """round(lowest / appraisal * 100), half up, in integers."""
    if lowest is None or appraisal is None:
        return None
    value = (lowest * 200 + appraisal) // (appraisal * 2)
    return value if value <= MAX_RATIO else None


def area(value: object) -> int | float | None:
    if _is_number(value) and 0 < value <= MAX_AREA:
        return value
    return None


def bid_datetime(text: object) -> datetime.datetime | None:
    """KST datetime of `yyyyMMddHHmm`; the 2999 placeholder gives None."""
    match = _BID_TIME.fullmatch(text) if isinstance(text, str) else None
    if not match:
        return None
    year, month, day, hour, minute = (int(part) for part in match.groups())
    if year >= PLACEHOLDER_YEAR:
        return None
    try:
        return datetime.datetime(year, month, day, hour, minute, tzinfo=KST)
    except ValueError:
        return None


def bid_time(text: object) -> str | None:
    moment = bid_datetime(text)
    return moment.isoformat() if moment else None


def is_placeholder(text: object) -> bool:
    """The 2999 "schedule not fixed" value Onbid uses (A7 ③)."""
    match = _BID_TIME.fullmatch(text) if isinstance(text, str) else None
    return bool(match) and int(match.group(1)) >= PLACEHOLDER_YEAR


def bid_pair(start: object, end: object) -> tuple[str | None, str | None]:
    """(bidStart, bidEnd); a 2999 placeholder on either side nulls both."""
    if is_placeholder(start) or is_placeholder(end):
        return None, None
    return bid_time(start), bid_time(end)


def onbid_https(url: object) -> str | None:
    """The URL if it is https on the Onbid host, else None (field only)."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH:
        return None
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.netloc != ONBID_HOST:
        return None
    return url


def item_link(row: Mapping) -> str | None:
    """A5 deep link from the representative round, or None."""
    numbers = [row.get(name) for name in _LINK_FIELDS]
    if not all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in numbers):
        return None
    return ONBID_ITEM_URL + "?" + urllib.parse.urlencode(dict(zip(_LINK_FIELDS, numbers)))
