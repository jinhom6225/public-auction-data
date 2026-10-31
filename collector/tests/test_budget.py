import unittest

import netguard  # noqa: F401  (blocks the network)

from collector.budget import Budget
from collector.errors import BudgetExceeded


class BudgetTest(unittest.TestCase):
    def test_spend_returns_new_budget(self):
        start = Budget.load(None, "2026-11-01")
        after = start.spend("onbid")
        self.assertEqual(start.used_of("onbid"), 0)
        self.assertEqual(after.used_of("onbid"), 1)

    def test_stops_at_the_daily_cap(self):
        budget = Budget.load({"date": "2026-11-01", "used": {"onbid": 899}}, "2026-11-01")
        budget = budget.spend("onbid")
        with self.assertRaises(BudgetExceeded):
            budget.spend("onbid")

    def test_can_spend_checks_ahead(self):
        budget = Budget.load({"date": "2026-11-01", "used": {"onbid": 860}}, "2026-11-01")
        self.assertTrue(budget.can_spend("onbid", 40))
        self.assertFalse(budget.can_spend("onbid", 41))

    def test_new_kst_day_resets(self):
        budget = Budget.load({"date": "2026-10-31", "used": {"onbid": 900}}, "2026-11-01")
        self.assertEqual(budget.used_of("onbid"), 0)

    def test_unknown_api_is_refused(self):
        with self.assertRaises(KeyError):
            Budget.load(None, "2026-11-01").spend("nope")

    def test_round_trip(self):
        budget = Budget.load(None, "2026-11-01").spend("onbid").spend("onbid")
        self.assertEqual(budget.to_json(), {"date": "2026-11-01", "used": {"onbid": 2}})

    def test_bad_saved_state_starts_fresh_but_never_negative(self):
        self.assertEqual(Budget.load({"date": "2026-11-01", "used": {"onbid": -5}}, "2026-11-01").used_of("onbid"), 0)
        self.assertEqual(Budget.load("garbage", "2026-11-01").used_of("onbid"), 0)


if __name__ == "__main__":
    unittest.main()
