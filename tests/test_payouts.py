"""Tests for the payout ledger. No network or keys needed."""
import json
import unittest
from datetime import date
from pathlib import Path

from agent import payouts
from agent.payouts import (PAID_LABEL, SUMMARY_LABEL, Ledger, build_ledger, load_tasks,
                           mask_phone, next_payout_date, parse_records, pay_date_for,
                           payout_week, run_payouts, summary_markdown, ledger_markdown)

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "samples" / "adt-work-export.sample.json"

# The entries from the website's tasks.json that the sample export refers to.
TASKS = {
    "ts-8": {"id": "ts-8", "repo": "trans_sport", "issue": 8, "reward": 20, "paid": True,
             "title": "Mobile money fares in a sandbox"},
    "ts-13": {"id": "ts-13", "repo": "trans_sport", "issue": 13, "reward": 20, "paid": True,
              "title": "Figma: design the driver app"},
    "ops-5": {"id": "ops-5", "repo": "adt-community.github.io", "issue": 5, "reward": 20,
              "paid": True, "title": "Community set-up: Discord and weekly updates"},
    "la-3": {"id": "la-3", "repo": "locallity_ai", "issue": 3, "reward": 20, "paid": True,
             "title": "Language research: pick our first language"},
    "la-29": {"id": "la-29", "repo": "locallity_ai", "issue": 29, "reward": 0, "paid": False,
              "title": "WhatsApp channel"},
    "qt-7": {"id": "qt-7", "repo": "trader", "issue": 7, "reward": 20, "paid": True,
             "title": "Figma: design the investing app"},
    "cf-4": {"id": "cf-4", "repo": "content_feed", "issue": 4, "reward": 20, "paid": True,
             "title": "Backend v1: accounts, profiles and follows"},
}


def claim(task, phone, name, github="", **extra):
    return dict({"type": "claim", "task": task, "phone": phone, "name": name,
                 "github": github}, **extra)


def delivery(task, phone, approved_at=None, **extra):
    rec = {"type": "delivery", "task": task, "phone": phone, "stage": "final",
           "work_link": "https://example.com/work"}
    if approved_at:
        rec.update({"approved": "yes", "approved_at": approved_at})
    rec.update(extra)
    return rec


class FakeGitHub:
    """Stands in for the GitHub API: issues with labels, in memory."""

    def __init__(self, issues=None):
        self._issues = issues or {}
        self.created = []
        self.labels = []

    def issues(self, full):
        return self._issues.get(full, [])

    def ensure_label(self, full, name, color, description=""):
        self.labels.append((full, name))

    def create_issue(self, full, title, body, labels):
        self.created.append({"repo": full, "title": title, "body": body, "labels": labels})
        self._issues.setdefault(full, []).append({"number": 900 + len(self.created),
                                                  "title": title, "state": "open", "labels": []})
        return {"html_url": f"https://github.com/{full}/issues/{900 + len(self.created)}"}


