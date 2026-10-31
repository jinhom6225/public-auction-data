import datetime
import threading
import unittest
from types import SimpleNamespace

import netguard  # noqa: F401  (blocks the network)

from collector import backfill, rtms_cache
from collector.backfill import Task
from collector.budget import Budget
from collector.rtms_cache import Month
from collector.rtms_client import MonthResult

TODAY = datetime.date(2026, 11, 2)
WINDOW = rtms_cache.window(TODAY, 12)       # 202611 ... 202512
ROW = {"d": 1, "p": 100000000, "a": 50.0, "u": "1111010100"}


def item(sgg, rtms):
    return SimpleNamespace(record={"sgg": sgg}, rtms=rtms)


def budget(day="2026-11-02", caps=None, **used):
    caps = caps or {"apt": 900, "land": 900, "rh": 900, "sh": 900}
    return Budget.load({"date": day, "used": used}, day, caps)


class FakeFetch:
    def __init__(self, fail=(), quota=()):
        self.calls = []
        self.fail, self.quota = set(fail), set(quota)

    def __call__(self, task, spent):
        if not spent.can_spend(task.api, 1):
            return MonthResult("capped", (), spent)
        spent = spent.spend(task.api)
        self.calls.append((task.api, task.lawd, task.ym))
        if (task.api, task.lawd, task.ym) in self.fail:
            return MonthResult("failed", (), spent, "x failed: HTTP 500")
        if task.api in self.quota:
            return MonthResult("quota", (), spent, "x failed: resultCode 22")
        return MonthResult("ok", (ROW,), spent)


def run(tasks, fetch, cache=None, spent=None, clock=lambda: 0.0, deadline=1800, today=TODAY.isoformat(),
        stage=3000, cancel=None, fetch_for=None):
    return backfill.run(tasks, fetch_for or (lambda api: fetch), cache or {}, spent or budget(), today, clock,
                        backfill.Deadlines(deadline, stage), cancel)


class PairsAndPlanTest(unittest.TestCase):
    def test_pairs_count_items_and_skip_items_without_api(self):
        items = [item("11500", "rh"), item("11500", "rh"), item("11500", "offi"), item("12210", "apt"),
                 item("11500", None)]
        self.assertEqual(backfill.pairs_of(items), {("rh", "11500"): 2, ("offi", "11500"): 1, ("apt", "12210"): 1})

    def test_old_and_new_codes_meet_in_one_current_pair(self):
        # D8: items already carry current codes (29110 -> 12210 in normalize), so one pair.
        self.assertEqual(backfill.pairs_of([item("12210", "apt"), item("12210", "apt")]), {("apt", "12210"): 2})

    def test_plan_refreshes_current_and_previous_month_first(self):
        tasks = backfill.plan({("land", "11110"): 1, ("land", "41461"): 5}, WINDOW, set())
        self.assertEqual([(t.lawd, t.ym, t.refresh) for t in tasks[:4]],
                         [("41461", "202611", True), ("41461", "202610", True),
                          ("11110", "202611", True), ("11110", "202610", True)])
        # then the sgg with more items, recent months first
        self.assertEqual([(t.lawd, t.ym) for t in tasks[4:6]], [("41461", "202609"), ("41461", "202608")])
        self.assertEqual(tasks[-1], Task("land", "11110", "202512", False))
        self.assertEqual(len(tasks), 24)

    def test_cached_older_months_are_not_fetched_again(self):
        cached = {("apt", "12210", ym) for ym in WINDOW}
        tasks = backfill.plan({("apt", "12210"): 3}, WINDOW, cached)
        self.assertEqual([t.ym for t in tasks], ["202611", "202610"])

    def test_plan_refuses_non_selectable_codes(self):
        with self.assertRaises(ValueError):
            backfill.plan({("apt", "29110"): 1}, WINDOW, set(), selectable=lambda code: code != "29110")


