import datetime
import unittest

import netguard  # noqa: F401  (blocks the network)
from fakes import raw_row

from collector import rounds
from collector.config import KST

NOW = datetime.datetime(2026, 11, 3, 12, 0, tzinfo=KST)
READY, OPEN, CLOSED = "0001", "0002", "0003"


def row(cdtn, start, end, status=READY, price="1000", fails=0):
    return raw_row(pbctCdtnNo=cdtn, cltrBidBgngDt=start, cltrBidEndDt=end, pbctStatCd=status,
                   lowstBidPrcIndctCont=price, usbdNft=fails)


class PickTest(unittest.TestCase):
    def test_open_round_is_representative(self):
        rows = [
            row(3, "202611161000", "202611181700", price="800"),
            row(1, "202611021000", "202611041700", OPEN, price="1000", fails=0),
            row(2, "202611091000", "202611111700", price="900", fails=1),
        ]
        picked = rounds.pick(rows, NOW)
        self.assertEqual(picked.representative["pbctCdtnNo"], 1)
        self.assertEqual([r["pbctCdtnNo"] for r in picked.upcoming], [2, 3])

    def test_without_open_round_the_nearest_ready_one(self):
        rows = [row(5, "202611161000", "202611181700"), row(4, "202611091000", "202611111700")]
        self.assertEqual(rounds.pick(rows, NOW).representative["pbctCdtnNo"], 4)

    def test_closed_rows_are_never_representative(self):
        self.assertIsNone(rounds.pick([row(1, "202610261000", "202610281700", CLOSED)], NOW))
        picked = rounds.pick([row(1, "202610261000", "202610281700", CLOSED),
                              row(2, "202611091000", "202611111700")], NOW)
        self.assertEqual(picked.representative["pbctCdtnNo"], 2)

    def test_rounds_whose_end_has_passed_are_skipped(self):
        rows = [row(1, "202610261000", "202611021700"), row(2, "202611091000", "202611111700")]
        self.assertEqual(rounds.pick(rows, NOW).representative["pbctCdtnNo"], 2)
        self.assertIsNone(rounds.pick([row(1, "202610261000", "202611021700", OPEN)], NOW))

    def test_placeholder_dates_sort_last(self):
        rows = [row(1, "299912301000", "299912301600"), row(2, "202611091000", "202611111700")]
        picked = rounds.pick(rows, NOW)
        self.assertEqual(picked.representative["pbctCdtnNo"], 2)
        self.assertEqual([r["pbctCdtnNo"] for r in picked.upcoming], [1])

    def test_placeholder_only_item_is_kept(self):
        picked = rounds.pick([row(1, "299912301000", "299912301600")], NOW)
        self.assertEqual(picked.representative["pbctCdtnNo"], 1)

    def test_at_most_three_upcoming(self):
        rows = [row(i, f"2026112{i}1000", f"2026112{i}1700") for i in range(1, 7)]
        picked = rounds.pick(rows, NOW)
        self.assertEqual(picked.representative["pbctCdtnNo"], 1)
        self.assertEqual(len(picked.upcoming), 3)

    def test_ties_are_broken_by_condition_number(self):
        rows = [row(9, "202611091000", "202611111700"), row(7, "202611091000", "202611111700")]
        self.assertEqual(rounds.pick(rows, NOW).representative["pbctCdtnNo"], 7)

    def test_genuinely_tied_rows_pick_the_same_row_in_any_order(self):
        a = row(7, "202611091000", "202611111700", price="900")
        b = row(7, "202611091000", "202611111700", price="800")
        first = rounds.pick([a, b], NOW).representative
        second = rounds.pick([b, a], NOW).representative
        self.assertEqual(first, second)

    def test_one_sided_placeholder_in_next_rounds_is_fully_null(self):
        rows = [row(1, "202611091000", "202611111700"), row(2, "202611161000", "299912301600")]
        self.assertEqual(rounds.next_rounds(rounds.pick(rows, NOW)),
                         [{"bidStart": None, "bidEnd": None, "lowestBid": 1000}])

    def test_unknown_status_is_ignored(self):
        self.assertIsNone(rounds.pick([row(1, "202611091000", "202611111700", "0009")], NOW))

    def test_next_rounds_record(self):
        rows = [row(1, "202611091000", "202611111700"), row(2, "202611161000", "202611181700", price="비공개")]
        picked = rounds.pick(rows, NOW)
        self.assertEqual(rounds.next_rounds(picked), [
            {"bidStart": "2026-11-16T10:00:00+09:00", "bidEnd": "2026-11-18T17:00:00+09:00", "lowestBid": None},
        ])


class GroupTest(unittest.TestCase):
    def test_groups_by_item_key_in_order(self):
        rows = [raw_row(key="B"), raw_row(key="A"), raw_row(key="B")]
        groups = rounds.group_by_key(rows)
        self.assertEqual(list(groups), ["A", "B"])
        self.assertEqual(len(groups["B"]), 2)

    def test_rows_without_a_key_are_left_out(self):
        self.assertEqual(rounds.group_by_key([raw_row(key=None), raw_row(key="")]), {})


if __name__ == "__main__":
    unittest.main()
