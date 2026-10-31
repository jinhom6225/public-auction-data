"""Plan A9 re-measurement: same-building matches over 12 months with real MOLIT data.

Dev tool only (tests never run it). Items come from the uncommitted Phase 1
Onbid dump (public_auction_alert/tool/spike/.cache/). MOLIT pages are
fetched once with the collector's own client and parser, saved byte for
byte under <work_dir>/raw/ (gitignored `.local/`), and re-used on later
runs without calling again. The comparison is the collector's
`compare.compare_item` with `data/compare_params.json` unchanged.

    AUCTION_KEY_FILE=/path/to/prod.json \
    python3 -I collector/dev/measure_a9.py <dump_dir> <work_dir> [--plan | --offline]

The key is read from the JSON file named by AUCTION_KEY_FILE (field
DATA_GO_KR_API_KEY) and never printed or written. Real calls are counted
in <work_dir>/calls.tsv (api, lawd, ym, page, http, bytes, seconds — no
URL) and refused beyond CAPS. A body that contains the key or
`serviceKey=` is not saved.
"""

from __future__ import annotations

import datetime
import glob
import json
import os
import statistics
import sys
import time
import urllib.parse
from collections import Counter
from dataclasses import replace

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")))

from collector import compare, key_guard, pnu, rtms_cache, rtms_client  # noqa: E402
from collector.budget import Budget  # noqa: E402
from collector.config import KST, RTMS_TPS  # noqa: E402
from collector.http_client import ApiClient, Response, urllib_transport  # noqa: E402
from collector.normalize_item import Context, normalize_all  # noqa: E402

CAPS = {"apt": 300, "rh": 300, "offi": 300, "land": 30, "sh": 30, "nrg": 30, "indu": 30}
GROUP_API = {"apt": "apt", "rowhouse": "rh", "officetel": "offi"}
TOP = 20
EXTRA_GOLDEN = 3
SMOKE_SGGS = 2
DUMP_TIME = datetime.datetime(2026, 10, 31, 13, 0, tzinfo=KST)
GOLDEN_PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests", "fixtures",
                           "onbid_pages", "onbid_p1.json")
_ALIAS = {endpoint.url: api for api, endpoint in rtms_client.ENDPOINTS.items()}


def read_key() -> str:
    path = os.environ.get("AUCTION_KEY_FILE")
    if not path:
        sys.exit("AUCTION_KEY_FILE is not set")
    with open(path, encoding="utf-8") as handle:
        key = json.load(handle).get("DATA_GO_KR_API_KEY", "").strip()
    if not key:
        sys.exit("DATA_GO_KR_API_KEY missing in key file")
    return key


def load_items(dump_dir: str, ctx: Context):
    rows: list[dict] = []
    for path in sorted(glob.glob(os.path.join(dump_dir, "onbid_all_N_p*_n5000.json"))):
        with open(path, encoding="utf-8") as handle:
            rows.extend(json.load(handle)["body"]["items"]["item"])
    return normalize_all(rows, ctx).items


def golden_pairs(ctx: Context) -> Counter:
    with open(GOLDEN_PAGE, encoding="utf-8") as handle:
        rows = json.load(handle)["body"]["items"]["item"]
    return Counter((i.rtms, i.record["sgg"]) for i in normalize_all(rows, ctx).items if i.rtms)


def sample(items, golden: Counter) -> dict[str, list[str]]:
    """api -> sgg list: top TOP by items (+ golden sggs) for apt/rh/offi, golden sggs for the rest."""
    counts = Counter((i.rtms, i.record["sgg"]) for i in items if i.rtms)
    chosen: dict[str, list[str]] = {}
    for api in CAPS:
        ranked = [sgg for (a, sgg), _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0][1])) if a == api]
        gold = [sgg for (a, sgg), _ in sorted(golden.items(), key=lambda kv: (-kv[1], kv[0][1])) if a == api]
        if api in GROUP_API.values():
            top = ranked[:TOP]
            chosen[api] = top + [sgg for sgg in gold if sgg not in top][:EXTRA_GOLDEN]
        else:
            chosen[api] = gold[:SMOKE_SGGS]
    return chosen


