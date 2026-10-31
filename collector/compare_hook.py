"""Real-transaction stage: cache refresh/backfill, then `compare` per item (plan Phase 3).

collect.py calls `refresh` after normalising (with yesterday's
`cache/rtms/` from the data branch) and `attach_compare` before the
day-to-day history. `refresh` raises RtmsFailed when more than 10% of the
attempted months failed (D29 ②); a failed month otherwise keeps
yesterday's file. Without a context (tests, Phase 2 behaviour) every item
gets `{"kind": "none"}`. Raw MOLIT rows never reach `v1/`: items only get
the comparison summary (at most 10 deals).
"""

from __future__ import annotations

import datetime
from collections import Counter
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Callable, Iterable, Mapping

from collector import backfill, compare, rtms_cache, rtms_client
from collector.budget import Budget
from collector.config import RTMS_APIS, RTMS_BACKFILL_SECONDS, RTMS_STAGE_SECONDS
from collector.errors import RtmsFailed
from collector.http_client import ApiClient
from collector.region_index import RegionIndex

NO_COMPARE: Mapping[str, str] = MappingProxyType({"kind": "none"})
STATE_PATH = "cache/backfill_state.json"


@dataclass(frozen=True)
class CompareContext:
    index: compare.DealIndex
    params: compare.Params


@dataclass(frozen=True)
class RtmsRun:
    context: CompareContext
    budget: Budget
    cache: rtms_cache.Cache
    state: Mapping
    outcome: backfill.Outcome


def _fetcher(client: ApiClient, regions: RegionIndex) -> backfill.Fetch:
    """Fetch + summarise one month with [client] (one client per API thread)."""
    def fetch(task: backfill.Task, budget: Budget) -> rtms_client.MonthResult:
        result = rtms_client.fetch_month(client, task.api, task.lawd, task.ym, budget, regions)
        if result.status != "ok":
            return result
        rows = (rtms_client.summarize(task.api, task.lawd, raw, regions) for raw in result.rows)
        return replace(result, rows=tuple(row for row in rows if row is not None))
    return fetch


def refresh(new_client: Callable[[], ApiClient], items: Iterable, previous: rtms_cache.Cache, budget: Budget,
            today: datetime.date, regions: RegionIndex, clock: Callable[[], float],
            deadlines: backfill.Deadlines | None = None, params: compare.Params | None = None,
            previous_state: object = None) -> RtmsRun:
    """[deadlines]: clock values (default: now + RTMS_BACKFILL_SECONDS / RTMS_STAGE_SECONDS)."""
    params = params or compare.Params.load()
    if deadlines is None:
        start = clock()
        deadlines = backfill.Deadlines(start + RTMS_BACKFILL_SECONDS, start + RTMS_STAGE_SECONDS)
    months = rtms_cache.window(today, params.months)
    pairs = backfill.pairs_of(items)
    seen = backfill.last_seen(previous_state, pairs, today)
    active = {slot: entry for slot, entry in rtms_cache.in_window(previous, months).items()
              if (slot[0], slot[1]) in seen}
    tasks = backfill.plan(pairs, months, set(active), regions.is_selectable)
    outcome = backfill.run(tasks, lambda api: _fetcher(new_client(), regions), active, budget, today.isoformat(),
                           clock, deadlines)
    problem = backfill.failure_problem(outcome)
    if problem:
        raise RtmsFailed(problem + "; first failures: " + " | ".join(outcome.errors[:3]))
    state = backfill.state(outcome, pairs, months, today.isoformat(), seen)
    context = CompareContext(compare.deals_index(outcome.cache), params)
    return RtmsRun(context, outcome.budget, outcome.cache, MappingProxyType(state), outcome)


def report(run: RtmsRun) -> list[str]:
    """One log line per API that has pairs (counts only, no URLs)."""
    lines = []
    for api in RTMS_APIS:
        entry = run.state["apis"][api]
        if entry["pairs"]:
            lines.append(f"rtms {api}: pairs={entry['pairs']} cached={entry['cached']}/{entry['months']} "
                         f"fetched={entry['fetched']} failed={entry['failed']} deferred={entry['deferred']} "
                         f"blocked={entry['blocked'] or '-'} calls={run.budget.used_of(api)}")
    lines += [f"rtms error: {error}" for error in run.outcome.errors[:5]]
    manifest = rtms_cache.manifest(rtms_cache.render(run.cache))
    lines.append(f"rtms manifest: files={manifest['files']} rows={manifest['rows']} sha256={manifest['sha256']}")
    return lines


def attach_compare(items: Iterable, context: CompareContext | None = None) -> list[dict]:
    """Records with `compare` set (kind none without a context)."""
    if context is None:
        return [{**item.record, "compare": dict(NO_COMPARE)} for item in items]
    return [{**item.record, "compare": compare.compare_item(item, context.index, context.params)} for item in items]


def kinds_by_group(records: Iterable[Mapping]) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter] = {}
    for record in records:
        counts.setdefault(record["usage"]["group"], Counter())[record["compare"]["kind"]] += 1
    return {group: dict(sorted(counter.items())) for group, counter in sorted(counts.items())}