class SampleLedgerTests(unittest.TestCase):
    """The ledger is correct for the sample data."""

    def setUp(self):
        self.records = parse_records(SAMPLE.read_text(encoding="utf-8"))
        self.ledger = build_ledger(self.records, TASKS)

    def test_one_line_per_approved_task_in_payout_order(self):
        rows = [(e.task, e.week, e.pay_date.isoformat(), e.repo, e.issue, e.contributor,
                 e.amount, e.status) for e in self.ledger.entries]
        self.assertEqual(rows, [
            ("ops-5", "2026-W47", "2027-01-06", "ADT-community/adt-community.github.io", 5,
             "Kwame Mensah", 20, "owed"),
            ("qt-7", "2026-W53", "2027-01-06", "ADT-community/trader", 7,
             "Thandiwe Ncube", 20, "owed"),
            ("ts-13", "2026-W53", "2027-01-06", "ADT-community/trans_sport", 13,
             "Amara Okafor", 20, "owed"),
            ("la-3", "2027-W01", "2027-01-13", "ADT-community/locallity_ai", 3,
             "Fatou Diop", 20, "owed"),
        ])
        self.assertEqual(sum(e.amount for e in self.ledger.owed()), 80)

    def test_a_resubmitted_delivery_is_paid_once_for_the_first_approval(self):
        ts13 = [e for e in self.ledger.entries if e.task == "ts-13"]
        self.assertEqual(len(ts13), 1)
        self.assertEqual(ts13[0].approved_on, date(2026, 12, 30))   # not the 2027-01-05 redo

    def test_progress_updates_and_unreviewed_work_are_not_owed(self):
        tasks_on_ledger = {e.task for e in self.ledger.entries}
        self.assertNotIn("ts-8", tasks_on_ledger)    # a progress update, not a delivery
        self.assertNotIn("cf-4", tasks_on_ledger)    # delivered but not approved yet
        self.assertNotIn("ts-8", [s["task"] for s in self.ledger.skipped])

    def test_unpriced_work_is_listed_separately_not_as_money_owed(self):
        reasons = {s["task"]: s["reason"] for s in self.ledger.skipped}
        self.assertIn("no reward", reasons["la-29"])       # a next-wave task, nothing owed
        self.assertIn("tasks.json", reasons["zz-99"])      # no such task, so no price
        self.assertEqual(reasons.keys(), {"la-29", "zz-99"})

    def test_work_approved_before_the_first_run_is_paid_in_it(self):
        ops5 = next(e for e in self.ledger.entries if e.task == "ops-5")
        self.assertEqual(ops5.approved_on, date(2026, 11, 20))
        self.assertEqual(ops5.week_start, date(2026, 11, 16))
        self.assertEqual(ops5.pay_date, payouts.FIRST_PAYOUT)

    def test_contributor_comes_from_the_claim_form(self):
        ts13 = next(e for e in self.ledger.entries if e.task == "ts-13")
        self.assertEqual(ts13.github, "amaraok")              # the delivery form has no name
        self.assertEqual(ts13.phone, "+2348012345678")        # spaces in the delivery normalised
        self.assertEqual(next(e for e in self.ledger.entries if e.task == "la-3").github, "")

    def test_no_warnings_for_clean_sample_data(self):
        self.assertEqual(self.ledger.warnings, [])


class PaidTests(unittest.TestCase):
    def setUp(self):
        self.ledger = build_ledger(parse_records(SAMPLE.read_text(encoding="utf-8")), TASKS)

    def test_a_paid_label_means_never_owed_again(self):
        payouts.mark_paid(self.ledger, {("ADT-community/trader", 7)})
        qt7 = next(e for e in self.ledger.entries if e.task == "qt-7")
        self.assertEqual(qt7.status, "paid")
        self.assertNotIn(qt7, self.ledger.owed())
        self.assertNotIn(qt7, self.ledger.due_by(date(2027, 1, 6)))
        self.assertEqual(sum(e.amount for e in self.ledger.owed()), 60)

    def test_paid_labels_are_read_from_github(self):
        gh = FakeGitHub({"ADT-community/trader": [
            {"number": 7, "title": "Figma", "labels": [{"name": "bounty:$20"}, {"name": "Paid"}]},
            {"number": 8, "title": "Other", "labels": [{"name": "bug"}]},
        ]})
        self.assertEqual(payouts.paid_from_github(gh, ["ADT-community/trader"]),
                         {("ADT-community/trader", 7)})

    def test_labels_given_as_plain_strings_also_work(self):
        gh = FakeGitHub({"r/x": [{"number": 3, "labels": ["paid"]}]})
        self.assertEqual(payouts.paid_from_github(gh, ["r/x"]), {("r/x", 3)})


class WeekTests(unittest.TestCase):
    def test_week_runs_monday_to_sunday(self):
        self.assertEqual(payout_week(date(2027, 1, 7)), (date(2027, 1, 4), date(2027, 1, 10)))
        self.assertEqual(payout_week(date(2027, 1, 10)), (date(2027, 1, 4), date(2027, 1, 10)))

    def test_a_week_is_paid_the_following_wednesday(self):
        self.assertEqual(pay_date_for(date(2027, 1, 10)), date(2027, 1, 13))
        self.assertEqual(pay_date_for(date(2027, 1, 10)).strftime("%A"), "Wednesday")

    def test_nothing_is_paid_before_the_first_run(self):
        self.assertEqual(pay_date_for(date(2026, 1, 4)), date(2027, 1, 6))
        self.assertEqual(next_payout_date(date(2026, 11, 23)), date(2027, 1, 6))

    def test_next_payout_is_the_coming_wednesday(self):
        self.assertEqual(next_payout_date(date(2027, 1, 11)), date(2027, 1, 13))   # Monday
        self.assertEqual(next_payout_date(date(2027, 1, 13)), date(2027, 1, 13))   # the day itself
        self.assertEqual(next_payout_date(date(2027, 1, 14)), date(2027, 1, 20))   # Thursday