class SavingTransport:
    """Serves saved pages; calls the real API only for missing ones, within CAPS."""

    def __init__(self, work: str, key: str, offline: bool) -> None:
        self.offline = offline
        self.raw = os.path.join(work, "raw")
        self.log = os.path.join(work, "calls.tsv")
        self.forms = key_guard.key_forms(key)
        os.makedirs(self.raw, exist_ok=True)
        self.used = Counter(line.split("\t")[0] for line in self._lines())
        self.seconds: list[float] = []

    def _lines(self) -> list[str]:
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as handle:
            return [line for line in handle.read().splitlines()[1:] if line]

    def _name(self, url: str) -> tuple[str, str, str, str]:
        parts = urllib.parse.urlsplit(url)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()}
        api = _ALIAS[f"{parts.scheme}://{parts.netloc}{parts.path}"]
        return api, query["LAWD_CD"], query["DEAL_YMD"], query["pageNo"]

    def __call__(self, url: str, timeout: float, max_bytes: int) -> Response:
        api, lawd, ym, page = self._name(url)
        path = os.path.join(self.raw, f"rtms_{api}_{lawd}_{ym}_p{page}.xml")
        if os.path.exists(path):
            with open(path, "rb") as handle:
                return Response(200, handle.read())
        if self.offline or self.used[api] >= CAPS[api]:
            raise RuntimeError("offline or cap reached")
        started = time.monotonic()
        response = urllib_transport(url, timeout, max_bytes)
        elapsed = time.monotonic() - started
        self.used[api] += 1
        self.seconds.append(elapsed)
        self._record(api, lawd, ym, page, response, elapsed)
        clean = not any(form.encode() in response.body for form in self.forms) and b"serviceKey=" not in response.body
        if response.status == 200 and clean:
            with open(path, "wb") as handle:
                handle.write(response.body)
        else:
            self.offline = True    # stop calling after the first non-200 or suspicious body
        return response

    def _record(self, api, lawd, ym, page, response, elapsed) -> None:
        new = not os.path.exists(self.log)
        with open(self.log, "a", encoding="utf-8") as handle:
            if new:
                handle.write("api\tlawd\tym\tpage\thttp\tbytes\tseconds\n")
            handle.write(f"{api}\t{lawd}\t{ym}\t{page}\t{response.status}\t{len(response.body)}\t{elapsed:.2f}\n")


def fetch_all(client, chosen, months, ctx) -> tuple[dict, Counter]:
    cache: dict = {}
    status: Counter = Counter()
    budget = Budget.load(None, "2026-10-31", {api: 10**6 for api in CAPS})
    for api, sggs in chosen.items():
        for lawd in sggs:
            for ym in months:
                result = rtms_client.fetch_month(client, api, lawd, ym, budget, ctx.regions)
                budget = result.budget
                status[(api, result.status)] += 1
                if result.status == "ok":
                    rows = [rtms_client.summarize(api, lawd, raw, ctx.regions) for raw in result.rows]
                    cache[(api, lawd, ym)] = rtms_cache.Month(api, lawd, ym, "2026-10-31",
                                                              tuple(r for r in rows if r is not None))
    return cache, status


def _example(item, result, deals) -> dict:
    record = item.record
    key = pnu.item_key(record["pnu"])
    areas = sorted({d.area for d in deals if d.key == key})
    return {"key": record["key"], "name": record["name"][:60], "bldgM2": record["bldgM2"],
            "share": record["share"], "lowestBid": record["lowestBid"], "estimate": result.get("estimate"),
            "ratio": result.get("ratio"), "n": result.get("n"), "buildingDealAreas": areas[:8],
            "usage": record["usage"]["s"]}


def group_stats(group, items, index, params, chosen) -> dict:
    api = GROUP_API[group]
    members = [i for i in items if i.record["usage"]["group"] == group and i.rtms == api
               and i.record["sgg"] in chosen[api]]
    kinds, ratios, any_building, low, high = Counter(), [], 0, [], []
    what_if = replace(params, building_min=2)
    what_if_badges = 0
    for item in members:
        result = compare.compare_item(item, index, params)
        kinds[result["kind"]] += 1
        pool = index.get((api, item.record["sgg"]), ())
        key = pnu.item_key(item.record["pnu"])
        any_building += bool(key and any(d.key == key for d in pool))
        if result["kind"] == "sameBuilding" and result["ratio"] is not None:
            ratios.append(result["ratio"])
            if result["ratio"] < 30:
                low.append(_example(item, result, pool))
            if result["ratio"] > 150:
                high.append(_example(item, result, pool))
        alt = compare.compare_item(item, index, what_if)
        what_if_badges += alt["kind"] == "sameBuilding" and alt["ratio"] is not None
    n = len(members)
    pct = lambda x: round(100 * x / n, 1) if n else None  # noqa: E731
    return {
        "items": n, "noPnu": sum(1 for i in members if not i.record["pnu"]),
        "anySameBuildingDeal": any_building, "anySameBuildingDealPct": pct(any_building),
        "kinds": dict(kinds), "sameBuildingPct": pct(kinds["sameBuilding"]),
        "ratioBadge": len(ratios), "ratioBadgePct": pct(len(ratios)),
        "sameDongPct": pct(kinds["sameDong"]), "nonePct": pct(kinds["none"]),
        "ratio": {"min": min(ratios), "median": statistics.median(ratios), "max": max(ratios)} if ratios else None,
        "under30": len(low), "over150": len(high), "under30Examples": low[:8], "over150Examples": high[:8],
        "whatIfMin2RatioBadge": what_if_badges, "whatIfMin2Pct": pct(what_if_badges),
        "shareWithSameBuilding": sum(1 for i in members if i.record["share"]
                                     and compare.compare_item(i, index, params)["kind"] == "sameBuilding"),
    }


