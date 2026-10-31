import datetime
import unittest

import netguard  # noqa: F401  (blocks the network)

from collector import seen_history

TODAY = datetime.date(2026, 11, 10)


def rec(key, bid):
    return {"key": key, "lowestBid": bid}


def entry(first="2026-11-01", last_bid=100, drop=None, prev=None, last_seen="2026-11-09"):
    return {"firstSeen": first, "lastLowestBid": last_bid, "priceDropAt": drop,
            "prevLowestBid": prev, "lastSeen": last_seen}


class HistoryTest(unittest.TestCase):
    def test_first_run_marks_everything_new_without_drops(self):
        records, seen = seen_history.apply([rec("A", 100), rec("B", None)], None, TODAY)
        for record in records:
            self.assertEqual(record["firstSeen"], "2026-11-10")
            self.assertIsNone(record["priceDropAt"])
            self.assertIsNone(record["prevLowestBid"])
        self.assertEqual(seen["A"], entry("2026-11-10", 100, None, None, "2026-11-10"))

    def test_new_item(self):
        records, _ = seen_history.apply([rec("N", 50)], {"A": entry()}, TODAY)
        self.assertEqual(records[0]["firstSeen"], "2026-11-10")

    def test_price_drop(self):
        records, seen = seen_history.apply([rec("A", 90)], {"A": entry()}, TODAY)
        self.assertEqual(records[0]["firstSeen"], "2026-11-01")
        self.assertEqual(records[0]["priceDropAt"], "2026-11-10")
        self.assertEqual(records[0]["prevLowestBid"], 100)
        self.assertEqual(seen["A"]["lastLowestBid"], 90)

    def test_same_price_keeps_the_earlier_drop(self):
        old = {"A": entry(last_bid=90, drop="2026-11-08", prev=100)}
        records, _ = seen_history.apply([rec("A", 90)], old, TODAY)
        self.assertEqual((records[0]["priceDropAt"], records[0]["prevLowestBid"]), ("2026-11-08", 100))

    def test_price_rise_is_not_a_drop_and_keeps_drop_date(self):
        old = {"A": entry(last_bid=90, drop="2026-11-08", prev=100)}
        records, seen = seen_history.apply([rec("A", 120)], old, TODAY)
        self.assertEqual(records[0]["priceDropAt"], "2026-11-08")
        self.assertEqual(seen["A"]["lastLowestBid"], 120)

    def test_private_price_keeps_the_last_known_number(self):
        records, seen = seen_history.apply([rec("A", None)], {"A": entry()}, TODAY)
        self.assertIsNone(records[0]["priceDropAt"])
        self.assertEqual(seen["A"]["lastLowestBid"], 100)
        records, _ = seen_history.apply([rec("A", 80)], seen, TODAY + datetime.timedelta(days=1))
        self.assertEqual(records[0]["prevLowestBid"], 100)

    def test_reappearing_within_90_days_keeps_first_seen(self):
        old = {"A": entry(first="2026-07-01", last_seen="2026-08-12")}   # 90 days before
        records, _ = seen_history.apply([rec("A", 100)], old, TODAY)
        self.assertEqual(records[0]["firstSeen"], "2026-07-01")

    def test_reappearing_after_91_days_is_new(self):
        old = {"A": entry(first="2026-07-01", last_seen="2026-08-11", drop="2026-08-01", prev=200)}
        records, _ = seen_history.apply([rec("A", 50)], old, TODAY)
        self.assertEqual(records[0]["firstSeen"], "2026-11-10")
        self.assertIsNone(records[0]["priceDropAt"])
        self.assertIsNone(records[0]["prevLowestBid"])

    def test_pruning_after_90_days(self):
        old = {"KEEP": entry(last_seen="2026-08-12"), "GONE": entry(last_seen="2026-08-11")}
        _, seen = seen_history.apply([], old, TODAY)
        self.assertEqual(sorted(seen), ["KEEP"])
        self.assertEqual(seen["KEEP"]["lastSeen"], "2026-08-12")

    def test_inputs_are_not_mutated(self):
        old = {"A": entry()}
        record = rec("A", 90)
        seen_history.apply([record], old, TODAY)
        self.assertEqual(old, {"A": entry()})
        self.assertEqual(record, rec("A", 90))

    def test_parse_and_render(self):
        self.assertEqual(seen_history.parse({"schemaVersion": 1, "items": {"A": entry()}}), {"A": entry()})
        for bad in (None, [], {"schemaVersion": 2, "items": {}}, {"schemaVersion": 1, "items": {"A": {}}}):
            with self.assertRaises(ValueError):
                seen_history.parse(bad)
        self.assertEqual(seen_history.render({"A": entry()}), {"schemaVersion": 1, "items": {"A": entry()}})


if __name__ == "__main__":
    unittest.main()
