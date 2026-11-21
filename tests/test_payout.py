"""Tests for the weekly payout ledger. No network or keys needed."""
import json
import unittest
from datetime import date, datetime
from pathlib import Path

from agent import payout
from agent.payout import (Delivery, build_ledger, find_price, build_price_index,
                          is_paid, iso_week, ledger_summary, load_export,
                          load_issue_labels, load_prices, parse_deliveries)

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "sample_data"


def sample():
    deliveries = parse_deliveries(load_export(SAMPLE / "deliveries.json"))
    prices = load_prices(SAMPLE / "tasks.json")
    labels = load_issue_labels(SAMPLE / "issues.json")
    return deliveries, prices, labels


class WeekTests(unittest.TestCase):
    def test_iso_week_uses_iso_year(self):
        self.assertEqual(iso_week(date(2027, 1, 1)), "2026-W53")
        self.assertEqual(iso_week(date(2027, 1, 6)), "2027-W01")
        self.assertEqual(iso_week(datetime.fromisoformat("2027-01-04T08:00:00+00:00")), "2027-W01")
        self.assertEqual(iso_week("2027-W09"), "2027-W09")
        self.assertEqual(iso_week(""), "")


class SampleDataTests(unittest.TestCase):
    def test_ledger_correct_for_sample_data(self):
        deliveries, prices, labels = sample()
        ledger = build_ledger(deliveries, prices, issue_labels=labels)

        self.assertEqual(len(ledger.entries), 3)
        by_issue = {(e.repo, e.issue): e for e in ledger.entries}
        self.assertEqual(by_issue[("ADT-community/trans_sport", 7)].contributor, "alice")
        self.assertEqual(by_issue[("ADT-community/trans_sport", 7)].status, payout.PAID)
        self.assertEqual(by_issue[("ADT-community/trans_sport", 7)].amount, 20.0)
        self.assertEqual(by_issue[("ADT-community/locallity_ai", 7)].contributor, "bob")
        self.assertEqual(by_issue[("ADT-community/locallity_ai", 7)].status, payout.OWED)
        self.assertEqual(by_issue[("ADT-community/content_feed", 9)].amount, 75.0)
        self.assertTrue(all(e.week == "2027-W01" for e in ledger.entries))
        self.assertEqual(ledger.total_owed(), 95.0)
        self.assertEqual(ledger.total_owed("2027-W02"), 0.0)

        self.assertEqual([e for e in ledger.entries if e.issue == 11], [])
        self.assertEqual(len(ledger.unpriced), 1)
        self.assertEqual(ledger.unpriced[0]["issue"], 10)
        self.assertEqual(ledger.conflicts, [])

    def test_paid_items_never_listed_as_owed(self):
        deliveries, prices, labels = sample()
        ledger = build_ledger(deliveries, prices, issue_labels=labels)
        summary = ledger_summary(ledger, "2027-W01")

        owed_part = summary.split("## Owed", 1)[1].split("## Already paid", 1)[0]
        self.assertNotIn("trans_sport", owed_part)
        self.assertNotIn("alice", owed_part)
        self.assertIn("@bob", owed_part)
        self.assertIn("@carol", owed_part)
        self.assertIn("$95.00", owed_part)
        self.assertNotIn("$115.00", summary)
        paid_part = summary.split("## Already paid", 1)[1]
        self.assertIn("@alice", paid_part)
        self.assertIn("not due", paid_part)

    def test_summary_for_a_week_with_nothing_owed(self):
        deliveries, prices, labels = sample()
        ledger = build_ledger(deliveries, prices, issue_labels=labels)
        summary = ledger_summary(ledger, "2027-W02")
        self.assertIn("Nothing owed", summary)
        self.assertIn("$0.00", summary)

    def test_detailed_summary_shows_statuses(self):
        deliveries, prices, labels = sample()
        ledger = build_ledger(deliveries, prices, issue_labels=labels)
        summary = ledger_summary(ledger, "2027-W01", detailed=True)
        self.assertIn("| Status |", summary)
        self.assertIn("| paid |", summary)
        self.assertIn("| owed |", summary)


