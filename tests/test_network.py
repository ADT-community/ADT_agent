"""Tests for the network report. No network or keys needed."""
import json
import unittest
from collections import Counter
from datetime import datetime, timezone

from agent.network import load_tasks, open_prs, report_md, task_summary

TASKS = [
    {"project": "Minibus app", "paid": True, "reward": 20, "status": "open"},
    {"project": "Minibus app", "paid": True, "reward": 20, "status": "taken"},
    {"project": "Minibus app", "paid": False, "reward": 0, "status": "open"},
    {"project": "Trader", "paid": True, "reward": 20, "status": "done"},
    {"project": "Trader", "paid": True, "reward": 20, "status": "open"},
    {"project": "Trader", "paid": True, "reward": 20, "status": "open"},
]


class FakeGH:
    def file(self, full, path, ref):
        return json.dumps({"tasks": TASKS}) if path == "tasks.json" else None

    def pulls(self, repo):
        return [{"state": "open", "user": {"login": "ama"}}, {"state": "open", "user": {"login": "ama"}},
                {"state": "closed", "user": {"login": "kofi"}}]


class NetworkTests(unittest.TestCase):
    def test_summary(self):
        rows = {r["project"]: r for r in task_summary(TASKS)}
        self.assertEqual(rows["Minibus app"], {"project": "Minibus app", "paid_open": 1, "paid_open_usd": 20,
                                               "next_open": 1, "taken": 1, "done": 0})
        self.assertEqual(rows["Trader"]["paid_open_usd"], 40)
        self.assertEqual(rows["Trader"]["done"], 1)

    def test_sorted_by_money_open(self):
        self.assertEqual(task_summary(TASKS)[0]["project"], "Trader")

    def test_load_tasks_from_site(self):
        self.assertEqual(len(load_tasks(FakeGH())), 6)

    def test_open_prs_counts_only_open(self):
        self.assertEqual(open_prs(FakeGH(), ["o/r"])["o/r"], Counter({"ama": 2}))

    def test_report(self):
        when = datetime(2026, 12, 1, tzinfo=timezone.utc)
        md = report_md(task_summary(TASKS), {"o/r": Counter({"ama": 2})}, when)
        self.assertIn("**3 paid tasks open ($60)**", md)
        self.assertIn("| Trader | 2 | $40 | 0 | 0 | 1 |", md)
        self.assertIn("| r | 2 | ama (2) |", md)


if __name__ == "__main__":
    unittest.main()
