"""Daily collector entry point (plan Phase 2, D29, D30).

    python3 -m collector.collect --out DIR (--prev DIR | --first-run)
                                 [--offline-fixtures DIR] [--now 2026-11-01T20:10:00Z]

Steps: key from the environment -> Onbid list -> normalise -> (Phase 3:
real-transaction comparison, compare_hook) -> day-to-day history ->
render -> validate -> write. Nothing under --out changes unless every
step passed (D29 ⑤). Exit codes: 0 success, 1 call failure (or missing
key), 2 validation failure, 64 usage error.

`--offline-fixtures DIR` reads `onbid_p<N>.json` pages from DIR instead
of calling the API; no key is read. `--now` pins the clock so two runs on
the same input give identical bytes.
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
from dataclasses import dataclass
from typing import Mapping

from collector import compare_hook, key_guard, normalize_item, onbid_list, previous_copy, seen_history
from collector import validate, validate_output, write_output
from collector.budget import Budget
from collector.config import KEY_ENV, KST, MAX_DAILY_DECREASE, ONBID_TPS
from collector.errors import EXIT_OK, EXIT_USAGE, CollectError, ValidationFailed
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


def fixture_transport(folder: str) -> Transport:
    """Serves saved Onbid pages (`onbid_p<N>.json`) in place of the API."""
    def transport(url: str, timeout: float, max_bytes: int) -> Response:
        page = onbid_list.page_number_of(url)
        path = os.path.join(folder, f"onbid_p{page}.json")
        if not os.path.isfile(path):
            return Response(status=404, body=b"")
        with open(path, "rb") as handle:
            return Response(status=200, body=handle.read(max_bytes + 1))
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


def _client(opts: Options, env: Mapping[str, str], transport: Transport | None) -> ApiClient:
    if opts.offline:
        return ApiClient(transport or fixture_transport(opts.offline), "offline", tps=ONBID_TPS)
    key = key_guard.read_key(env)
    key_guard.emit_masks(key, env, sys.stdout)
    return ApiClient(transport or urllib_transport, key, tps=ONBID_TPS)


def _collect(opts: Options, env: Mapping[str, str], transport: Transport | None, log: Log) -> int:
    client = _client(opts, env, transport)
    today = opts.now.astimezone(KST).date()
    previous = previous_copy.load(opts.prev) if opts.prev else previous_copy.FIRST_RUN
    rows, budget = onbid_list.fetch_all(client, Budget.load(previous.budget, today.isoformat()))
    log.info(f"onbid: rows={len(rows)} calls={budget.used_of('onbid')}")
    context = Context.load(opts.now.astimezone(KST))
    result = normalize_item.normalize_all(rows, context)
    _report(result, log)
    _check_drops(result)
    records = compare_hook.attach_compare(result.items)   # Phase 3 plugs in here.
    records, seen = seen_history.apply(records, previous.seen, today)
    _check_decrease(len(records), previous.total)
    generated_at = opts.now.strftime("%Y-%m-%dT%H:%M:%SZ")
    files = write_output.render(records, generated_at, context.regions, seen, budget)
    problems = validate_output.tree_problems(files, opts.now, context.regions)
    if problems:
        raise ValidationFailed(f"output failed validation ({len(problems)}): {problems[:5]}")
    write_output.commit(files, opts.out)
    log.info(f"done: items={len(records)} firstRun={opts.first_run} generatedAt={generated_at}")
    return EXIT_OK


def main(argv: list[str], env: Mapping[str, str] | None = None, transport: Transport | None = None) -> int:
    env = os.environ if env is None else env
    log = Log(env.get(KEY_ENV) or None)
    try:
        opts = parse_args(argv)
    except UsageError as error:
        log.error(f"usage error: {error}")
        return EXIT_USAGE
    try:
        return _collect(opts, env, transport, log)
    except CollectError as error:
        log.error(str(error))
        return error.exit_code
    except Exception as error:  # noqa: BLE001 - a traceback could carry a URL
        log.error(f"collector stopped: unexpected {type(error).__name__}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
