"""Tests for the daily network status. No network or keys needed."""
import json
import unittest
from datetime import datetime, timezone

from agent import status as st
from agent.__main__ import main
from agent.github import GitHubError

NOW = datetime(2026, 11, 25, 5, 41, tzinfo=timezone.utc)

TASKS = [
    {"id": "ts-1", "project": "Minibus app", "repo": "trans_sport", "issue": 1, "status": "open", "paid": True, "reward": 20},
    {"id": "ts-2", "project": "Minibus app", "repo": "trans_sport", "issue": 2, "status": "taken", "paid": False},
    {"id": "ag-1", "project": "ADT agent", "repo": "ADT_agent", "issue": 1, "status": "done", "paid": False},
    {"id": "ag-2", "project": "ADT agent", "repo": "ADT_agent", "issue": 2, "status": "open", "paid": False},
]


class FakeGitHub:
    def __init__(self, tasks=TASKS, broken=(), status_issue=None):
        self.tasks = tasks
        self.broken = set(broken)
        self.issues_by_repo = {
            "ADT-community/trans_sport": [{"number": 1, "title": "A"}, {"number": 2, "title": "B"}],
            "ADT-community/ADT_agent": [{"number": 1, "title": "C"}] + ([status_issue] if status_issue else []),
        }
        self.pulls_by_repo = {"ADT-community/trans_sport": [{"user": {"login": "ama"}}, {"user": {"login": "ama"}},
                                                             {"user": {"login": "kwame"}}]}
        self.comments, self.files, self.created = [], [], []

    def _check(self, full):
        if full in self.broken:
            raise GitHubError(f"GET /repos/{full} -> 404")

    def file(self, full, path, ref):
        return json.dumps({"updated": "2026-11-19", "tasks": self.tasks}) if path == "tasks.json" else None

    def repo(self, full):
        self._check(full)
        return {"pushed_at": "2026-11-24T10:00:00Z"}

    def open_issues(self, full):
        self._check(full)
        return list(self.issues_by_repo.get(full, []))

    def pulls(self, full):
        self._check(full)
        return self.pulls_by_repo.get(full, [])

    def create_issue(self, full, title, body, labels):
        issue = {"number": 99, "title": title, "html_url": f"https://github.com/{full}/issues/99"}
        self.created.append(issue)
        return issue

    def comment(self, full, number, body):
        self.comments.append((full, number, body))
        return {"html_url": f"https://github.com/{full}/issues/{number}#c1"}

    def put_file(self, full, path, text, message, branch="main"):
        self.files.append((full, path, text, message))
        return {"content": {"html_url": f"https://github.com/{full}/blob/main/{path}"}}


class NetworkReposTest(unittest.TestCase):
    def test_merges_tasks_and_projects_once(self):
        repos = st.network_repos(TASKS, ["ADT-community/trans_sport", "ADT-community/content_feed",
                                         "ADT-community/adt_agent"])
        self.assertEqual(repos, ["ADT-community/trans_sport", "ADT-community/ADT_agent",
                                 "ADT-community/content_feed"])


class BuildStatusTest(unittest.TestCase):
    def test_counts(self):
        s = st.build_status(FakeGitHub(), TASKS, ["ADT-community/trans_sport", "ADT-community/ADT_agent"], now=NOW)
        self.assertEqual(s["generated_at"], "2026-11-25T05:41:00Z")
        self.assertEqual(s["totals"], {"open": 2, "taken": 1, "done": 1, "tasks": 4,
                                       "open_pull_requests": 3, "open_issues": 3, "repos": 2})
        minibus = next(p for p in s["projects"] if p["name"] == "Minibus app")
        self.assertEqual((minibus["open"], minibus["taken"], minibus["done"]), (1, 1, 0))
        ts = s["repos"][0]
        self.assertEqual(ts["open_pull_requests"], 3)
        self.assertEqual(ts["contributors_with_open_prs"], 2)
        self.assertEqual(ts["last_push"], "2026-11-24T10:00:00Z")

    def test_unreadable_repo_is_reported_not_skipped(self):
        s = st.build_status(FakeGitHub(broken={"ADT-community/ADT_agent"}), TASKS,
                            ["ADT-community/trans_sport", "ADT-community/ADT_agent"], now=NOW)
        self.assertIn("error", s["repos"][1])
        self.assertEqual(s["totals"]["open_pull_requests"], 3)
        self.assertIn("couldn't read", st.render_md(s))

    def test_no_pay_information(self):
        s = st.build_status(FakeGitHub(), TASKS, ["ADT-community/trans_sport"], now=NOW)
        text = json.dumps(s) + st.render_md(s)
        for word in ("$", "reward", "paid", "bounty"):
            self.assertNotIn(word, text.lower())


class RenderTest(unittest.TestCase):
    def test_markdown(self):
        md = st.render_md(st.build_status(FakeGitHub(), TASKS, ["ADT-community/trans_sport"], now=NOW))
        self.assertIn("## Network status, 2026-11-25", md)
        self.assertIn("| Minibus app | 1 | 1 | 0 |", md)
        self.assertIn("| [trans_sport](https://github.com/ADT-community/trans_sport) | 2 | 3 | 2026-11-24 |", md)
        self.assertIn("status.html", md)


class RunStatusTest(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        gh = FakeGitHub()
        res = st.run_status(gh, dry_run=True, now=NOW)
        self.assertEqual((gh.comments, gh.files, gh.created), ([], [], []))
        self.assertEqual(res.status["totals"]["tasks"], 4)

    def test_comments_on_existing_issue_and_writes_status_json(self):
        gh = FakeGitHub(status_issue={"number": 12, "title": "Network status",
                                      "html_url": "https://github.com/ADT-community/ADT_agent/issues/12"})
        res = st.run_status(gh, now=NOW)
        self.assertEqual(gh.created, [])
        self.assertEqual(gh.comments[0][:2], ("ADT-community/ADT_agent", 12))
        full, path, text, msg = gh.files[0]
        self.assertEqual((full, path), ("ADT-community/adt-community.github.io", "status.json"))
        self.assertEqual(json.loads(text)["generated_at"], "2026-11-25T05:41:00Z")
        self.assertEqual(msg, "Network status 2026-11-25")
        self.assertTrue(res.comment_url and res.file_url)

    def test_creates_issue_when_missing(self):
        gh = FakeGitHub()
        st.run_status(gh, now=NOW)
        self.assertEqual(gh.created[0]["title"], "Network status")
        self.assertEqual(gh.comments[0][1], 99)

    def test_missing_tasks_json_raises(self):
        gh = FakeGitHub()
        gh.file = lambda *a: None
        with self.assertRaises(GitHubError):
            st.run_status(gh, dry_run=True)


class CliTest(unittest.TestCase):
    def test_status_dry_run(self):
        import io
        import os
        from contextlib import redirect_stdout
        from unittest import mock
        fake = FakeGitHub()
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"ADT_GITHUB_TOKEN": "x"}, clear=False), \
                mock.patch("agent.__main__.GitHub", return_value=fake), redirect_stdout(out):
            code = main(["status", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("Network status", out.getvalue())
        self.assertEqual(fake.comments, [])


if __name__ == "__main__":
    unittest.main()