class RunTest(unittest.TestCase):
    def test_all_months_fetched(self):
        tasks = backfill.plan({("apt", "12210"): 1}, WINDOW, set())
        outcome = run(tasks, FakeFetch())
        self.assertEqual(len(outcome.cache), 12)
        self.assertEqual(outcome.cache[("apt", "12210", "202611")].fetched, "2026-11-02")
        self.assertEqual(outcome.stats["apt"].fetched, 12)
        self.assertEqual(outcome.budget.used_of("apt"), 12)
        self.assertIsNone(backfill.failure_problem(outcome))

    def test_land_cap_stops_and_continues_the_next_day_refresh_first(self):
        caps = {"land": 30}
        pairs = {("land", f"{41000 + i}"): 10 - i for i in range(5)}     # 5 pairs x 12 = 60 months
        day1 = run(backfill.plan(pairs, WINDOW, set()), FakeFetch(), spent=budget(caps=caps))
        self.assertEqual(day1.stats["land"].blocked, "cap")
        self.assertEqual(len(day1.cache), 30)
        refreshed = {slot for slot in day1.cache if slot[2] in WINDOW[:2]}
        self.assertEqual(len(refreshed), 10)                    # every current + previous month first
        self.assertEqual({slot[1] for slot in day1.cache if slot[2] not in WINDOW[:2]}, {"41000", "41001"})
        fetch = FakeFetch()
        day2 = run(backfill.plan(pairs, WINDOW, set(day1.cache)), fetch, cache=day1.cache,
                   spent=budget(day="2026-11-03", caps=caps), today="2026-11-03")
        self.assertEqual(fetch.calls[:10], [("land", lawd, ym) for lawd in sorted(
            (p[1] for p in pairs), key=lambda code: -pairs[("land", code)]) for ym in WINDOW[:2]])
        self.assertEqual(len(day2.cache), 50)
        self.assertEqual(day2.cache[("land", "41000", "202611")].fetched, "2026-11-03")   # refreshed
        self.assertEqual(day2.cache[("land", "41000", "202609")].fetched, "2026-11-02")   # not fetched again
        self.assertEqual({slot[1] for slot in day2.cache if slot[2] not in WINDOW[:2]},
                         {"41000", "41001", "41002", "41003"})

    def test_highest_item_count_is_backfilled_first(self):
        pairs = {("rh", "11500"): 85, ("rh", "11470"): 26}
        fetch = FakeFetch()
        run(backfill.plan(pairs, WINDOW, set()), fetch, spent=budget(caps={"rh": 14}))
        older = [call for call in fetch.calls if call[2] not in WINDOW[:2]]
        self.assertEqual({call[1] for call in older}, {"11500"})
        self.assertEqual([call[2] for call in older], list(WINDOW[2:12]))

    def test_failed_month_keeps_the_previous_cache(self):
        old = Month("apt", "12210", "202610", "2026-10-31", ({**ROW, "d": 9},))
        tasks = backfill.plan({("apt", "12210"): 1}, WINDOW, {old.slot})
        outcome = run(tasks, FakeFetch(fail={old.slot}), cache={old.slot: old})
        self.assertEqual(outcome.cache[old.slot], old)
        self.assertEqual(outcome.stats["apt"].failed, 1)

    def test_more_than_10_percent_failed_months_is_a_problem(self):
        tasks = backfill.plan({("apt", "12210"): 1}, WINDOW, set())       # 12 months
        one = run(tasks, FakeFetch(fail={("apt", "12210", "202611")}))
        self.assertIsNone(backfill.failure_problem(one))                 # 1/12 = 8.3%
        two = run(tasks, FakeFetch(fail={("apt", "12210", "202611"), ("apt", "12210", "202610")}))
        # two failures pass the failure budget (10% of 12 planned): the API stops early and the run fails
        self.assertIn("apt: 2 of 2", backfill.failure_problem(two))
        self.assertEqual(two.stats["apt"].deferred, 10)

    def test_exactly_10_percent_passes(self):
        pairs = {("apt", "12210"): 2, ("rh", "12210"): 1}
        tasks = backfill.plan(pairs, rtms_cache.window(TODAY, 5), set())   # 10 months
        outcome = run(tasks, FakeFetch(fail={("apt", "12210", "202611")}))
        self.assertIsNone(backfill.failure_problem(outcome))

    def test_quota_code_blocks_the_api_without_counting_failures(self):
        tasks = backfill.plan({("land", "41461"): 1, ("apt", "12210"): 1}, WINDOW, set())
        fetch = FakeFetch(quota={"land"})
        outcome = run(tasks, fetch)
        self.assertEqual(outcome.stats["land"].blocked, "quota")
        self.assertEqual(sum(1 for call in fetch.calls if call[0] == "land"), 1)
        self.assertEqual(outcome.stats["apt"].fetched, 12)
        self.assertIsNone(backfill.failure_problem(outcome))

    def test_deadline_stops_backfill_but_not_refresh(self):
        ticks = iter(range(1000, 100_000, 1000))     # one clock reading per task: 1000, 2000, ...
        tasks = backfill.plan({("apt", "12210"): 1}, WINDOW, set())
        outcome = run(tasks, FakeFetch(), clock=lambda: next(ticks), deadline=3500, stage=10**9)
        # refresh at 1000 and 2000, one backfill month at 3000, the rest after the backfill deadline
        self.assertEqual(sorted(slot[2] for slot in outcome.cache), ["202609", "202610", "202611"])
        self.assertEqual(outcome.stats["apt"].deferred, 9)

    def test_stage_deadline_defers_refresh_too_and_keeps_yesterday(self):
        old = Month("apt", "12210", "202611", "2026-11-01", (ROW,))
        tasks = backfill.plan({("apt", "12210"): 1}, WINDOW, {old.slot})
        fetch = FakeFetch()
        outcome = run(tasks, fetch, cache={old.slot: old}, clock=lambda: 5000.0)   # past both deadlines
        self.assertEqual(fetch.calls, [])
        self.assertEqual(outcome.cache, {old.slot: old})
        self.assertEqual((outcome.stats["apt"].deferred, outcome.stats["apt"].failed), (12, 0))
        self.assertIsNone(backfill.failure_problem(outcome))

    def test_failure_budget_cancels_every_api(self):
        cancel = threading.Event()
        apt_calls = []

        def fetch_for(api):
            if api == "rh":
                return FakeFetch(fail={("rh", "11500", ym) for ym in WINDOW})
            def waiting(task, spent):                       # holds until the cancel event is set
                cancel.wait(5)
                apt_calls.append(task)
                return MonthResult("ok", (ROW,), spent.spend("apt"))
            return waiting
        tasks = backfill.plan({("apt", "12210"): 2, ("rh", "11500"): 1}, WINDOW, set())   # 24 planned -> budget 2.4
        outcome = run(tasks, None, fetch_for=fetch_for, cancel=cancel)
        self.assertTrue(cancel.is_set())
        self.assertEqual(outcome.stats["rh"].failed, 3)                       # stopped at the third failure
        self.assertEqual(len(apt_calls), 1)                                    # the other thread stops too
        self.assertIn("rh", backfill.failure_problem(outcome))

    def test_exception_in_one_api_stops_the_others_and_is_raised(self):
        cancel = threading.Event()
        apt_calls = []

        def fetch_for(api):
            if api == "rh":
                def broken(task, spent):
                    raise RuntimeError("bug")
                return broken
            def waiting(task, spent):
                cancel.wait(5)
                apt_calls.append(task)
                return MonthResult("ok", (ROW,), spent.spend("apt"))
            return waiting
        tasks = backfill.plan({("apt", "12210"): 2, ("rh", "11500"): 1}, WINDOW, set())
        with self.assertRaises(RuntimeError):
            run(tasks, None, fetch_for=fetch_for, cancel=cancel)
        self.assertEqual(len(apt_calls), 1)

    def test_auth_error_stops_the_api_and_counts_as_failed(self):
        class Auth(FakeFetch):
            def __call__(self, task, spent):
                self.calls.append(task)
                return MonthResult("auth", (), spent.spend(task.api), "x failed: HTTP 403, resultCode 30")
        tasks = backfill.plan({("rh", "11500"): 1, ("apt", "12210"): 9}, WINDOW, set())
        fetch_for = lambda api: Auth() if api == "rh" else FakeFetch()      # noqa: E731
        outcome = run(tasks, None, fetch_for=fetch_for)
        self.assertEqual((outcome.stats["rh"].failed, outcome.stats["rh"].blocked), (1, "auth"))
        self.assertIn("rh: 1 of 1", backfill.failure_problem(outcome))         # 1 of 13 overall, but all of rh

    def test_more_than_half_of_one_api_failing_fails_the_run(self):
        stats = {api: backfill.ApiStats() for api in backfill.RTMS_APIS}
        ok = backfill.Outcome({}, budget(), {**stats, "apt": backfill.ApiStats(fetched=100),
                                              "rh": backfill.ApiStats(fetched=1, failed=1)}, ())
        self.assertIsNone(backfill.failure_problem(ok))                        # 1 of 2 = half
        bad = backfill.Outcome({}, budget(), {**stats, "apt": backfill.ApiStats(fetched=100),
                                               "rh": backfill.ApiStats(fetched=1, failed=2)}, ())
        self.assertIn("rh: 2 of 3", backfill.failure_problem(bad))

    def test_state_document(self):
        tasks = backfill.plan({("apt", "12210"): 1}, WINDOW, set())
        outcome = run(tasks, FakeFetch(fail={("apt", "12210", "202512")}))
        state = backfill.state(outcome, {("apt", "12210"): 1}, WINDOW, TODAY.isoformat(),
                               {("apt", "12210"): "2026-11-02", ("rh", "11500"): "2026-10-25"})
        self.assertEqual(state["window"], list(WINDOW))
        self.assertEqual(state["apis"]["apt"], {"pairs": 1, "months": 12, "cached": 11, "fetched": 11,
                                                "failed": 1, "deferred": 0, "blocked": None})
        self.assertEqual(state["apis"]["land"]["pairs"], 0)
        self.assertEqual(state["pairsLastSeen"], {"apt/12210": "2026-11-02", "rh/11500": "2026-10-25"})


class RetentionTest(unittest.TestCase):
    """Review MEDIUM-3: a pair keeps its months for 14 days after its last item."""

    def test_today_pairs_are_seen_today_and_gone_pairs_keep_their_date_for_14_days(self):
        previous = {"pairsLastSeen": {"apt/12210": "2026-10-19", "rh/11500": "2026-10-18", "land/41461": "2026-11-01"}}
        seen = backfill.last_seen(previous, {("offi", "11500"): 1, ("land", "41461"): 2}, TODAY)
        self.assertEqual(seen, {("apt", "12210"): "2026-10-19",          # 14 days ago: kept
                                ("land", "41461"): "2026-11-02", ("offi", "11500"): "2026-11-02"})

    def test_unreadable_previous_state_is_ignored(self):
        for previous in (None, [], {"pairsLastSeen": []}, {"pairsLastSeen": {"x": "2026-11-01"}},
                         {"pairsLastSeen": {"apt/12210": "yesterday"}}, {"pairsLastSeen": {"car/12210": "2026-11-01"}},
                         {"pairsLastSeen": {"apt/12210": "2026-12-01"}}):
            self.assertEqual(backfill.last_seen(previous, {}, TODAY), {}, previous)


if __name__ == "__main__":
    unittest.main()
