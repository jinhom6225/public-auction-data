"""Record and document rules the app also applies (plan D11, D13, research §3.5).

`record_problems` is the per-item rule set (an item with problems is
dropped); the document helpers are used by `validate_output`, which checks
a whole `v1/` tree.
"""

from __future__ import annotations

import datetime
import re
import urllib.parse
from typing import Mapping

from collector.config import MAX_URL_LENGTH, ONBID_HOST
from collector.region_index import RegionIndex
from collector.usage import GROUPS
from collector.values import MAX_AMOUNT, MAX_AREA, MAX_RATIO

# D13 text limits. `name` uses the D13 formula max(measured max x 2,
# default 120) with the Phase 1 dump maximum of 301 characters, so no real
# item is dropped for length. Phase 4 mirrors these numbers in the app.
TEXT_LIMITS = {"name": 602, "emdName": 20, "assetType": 20, "lowestBidText": 30, "round": 10}
USAGE_TEXT_LIMIT = 30
MAX_FAIL_COUNT = 999   # D13 said 99; one real item has 108 failed rounds.
MAX_DEALS = 10
MIN_SAME_BUILDING = 3   # compare_params.json sameBuilding.minDeals (a test keeps them equal)
MAX_NEXT_ROUNDS = 3
COMPARE_KINDS = ("sameBuilding", "sameDong", "none")
COMPARE_FIELDS = {
    "none": ("kind",),
    "sameDong": ("kind", "months", "n", "medianPerM2", "deals"),
    "sameBuilding": ("kind", "months", "n", "medianPerM2", "estimate", "ratio", "deals"),
}
DEAL_FIELDS = ("ym", "m2", "floor", "amount")
MAX_MONTHS = 24
MAX_FLOOR = 999
YEAR_MONTH = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])")
FIELDS = (
    "key", "round", "name", "pnu", "sgg", "umd", "emdName", "usage", "assetType", "landM2", "bldgM2",
    "share", "appraisal", "lowestBid", "lowestBidText", "ratioToAppraisal", "failCount", "bidStart",
    "bidEnd", "thumb", "onbidUrl", "firstSeen", "priceDropAt", "prevLowestBid", "compare", "nextRounds",
)
KEY = re.compile(r"[A-Za-z0-9_\-]{1,40}")
KST_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\+09:00")
UTC_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
DIGITS = {"pnu": re.compile(r"[0-9]{19}"), "umd": re.compile(r"[0-9]{10}")}


