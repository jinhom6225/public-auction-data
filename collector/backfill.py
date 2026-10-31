"""Which real-transaction months to fetch today, within the daily caps (plan D17, D29 ②).

Pairs = (API, sgg) that have at least one item today. Order:
  1. refresh: current and previous month of every pair (always, even
     past the backfill deadline), pairs with more items first;
  2. backfill: months of the 12-month window not yet cached, pairs with
     more items first, recent months first. Months 2-11 back that are
     cached are never fetched again (late cancellations are accepted, D17).
Progress persists through the cache itself (a cached month is done) plus
`cache/backfill_state.json` (counts per API, for the log and the runbook).

A month whose call fails (after one retry, rtms_client) keeps yesterday's
file; more than 10% failed months in a run fails the run (D29 ②). An API
that reaches our daily cap or the provider's daily limit (code 22) stops
for today and is not counted as failed; the next run continues. Each
attempt, retries included, spends one call of the API's budget.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Callable, Iterable, Mapping

from collector.budget import Budget
from collector.config import RTMS_APIS, RTMS_MAX_FAILURE_SHARE, SCHEMA_VERSION
from collector.rtms_cache import Month, Slot
from collector.rtms_client import MonthResult

Pair = tuple[str, str]   # (api, lawd)


@dataclass(frozen=True)
class Task:
    api: str
    lawd: str
    ym: str
    refresh: bool


@dataclass(frozen=True)
class ApiStats:
    fetched: int = 0
    failed: int = 0
    deferred: int = 0
    blocked: str | None = None     # "cap" | "quota"


@dataclass(frozen=True)
class Outcome:
    cache: Mapping[Slot, Month]
    budget: Budget
    stats: Mapping[str, ApiStats]
    errors: tuple[str, ...]


Fetch = Callable[[Task, Budget], MonthResult]


def pairs_of(items: Iterable) -> dict[Pair, int]:
    """(api, sgg) -> number of items; items without an RTMS API are skipped."""
    counts: dict[Pair, int] = {}
    for item in items:
        if item.rtms:
            pair = (item.rtms, item.record["sgg"])
            counts[pair] = counts.get(pair, 0) + 1
    return counts


def _ordered(pairs: Mapping[Pair, int]) -> list[Pair]:
    return sorted(pairs, key=lambda pair: (-pairs[pair], pair[1], pair[0]))


def plan(pairs: Mapping[Pair, int], months: tuple[str, ...], cached: set[Slot],
         selectable: Callable[[str], bool] = lambda code: True) -> tuple[Task, ...]:
    """Tasks in priority order; months[0] is the current month."""
    bad = [lawd for _, lawd in pairs if not selectable(lawd)]
    if bad:
        raise ValueError("real-transaction pairs must use current, selectable codes")
    order = _ordered(pairs)
    refresh = [Task(api, lawd, ym, True) for api, lawd in order for ym in months[:2]]
    missing = [Task(api, lawd, ym, False) for api, lawd in order for ym in months[2:]
               if (api, lawd, ym) not in cached]
    return tuple(refresh + missing)


@dataclass(frozen=True)
class _ApiRun:
    months: Mapping[Slot, Month]
    used: int
    stats: ApiStats
    errors: tuple[str, ...]


def _run_api(tasks: list[Task], fetch: Fetch, budget: Budget, today: str, clock: Callable[[], float],
             deadline: float) -> _ApiRun:
    """One API's tasks, in order, sequentially (its budget is only touched here)."""
    months: dict[Slot, Month] = {}
    stats, errors = ApiStats(), []
    for task in tasks:
        if stats.blocked:
            break
        if not task.refresh and clock() > deadline:
            stats = replace(stats, deferred=stats.deferred + 1)
            continue
        result = fetch(task, budget)
        budget = result.budget
        if result.status == "ok":
            months[(task.api, task.lawd, task.ym)] = Month(task.api, task.lawd, task.ym, today, result.rows)
            stats = replace(stats, fetched=stats.fetched + 1)
        elif result.status == "failed":
            stats = replace(stats, failed=stats.failed + 1)
            errors.append(f"{task.api} {task.lawd} {task.ym}: {result.error}")
        else:
            stats = replace(stats, blocked="cap" if result.status == "capped" else "quota")
    api = tasks[0].api if tasks else ""
    return _ApiRun(months, budget.used_of(api) if api else 0, stats, tuple(errors))


def run(tasks: Iterable[Task], fetch_for: Callable[[str], Fetch], cache: Mapping[Slot, Month], budget: Budget,
        today: str, clock: Callable[[], float], deadline: float) -> Outcome:
    """Fetch [tasks]; each API runs in its own thread, in task order.

    MOLIT answers in about 1 s per call, so the seven APIs run side by side
    (each with its own client from [fetch_for], see RTMS_TPS); within an API
    the order and the budget stay sequential. Backfill tasks are skipped once
    clock() passes [deadline]. Returns the updated cache (never mutates [cache]).
    """
    by_api: dict[str, list[Task]] = {}
    for task in tasks:
        by_api.setdefault(task.api, []).append(task)
    with ThreadPoolExecutor(max_workers=max(1, len(by_api))) as pool:
        futures = {api: pool.submit(_run_api, api_tasks, fetch_for(api), budget, today, clock, deadline)
                   for api, api_tasks in by_api.items()}
        results = {api: future.result() for api, future in futures.items()}
    updated = dict(cache)
    used = dict(budget.used)
    stats = {api: ApiStats() for api in RTMS_APIS}
    errors: list[str] = []
    for api in sorted(results, key=RTMS_APIS.index):
        result = results[api]
        updated.update(result.months)
        used[api] = result.used
        stats[api] = result.stats
        errors.extend(result.errors)
    merged = Budget(budget.date, MappingProxyType(used), budget.caps)
    return Outcome(MappingProxyType(updated), merged, MappingProxyType(stats), tuple(errors))


def failure_problem(outcome: Outcome) -> str | None:
    """D29 ②: a message when more than 10% of the attempted months failed."""
    failed = sum(s.failed for s in outcome.stats.values())
    attempted = failed + sum(s.fetched for s in outcome.stats.values())
    if attempted and failed > attempted * RTMS_MAX_FAILURE_SHARE + 1e-9:
        return f"real-transaction months failed: {failed} of {attempted} (more than 10%)"
    return None


def state(outcome: Outcome, pairs: Mapping[Pair, int], months: tuple[str, ...], today: str) -> dict:
    """`cache/backfill_state.json`."""
    apis = {}
    for api in RTMS_APIS:
        lawds = {lawd for pair_api, lawd in pairs if pair_api == api}
        cached = sum(1 for slot in outcome.cache if slot[0] == api and slot[1] in lawds and slot[2] in months)
        s = outcome.stats[api]
        apis[api] = {"pairs": len(lawds), "months": len(lawds) * len(months), "cached": cached,
                     "fetched": s.fetched, "failed": s.failed, "deferred": s.deferred, "blocked": s.blocked}
    return {"schemaVersion": SCHEMA_VERSION, "date": today, "window": list(months), "apis": apis}