class AcceptanceCaseTests(unittest.TestCase):
    def test_two_repos_with_issue_7_total_40_then_20_when_one_paid(self):
        deliveries = [
            Delivery("ADT-community/one", 7, "alice", "2027-W01", "t1"),
            Delivery("ADT-community/two", 7, "bob", "2027-W01", "t2"),
        ]
        prices = {"ADT-community/one#7": 20, "ADT-community/two#7": 20}
        unpaid = build_ledger(deliveries, prices)
        self.assertEqual(unpaid.total_owed(), 40.0)

        paid = build_ledger(deliveries, prices,
                            issue_labels={"ADT-community/one#7": ["paid"]})
        self.assertEqual(paid.total_owed(), 20.0)
        self.assertEqual(len(paid.entries), 2)
        self.assertEqual([e.contributor for e in paid.paid], ["alice"])
        self.assertEqual([e.contributor for e in paid.owed], ["bob"])

    def test_paid_label_is_case_insensitive(self):
        self.assertTrue(is_paid(["Bounty:$20", "Paid"]))
        self.assertFalse(is_paid(["bounty:$20"]))
        lookup = lambda repo, issue: {"paid"} if issue == 7 else set()
        ledger = build_ledger([Delivery("o/r", 7, "a", "2027-W01"),
                               Delivery("o/r", 8, "b", "2027-W01")], {"o/r#7": 5, "o/r#8": 5},
                              issue_labels=lookup)
        self.assertEqual([e.status for e in ledger.entries], [payout.PAID, payout.OWED])

    def test_conflicting_claims_are_flagged_not_paid(self):
        deliveries = [
            Delivery("o/r", 7, "alice", "2027-W01"),
            Delivery("o/r", 7, "bob", "2027-W01"),
        ]
        ledger = build_ledger(deliveries, {"o/r#7": 20})
        self.assertEqual(ledger.entries, [])
        self.assertEqual(len(ledger.conflicts), 1)
        self.assertIn("human", ledger.conflicts[0]["reason"])
        self.assertIn("@bob", ledger_summary(ledger))

    def test_same_contributor_claiming_twice_is_paid_once(self):
        deliveries = [
            Delivery("o/r", 7, "alice", "2027-W01"),
            Delivery("o/r", 7, "@alice", "2027-W01"),
            Delivery("o/r", 7, "Alice", "2027-W01"),
        ]
        ledger = build_ledger(deliveries, {"o/r#7": 20})
        self.assertEqual(len(ledger.entries), 1)
        self.assertEqual(ledger.total_owed(), 20.0)

    def test_missing_price_is_flagged_not_invented(self):
        ledger = build_ledger([Delivery("o/r", 7, "alice", "2027-W01")], {})
        self.assertEqual(ledger.entries, [])
        self.assertEqual(len(ledger.unpriced), 1)
        self.assertIn("needs a price", ledger_summary(ledger).lower())


class ParsingTests(unittest.TestCase):
    def test_only_approved_deliveries_count(self):
        records = [
            {"repo": "o/r", "issue": 1, "contributor": "a", "status": "approved", "delivered_at": "2027-01-06"},
            {"repo": "o/r", "issue": 2, "contributor": "b", "status": "rejected", "delivered_at": "2027-01-06"},
            {"repo": "o/r", "issue": 3, "contributor": "c", "approved": True, "date": "2027-01-06"},
        ]
        parsed = parse_deliveries(records)
        self.assertEqual([d.issue for d in parsed], [1, 3])
        self.assertTrue(all(d.week == "2027-W01" for d in parsed))

    def test_issue_number_and_repo_from_url(self):
        records = [{"issue_url": "https://github.com/o/r/issues/42", "repository": "o/r.git",
                    "github_login": "zoe", "status": "approved", "week": "2027-W03"}]
        d = parse_deliveries(records)[0]
        self.assertEqual((d.repo, d.issue, d.contributor, d.week), ("o/r", 42, "zoe", "2027-W03"))

    def test_price_index_accepts_all_task_json_shapes(self):
        for prices in (
            {"o/r#7": 20},
            {"o/r:7": 20},
            {"t-7": 20},
            {"tasks": [{"id": "t-7", "repo": "o/r", "issue": 7, "bounty_usd": 20}]},
            [{"id": "t-7", "repo": "o/r", "issue": 7, "price_usd": "$20"}],
        ):
            index = build_price_index(prices)
            self.assertEqual(find_price(index, "o/r", 7, "t-7"), 20.0, prices)

    def test_task_without_a_price_is_flagged_not_paid_zero(self):
        prices = {"tasks": [{"id": "t-7", "repo": "o/r", "issue": 7}]}
        ledger = build_ledger([Delivery("o/r", 7, "alice", "2027-W01", "t-7")], prices)
        self.assertEqual(ledger.entries, [])
        self.assertEqual(len(ledger.unpriced), 1)

    def test_prices_can_be_keyed_by_contributor_task_id(self):
        ledger = build_ledger([Delivery("o/r", 7, "a", "2027-W01", "ag-7")], {"ag-7": 25})
        self.assertEqual(ledger.total_owed(), 25.0)

    def test_csv_export(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "deliveries.csv"
            path.write_text("repo,issue,contributor,status,delivered_at\n"
                            "o/r,7,alice,approved,2027-01-06\n", encoding="utf-8")
            rows = load_export(path)
        self.assertEqual(rows[0]["repo"], "o/r")
        self.assertEqual(parse_deliveries(rows)[0].issue, 7)

    def test_ensure_paid_label(self):
        calls = []
        class GH:
            def ensure_label(self, repo, name, color, description=""):
                calls.append((repo, name, color, description))
        payout.ensure_paid_label(GH(), "o/r")
        self.assertEqual(calls[0][0:2], ("o/r", "paid"))


class CliTests(unittest.TestCase):
    def test_payout_command_works_offline(self):
        import argparse
        import contextlib
        import io
        from agent.__main__ import cmd_payout

        args = argparse.Namespace(
            deliveries=str(SAMPLE / "deliveries.json"),
            prices=str(SAMPLE / "tasks.json"),
            issues=str(SAMPLE / "issues.json"),
            tasks_repo="ADT-community/ADT-community.github.io",
            week="2027-W01", all_weeks=False, format="summary",
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cmd_payout(args)
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("$95.00", text)
        self.assertIn("@bob", text)
        self.assertEqual(text.count("@alice"), 1)
        self.assertIn("Already paid", text)


if __name__ == "__main__":
    unittest.main()