def _int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _num(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _parses(value: str, fmt: re.Pattern[str], parser) -> bool:
    if not fmt.fullmatch(value):
        return False
    try:
        parser(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _time_ok(value: object) -> bool:
    return value is None or (isinstance(value, str) and _parses(value, KST_TIME, datetime.datetime.fromisoformat))


def _date_ok(value: object, required: bool = False) -> bool:
    if value is None:
        return not required
    return isinstance(value, str) and _parses(value, DATE, datetime.date.fromisoformat)


def _text_ok(value: object, limit: int, required: bool = False) -> bool:
    if value is None:
        return not required
    return isinstance(value, str) and 0 < len(value) <= limit


def _https(value: object) -> bool:
    """https on the Onbid host only (S-L4); the app allows the same host."""
    if value is None:
        return True
    if not isinstance(value, str) or len(value) > MAX_URL_LENGTH:
        return False
    parts = urllib.parse.urlsplit(value)
    return parts.scheme == "https" and parts.netloc == ONBID_HOST


def _optional(value: object, check) -> bool:
    return value is None or check(value)


def _schedule_problems(start: object, end: object) -> list[str]:
    problems = [f"invalid {name}" for name, value in (("bidStart", start), ("bidEnd", end)) if not _time_ok(value)]
    if problems:
        return problems
    if (start is None) != (end is None):
        return ["bidStart and bidEnd must both be set or both null"]
    if start is not None and datetime.datetime.fromisoformat(start) > datetime.datetime.fromisoformat(end):
        return ["bidStart after bidEnd"]
    return []


def _usage_ok(usage: object) -> bool:
    if not isinstance(usage, dict) or set(usage) != {"l", "m", "s", "group"}:
        return False
    return usage["group"] in GROUPS and all(_text_ok(usage[k], USAGE_TEXT_LIMIT) for k in ("l", "m", "s"))


def _deal_ok(deal: object) -> bool:
    if not isinstance(deal, dict) or set(deal) != set(DEAL_FIELDS):
        return False
    return (isinstance(deal["ym"], str) and bool(YEAR_MONTH.fullmatch(deal["ym"]))
            and _num(deal["m2"]) and 0 < deal["m2"] <= MAX_AREA
            and _optional(deal["floor"], lambda v: _int(v) and -MAX_FLOOR <= v <= MAX_FLOOR)
            and _int(deal["amount"]) and 0 < deal["amount"] <= MAX_AMOUNT)


def _compare_ok(compare: object) -> bool:
    """Phase 3 shapes (research §3.3): exact keys per kind, deals 1..10."""
    if not isinstance(compare, dict) or compare.get("kind") not in COMPARE_KINDS:
        return False
    kind = compare["kind"]
    if set(compare) != set(COMPARE_FIELDS[kind]):
        return False
    if kind == "none":
        return True
    deals, n = compare["deals"], compare["n"]
    amount = lambda v: _int(v) and 0 < v <= MAX_AMOUNT  # noqa: E731
    if not (isinstance(deals, list) and 1 <= len(deals) <= MAX_DEALS and all(_deal_ok(d) for d in deals)):
        return False
    if not (_int(compare["months"]) and 1 <= compare["months"] <= MAX_MONTHS and _int(n)
            and amount(compare["medianPerM2"])):
        return False
    if kind == "sameDong":
        return n == len(deals)
    return (n >= MIN_SAME_BUILDING and len(deals) == min(n, MAX_DEALS) and amount(compare["estimate"])
            and _optional(compare["ratio"], lambda v: _int(v) and 0 <= v <= MAX_RATIO))


def _ratio_allowed(r: Mapping) -> bool:
    """LOW-1: share items and items without a lowest bid have no ratio."""
    compare = r["compare"]
    if not isinstance(compare, dict) or compare.get("kind") != "sameBuilding" or compare.get("ratio") is None:
        return True
    return r["share"] is False and r["lowestBid"] is not None


def _next_rounds_ok(rounds: object) -> bool:
    if not isinstance(rounds, list) or len(rounds) > MAX_NEXT_ROUNDS:
        return False
    return all(isinstance(r, dict) and set(r) == {"bidStart", "bidEnd", "lowestBid"}
               and not _schedule_problems(r["bidStart"], r["bidEnd"])
               and _optional(r["lowestBid"], lambda v: _int(v) and 0 < v <= MAX_AMOUNT) for r in rounds)


def _region_problems(r: Mapping, sido: str, index: RegionIndex) -> list[str]:
    problems = []
    sgg = r.get("sgg")
    if not (isinstance(sgg, str) and sgg[:2] == sido and index.is_selectable(sgg)):
        problems.append("invalid sgg")
    umd = r.get("umd")
    if not _optional(umd, lambda v: isinstance(v, str) and DIGITS["umd"].fullmatch(v) and v[:5] == sgg
                     and v in index.emds):
        problems.append("invalid umd")
    if not _optional(r.get("pnu"), lambda v: isinstance(v, str) and DIGITS["pnu"].fullmatch(v)):
        problems.append("invalid pnu")
    return problems


def _value_checks(r: Mapping) -> dict[str, bool]:
    amount = lambda v: _int(v) and 0 < v <= MAX_AMOUNT  # noqa: E731
    area = lambda v: _num(v) and 0 < v <= MAX_AREA  # noqa: E731
    return {
        "key": isinstance(r["key"], str) and bool(KEY.fullmatch(r["key"])),
        **{f: _text_ok(r[f], limit, required=f == "name") for f, limit in TEXT_LIMITS.items()},
        "usage": _usage_ok(r["usage"]),
        "landM2": _optional(r["landM2"], area), "bldgM2": _optional(r["bldgM2"], area),
        "share": isinstance(r["share"], bool),
        **{f: _optional(r[f], amount) for f in ("appraisal", "lowestBid", "prevLowestBid")},
        "ratioToAppraisal": _optional(r["ratioToAppraisal"], lambda v: _int(v) and 0 <= v <= MAX_RATIO),
        "failCount": _int(r["failCount"]) and 0 <= r["failCount"] <= MAX_FAIL_COUNT,
        "thumb": _https(r["thumb"]), "onbidUrl": _https(r["onbidUrl"]),
        "firstSeen": _date_ok(r["firstSeen"], required=True), "priceDropAt": _date_ok(r["priceDropAt"]),
        "compare": _compare_ok(r["compare"]) and _ratio_allowed(r), "nextRounds": _next_rounds_ok(r["nextRounds"]),
    }


def record_problems(record: object, sido: str, index: RegionIndex) -> list[str]:
    """Problems of one item record; empty means the app keeps it."""
    if not isinstance(record, dict):
        return ["not an object"]
    missing = [f"missing {f}" for f in FIELDS if f not in record]
    if missing:
        return missing
    problems = ["unexpected extra field"] if set(record) - set(FIELDS) else []
    problems += [f"invalid {name}" for name, ok in _value_checks(record).items() if not ok]
    problems += _region_problems(record, sido, index)
    return problems + _schedule_problems(record["bidStart"], record["bidEnd"])


def too_many_dropped(dropped: int, total: int) -> bool:
    """D13: more than 20% or more than 500 dropped records."""
    return dropped > 500 or (total > 0 and dropped * 5 > total)


def generated_at_problems(value: object, now: datetime.datetime) -> list[str]:
    if not isinstance(value, str) or not _parses(value, UTC_TIME, datetime.datetime.fromisoformat):
        return ["invalid generatedAt"]
    moment = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return ["generatedAt in the future"] if moment > now + datetime.timedelta(days=1) else []


def region_file_ok(entry: object) -> bool:
    if not isinstance(entry, dict) or not isinstance(entry.get("sido"), str):
        return False
    sido = entry["sido"]
    return bool(re.fullmatch(r"[0-9]{2}", sido)) and entry.get("file") == f"items/{sido}.json"