def rowhouse_in_apt(items, index, params, chosen) -> dict:
    """Report only: 10208/10219 rowhouse items whose building shows up in apartment rows."""
    found, strong, total = 0, 0, 0
    for item in items:
        if item.usage_code not in ("10208", "10219") or item.record["sgg"] not in chosen["apt"]:
            continue
        total += 1
        key = pnu.item_key(item.record["pnu"])
        pool = [d for d in index.get(("apt", item.record["sgg"]), ()) if key and d.key == key]
        found += bool(pool)
        area = item.record["bldgM2"]
        strong += bool(area) and sum(1 for d in pool if abs(d.area - area) <= 0.1 * area + 1e-9) >= 3
    return {"items": total, "anyAptDeal": found, "aptSameBuilding3": strong}


def smoke_stats(items, index, params, chosen) -> dict:
    out = {}
    for api in ("land", "sh", "nrg", "indu"):
        members = [i for i in items if i.rtms == api and i.record["sgg"] in chosen[api]]
        out[api] = dict(Counter(compare.compare_item(i, index, params)["kind"] for i in members))
        out[api]["items"] = len(members)
    return out


def size_stats(cache) -> dict:
    files = rtms_cache.render(cache)
    out = {}
    for api in CAPS:
        paths = [p for p in files if p.startswith(f"cache/rtms/{api}/")]
        rows = sum(len(cache[s].rows) for s in cache if s[0] == api)
        size = sum(len(files[p]) for p in paths)
        out[api] = {"files": len(paths), "rows": rows, "bytes": size,
                    "bytesPerRow": round(size / rows, 1) if rows else None,
                    "rowsPerMonthFile": round(rows / len(paths), 1) if paths else None}
    return out


def run(argv: list[str]) -> int:
    if len(argv) < 3:
        sys.exit(__doc__)
    dump_dir, work = argv[1], argv[2]
    ctx = Context.load(DUMP_TIME)
    items = load_items(dump_dir, ctx)
    chosen = sample(items, golden_pairs(ctx))
    months = rtms_cache.window(DUMP_TIME.date(), 12)
    offline = "--offline" in argv
    if "--plan" in argv:
        print(json.dumps({api: {"sggs": sggs, "calls": len(sggs) * len(months)} for api, sggs in chosen.items()},
                         ensure_ascii=False))
        return 0
    key = "offline-no-calls" if offline else read_key()
    transport = SavingTransport(work, key, offline)
    client = ApiClient(transport, key, tps=RTMS_TPS)
    cache, status = fetch_all(client, chosen, months, ctx)
    params = compare.Params.load()
    index = compare.deals_index(cache)
    report = {
        "window": [months[-1], months[0]], "sample": chosen,
        "fetch": {f"{api} {state}": count for (api, state), count in sorted(status.items())},
        "realCalls": dict(transport.used),
        "latency": {"calls": len(transport.seconds),
                    "mean": round(statistics.mean(transport.seconds), 2) if transport.seconds else None,
                    "median": round(statistics.median(transport.seconds), 2) if transport.seconds else None,
                    "max": round(max(transport.seconds), 2) if transport.seconds else None},
        "groups": {group: group_stats(group, items, index, params, chosen) for group in GROUP_API},
        "rowhouseInApt": rowhouse_in_apt(items, index, params, chosen),
        "smoke": smoke_stats(items, index, params, chosen),
        "size": size_stats(cache),
    }
    with open(os.path.join(work, "report.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)
    summary = {k: v for k, v in report.items() if k != "groups"}
    print(json.dumps(summary, ensure_ascii=False))
    for group, stats in report["groups"].items():
        print(group, json.dumps({k: v for k, v in stats.items() if not k.endswith("Examples")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run(sys.argv))
    except SystemExit:
        raise
    except Exception as error:  # a traceback could carry the URL with the key
        sys.exit(f"measure_a9 stopped: {type(error).__name__}")