class ParsingTests(unittest.TestCase):
    def test_csv_export_with_human_column_names(self):
        csv_text = ('Type,Task,Phone,Stage,Approved,"Approved At",Work Link\r\n'
                    'delivery,ts-13,+234 801 234 5678,final,yes,2027-01-05,https://x.test\r\n'
                    'delivery,qt-7,+263771234567,final,no,,https://y.test\r\n')
        ledger = build_ledger(parse_records(csv_text), TASKS)
        self.assertEqual([e.task for e in ledger.entries], ["ts-13"])
        self.assertEqual(ledger.entries[0].pay_date, date(2027, 1, 13))

    def test_a_bare_json_list_is_accepted(self):
        recs = parse_records(json.dumps([delivery("la-3", "+221770112233", "2027-01-05")]))
        self.assertEqual(len(build_ledger(recs, TASKS).entries), 1)

    def test_unreadable_exports_say_so(self):
        for bad in ("{}", "[1, 2]" if False else '{"nope": 1}', "not json, not csv"):
            with self.assertRaises(payouts.PayoutError):
                parse_records(bad)

    def test_approval_may_be_a_date_a_flag_or_a_status(self):
        for rec in (delivery("la-3", "+1", approved_at="2027-01-05"),
                    {"type": "delivery", "task": "la-3", "approved_on": "2027-01-05"},
                    {"type": "delivery", "task": "la-3", "status": "approved",
                     "submitted_at": "2027-01-05T10:00:00Z"}):
            self.assertEqual(len(build_ledger([rec], TASKS).entries), 1, rec)

    def test_an_explicit_no_is_not_an_approval(self):
        rec = {"type": "delivery", "task": "la-3", "approved": "no", "approved_at": "2027-01-05"}
        self.assertEqual(build_ledger([rec], TASKS).entries, [])

    def test_approved_work_with_no_usable_date_is_flagged_not_dropped_silently(self):
        ledger = build_ledger([{"type": "delivery", "task": "la-3", "approved": "yes"}], TASKS)
        self.assertEqual(ledger.entries, [])
        self.assertIn("approval date", ledger.warnings[0])


class ContributorTests(unittest.TestCase):
    def test_phone_only_contributors_are_shown_masked(self):
        ledger = build_ledger([delivery("la-3", "+221770112233", "2027-01-05")], TASKS)
        self.assertEqual(ledger.entries[0].contributor, "+221...233")
        self.assertEqual(mask_phone("+2348012345678"), "+234...678")

    def test_a_claim_from_another_task_is_not_used(self):
        records = [claim("ts-13", "+221770112233", "Someone Else", "someone"),
                   delivery("la-3", "+221770112233", "2027-01-05")]
        self.assertEqual(build_ledger(records, TASKS)[0].contributor
                         if isinstance(build_ledger(records, TASKS), list)
                         else build_ledger(records, TASKS).entries[0].contributor, "+221...233")

    def test_two_contributors_on_one_task_is_a_warning_not_a_double_payment(self):
        records = [delivery("la-3", "+221770112233", "2027-01-05", name="Fatou Diop"),
                   delivery("la-3", "+2348012345678", "2027-01-06", name="Amara Okafor")]
        ledger = build_ledger(records, TASKS)
        self.assertEqual([e.contributor for e in ledger.entries], ["Fatou Diop"])
        self.assertEqual(sum(e.amount for e in ledger.entries), 20)
        self.assertIn("more than one contributor", ledger.warnings[0])


