"""Daily collector entry point (plan Phase 2, D29, D30).

    python3 -m collector.collect --out DIR (--prev DIR | --first-run)
                                 [--offline-fixtures DIR] [--now 2026-11-01T20:10:00Z]

Steps: key from the environment -> Onbid list -> normalise -> real-
transaction cache refresh/backfill and comparison (Phase 3, compare_hook)
-> day-to-day history -> render -> validate -> write. Nothing under --out changes unless every
step passed (D29 ⑤). Exit codes: 0 success, 1 call failure (or missing
key), 2 validation failure, 3 collector bug (unexpected exception; the
traceback is logged with the key redacted), 64 usage error.

`--offline-fixtures DIR` reads `onbid_p<N>.json` pages and MOLIT pages
`rtms_<api>_<LAWD_CD>_<yyyymm>.xml` (`..._p<N>.xml` for page N > 1) from DIR
instead of calling the APIs; a month without a file is served as a valid
zero-row page. No key is read. `--now` pins the clock so two runs on
the same input give identical bytes.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import time
import traceback
import urllib.parse
from dataclasses import dataclass
from typing import Callable, Mapping

from collector import compare_hook, key_guard, normalize_item, onbid_list, previous_copy, rtms_cache
from collector import rtms_client, seen_history, validate, validate_output, write_output
from collector.budget import Budget
from collector.config import (
    KEY_ENV, KST, MAX_DAILY_DECREASE, MAX_PROVINCE_DECREASE, ONBID_TPS, PROVINCE_GUARD_MIN,
    RTMS_BACKFILL_SECONDS, RTMS_TPS,
)
from collector.errors import EXIT_BUG, EXIT_OK, EXIT_USAGE, CollectError, ValidationFailed
from collector.http_client import ApiClient, Response, Transport, urllib_transport
from collector.normalize_item import Context, NormalizeResult


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # never exit from inside argparse
        raise UsageError(message)


@dataclass(frozen=True)
class Options:
    out: str
    prev: str | None
    first_run: bool
    offline: str | None
    now: datetime.datetime


def _parse_now(text: str | None) -> datetime.datetime:
    if text is None:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    try:
        moment = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise UsageError("--now must be ISO-8601") from None
    if moment.tzinfo is None:
        raise UsageError("--now needs a time zone")
    return moment.astimezone(datetime.timezone.utc).replace(microsecond=0)


def parse_args(argv: list[str]) -> Options:
    parser = _Parser(prog="collect", add_help=False)
    parser.add_argument("--out", required=True)
    parser.add_argument("--prev")
    parser.add_argument("--first-run", action="store_true")
    parser.add_argument("--offline-fixtures")
    parser.add_argument("--now")
    args = parser.parse_args(argv[1:])
    if bool(args.prev) == args.first_run:
        raise UsageError("give exactly one of --prev DIR or --first-run")
    return Options(args.out, args.prev, args.first_run, args.offline_fixtures, _parse_now(args.now))


_RTMS_ALIAS = {endpoint.url: api for api, endpoint in rtms_client.ENDPOINTS.items()}
_SAFE_PARAM = re.compile(r"[0-9]{1,6}")


def _rtms_fixture_name(url: str) -> str | None:
    """`rtms_<api>_<lawd>_<ym>[_p<N>].xml` for a MOLIT request URL, else None."""
    parts = urllib.parse.urlsplit(url)
    api = _RTMS_ALIAS.get(f"{parts.scheme}://{parts.netloc}{parts.path}")
    if api is None:
        return None
    query = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()}
    lawd, ym, page = query.get("LAWD_CD", ""), query.get("DEAL_YMD", ""), query.get("pageNo", "1")
    if not all(_SAFE_PARAM.fullmatch(value) for value in (lawd, ym, page)):
        return "-"
    return f"rtms_{api}_{lawd}_{ym}" + ("" if page == "1" else f"_p{page}") + ".xml"


def _read_fixture(path: str, max_bytes: int) -> Response:
    with open(path, "rb") as handle:
        return Response(status=200, body=handle.read(max_bytes + 1))


def fixture_transport(folder: str) -> Transport:
    """Serves saved Onbid and MOLIT pages in place of the APIs."""
    def transport(url: str, timeout: float, max_bytes: int) -> Response:
        rtms_name = _rtms_fixture_name(url)
        if rtms_name is not None:
            path = os.path.join(folder, rtms_name)
            if os.path.isfile(path):
                return _read_fixture(path, max_bytes)
            return Response(status=200, body=rtms_client.empty_body())
        path = os.path.join(folder, f"onbid_p{onbid_list.page_number_of(url)}.json")
        if not os.path.isfile(path):
            return Response(status=404, body=b"")
        return _read_fixture(path, max_bytes)
    return transport


class Log:
    """stdout for progress, stderr for errors; both pass through redact."""

    def __init__(self, key: str | None) -> None:
        self._key = key

    def info(self, message: str) -> None:
        print(key_guard.redact(message, self._key), flush=True)

    def error(self, message: str) -> None:
        print(key_guard.redact(message, self._key), file=sys.stderr, flush=True)


def _report(result: NormalizeResult, log: Log) -> None:
    log.info(f"normalise: items={len(result.items)} closedOnly={result.closed} "
             f"filteredRows={result.filtered_rows} dropped={dict(sorted(result.dropped.items()))}")
    if result.unknown_usage:
        log.info(f"usage codes not in usage_groups.json (filed as other): {dict(sorted(result.unknown_usage.items()))}")


def _check_drops(result: NormalizeResult) -> None:
    dropped = sum(result.dropped.values())
    if validate.too_many_dropped(dropped, result.candidates):
        raise ValidationFailed(f"too many records dropped ({dropped} of {result.candidates}): "
                               f"{dict(sorted(result.dropped.items()))}")


def _check_decrease(count: int, previous_total: int | None) -> None:
    """D29 ③: more than a 40% fall versus yesterday fails the run."""
    if previous_total and count < previous_total * (1 - MAX_DAILY_DECREASE) - 1e-9:
        raise ValidationFailed(f"item count fell more than 40%: {previous_total} -> {count}")


def _check_provinces(records: list[dict], previous_counts: Mapping[str, int]) -> None:
    """Review M3: a province with >= 50 items yesterday may not fall by more than 60%."""
    today: dict[str, int] = {}
    for record in records:
        today[record["sgg"][:2]] = today.get(record["sgg"][:2], 0) + 1
    for sido, before in sorted(previous_counts.items()):
        now = today.get(sido, 0)
        if before >= PROVINCE_GUARD_MIN and now < before * (1 - MAX_PROVINCE_DECREASE) - 1e-9:
            raise ValidationFailed(f"province {sido} item count fell more than 60%: {before} -> {now}")


def _check_history(files: Mapping[str, bytes]) -> None:
    """Review L1: the history written today must be readable tomorrow."""
    try:
        seen_history.parse(json.loads(files["cache/seen.json"].decode("utf-8")))
    except (KeyError, UnicodeDecodeError, ValueError):
        raise ValidationFailed("cache/seen.json would not be readable tomorrow") from None


@dataclass(frozen=True)
class Clients:
    onbid: ApiClient
    new_rtms: Callable[[], ApiClient]     # one client per MOLIT API thread


def _clients(opts: Options, env: Mapping[str, str], transport: Transport | None,
             sleep: Callable[[float], None], clock: Callable[[], float]) -> Clients:
    if opts.offline:
        key, chosen = "offline", transport or fixture_transport(opts.offline)
    else:
        key, chosen = key_guard.read_key(env), transport or urllib_transport
        key_guard.emit_masks(key, env, sys.stdout)
    return Clients(ApiClient(chosen, key, tps=ONBID_TPS, monotonic=clock, sleep=sleep),
                   lambda: ApiClient(chosen, key, tps=RTMS_TPS, monotonic=clock, sleep=sleep))


def _compare(clients: Clients, result: NormalizeResult, previous: previous_copy.Previous, budget: Budget,
             today: datetime.date, context: Context, log: Log, clock: Callable[[], float],
             deadline: float) -> tuple[list[dict], compare_hook.RtmsRun]:
    if previous.rtms_skipped:
        log.info(f"rtms: skipped {previous.rtms_skipped} unreadable cache file(s); they are fetched again")
    run = compare_hook.refresh(clients.new_rtms, result.items, previous.rtms, budget, today, context.regions, clock,
                               deadline)
    for line in compare_hook.report(run):
        log.info(line)
    records = compare_hook.attach_compare(result.items, run.context)
    log.info(f"compare by group: {compare_hook.kinds_by_group(records)}")
    return records, run


def _collect(opts: Options, env: Mapping[str, str], transport: Transport | None, log: Log,
             sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> int:
    deadline = clock() + RTMS_BACKFILL_SECONDS     # backfill stops here; refresh always runs
    clients = _clients(opts, env, transport, sleep, clock)
    today = opts.now.astimezone(KST).date()
    previous = previous_copy.load(opts.prev) if opts.prev else previous_copy.FIRST_RUN
    rows, budget = onbid_list.fetch_all(clients.onbid, Budget.load(previous.budget, today.isoformat()))
    log.info(f"onbid: rows={len(rows)} calls={budget.used_of('onbid')}")
    context = Context.load(opts.now.astimezone(KST))
    result = normalize_item.normalize_all(rows, context)
    _report(result, log)
    _check_drops(result)
    records, run = _compare(clients, result, previous, budget, today, context, log, clock, deadline)
    records, seen = seen_history.apply(records, previous.seen, today)
    _check_decrease(len(records), previous.total)
    _check_provinces(records, previous.counts)
    generated_at = opts.now.strftime("%Y-%m-%dT%H:%M:%SZ")
    extra = {**rtms_cache.render(run.cache), compare_hook.STATE_PATH: write_output.dumps(dict(run.state))}
    files = write_output.render(records, generated_at, context.regions, seen, run.budget, extra)
    _check_history(files)
    problems = validate_output.tree_problems(files, opts.now, context.regions)
    if problems:
        raise ValidationFailed(f"output failed validation ({len(problems)}): {problems[:5]}")
    write_output.commit(files, opts.out)
    log.info(f"done: items={len(records)} firstRun={opts.first_run} generatedAt={generated_at}")
    return EXIT_OK


def main(argv: list[str], env: Mapping[str, str] | None = None, transport: Transport | None = None,
         sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> int:
    env = os.environ if env is None else env
    log = Log(env.get(KEY_ENV, "").strip() or None)
    try:
        opts = parse_args(argv)
    except UsageError as error:
        log.error(f"usage error: {error}")
        return EXIT_USAGE
    try:
        return _collect(opts, env, transport, log, sleep, clock)
    except CollectError as error:
        log.error(str(error))
        return error.exit_code
    except Exception as error:  # noqa: BLE001 - a collector bug: exit 3, traceback redacted
        log.error(f"collector stopped: unexpected {type(error).__name__}\n{traceback.format_exc()}")
        return EXIT_BUG


if __name__ == "__main__":
    sys.exit(main(sys.argv))