class RenderingTests(unittest.TestCase):
    def setUp(self):
        self.ledger = build_ledger(parse_records(SAMPLE.read_text(encoding="utf-8")), TASKS)

    def test_ledger_has_the_six_required_columns(self):
        md = ledger_markdown(self.ledger)
        self.assertIn("| Week | Pay on | Repo | Issue | Contributor | Amount | Status |", md)
        self.assertIn("[#13](https://github.com/ADT-community/trans_sport/issues/13)", md)
        self.assertIn("Amara Okafor (@amaraok)", md)
        self.assertIn("**Owed: $80 over 4 item(s).**", md)

    def test_full_phone_numbers_are_never_published(self):
        md = ledger_markdown(self.ledger) + summary_markdown(self.ledger, date(2027, 1, 6))
        for phone in ("+2348012345678", "+233244000111", "+221770112233"):
            self.assertNotIn(phone, md)
        self.assertIn("+221...233", md)

    def test_full_phone_is_kept_in_the_json_for_whoever_pays(self):
        entry = next(e for e in self.ledger.entries if e.task == "ops-5").as_dict()
        self.assertEqual(entry["phone"], "+233244000111")
        self.assertEqual(entry["week_start"], "2026-11-16")
        self.assertEqual(entry["issue_url"],
                         "https://github.com/ADT-community/adt-community.github.io/issues/5")

    def test_monday_summary_says_what_to_pay_on_wednesday(self):
        md = summary_markdown(self.ledger, date(2027, 1, 6))
        self.assertIn("Pay on **Wednesday 6 January 2027**: **$60** for 3 task(s).", md)
        self.assertIn("Not due yet: 1 item(s), $20", md)         # la-3, week still running
        self.assertIn(f"Add the **`{PAID_LABEL}`** label", md)
        self.assertIn("la-29", md)                               # listed, but not as money owed

    def test_empty_ledger_reads_sensibly(self):
        self.assertIn("No approved deliveries yet.", ledger_markdown(Ledger()))
        self.assertIn("Nothing is due this week.", summary_markdown(Ledger(), date(2027, 1, 6)))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.sources = {"export": SAMPLE.read_text(encoding="utf-8"),
                        "tasks": json.dumps({"tasks": list(TASKS.values())})}
        self.gh = FakeGitHub({"ADT-community/trader": [
            {"number": 7, "title": "Figma: design the investing app",
             "labels": [{"name": PAID_LABEL}]}]})

    def run_it(self, today, post=True):
        return run_payouts(self.gh, export="export", tasks_src="tasks", today=today, post=post,
                           read=self.sources.get)

    def test_monday_run_posts_one_summary_issue(self):
        r = self.run_it(date(2027, 1, 4))
        self.assertEqual(r["pay_date"], "2027-01-06")
        self.assertEqual(r["total_due"], 40)                     # qt-7 is already paid
        self.assertEqual([e["task"] for e in r["due"]], ["ops-5", "ts-13"])
        self.assertEqual(len(self.gh.created), 1)
        issue = self.gh.created[0]
        self.assertEqual(issue["title"], "Payouts for Wednesday 6 January 2027")
        self.assertEqual(issue["labels"], [SUMMARY_LABEL])
        self.assertIn("$40", issue["body"])
        self.assertTrue(r["posted"].endswith("/issues/901"))

    def test_the_paid_label_is_created_where_it_will_be_needed(self):
        self.run_it(date(2027, 1, 4))
        self.assertIn(("ADT-community/trans_sport", PAID_LABEL), self.gh.labels)
        self.assertIn(("ADT-community/ADT_agent", SUMMARY_LABEL), self.gh.labels)

    def test_running_twice_on_the_same_monday_does_not_post_twice(self):
        self.run_it(date(2027, 1, 4))
        again = self.run_it(date(2027, 1, 4))
        self.assertEqual(len(self.gh.created), 1)
        self.assertIn("already open", again["note"])

    def test_nothing_is_posted_without_post(self):
        r = self.run_it(date(2027, 1, 4), post=False)
        self.assertEqual(self.gh.created, [])
        self.assertIsNone(r["posted"])
        self.assertIn("Payout ledger", r["ledger_markdown"])

    def test_a_week_that_was_missed_is_still_owed_the_next_monday(self):
        r = self.run_it(date(2027, 1, 18))                       # nobody paid on the 6th
        self.assertEqual(r["pay_date"], "2027-01-20")
        self.assertEqual([e["task"] for e in r["due"]], ["ops-5", "ts-13", "la-3"])
        self.assertEqual(r["total_due"], 60)

    def test_nothing_due_means_no_issue(self):
        self.sources["export"] = json.dumps([])
        r = self.run_it(date(2027, 1, 4))
        self.assertEqual(self.gh.created, [])
        self.assertIn("Nothing due", r["note"])

    def test_tasks_json_from_the_website_is_read_as_is(self):
        tasks = load_tasks(self.sources["tasks"])
        self.assertEqual(tasks["ts-13"]["issue"], 13)
        with self.assertRaises(payouts.PayoutError):
            load_tasks('{"updated": "2026-11-19"}')


if __name__ == "__main__":
    unittest.main()
