"""Tests for stalled bounty detection. No network or keys needed."""
import unittest
from datetime import datetime, timezone

from agent import stalled
from agent.stalled import (COMMENT_MARKER, find_stalled, has_stall_comment,
                           is_assigned, is_bounty_label, is_stalled, run,
                           stall_comment)

NOW = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
OLD = "2026-06-01T00:00:00Z"
RECENT = "2026-06-09T00:00:00Z"
REPO = "ADT-community/demo"


def make_issue(number, *, state="open", labels=None, assignee="alice",
               updated_at=OLD, **extra):
    issue = {
        "number": number,
        "title": f"Task {number}",
        "state": state,
        "updated_at": updated_at,
        "labels": [{"name": "bounty:$100"}] if labels is None else labels,
        "html_url": f"https://github.com/{REPO}/issues/{number}",
    }
    if assignee is not None:
        issue["assignee"] = {"login": assignee}
    issue.update(extra)
    return issue


class FakeGitHub:
    def __init__(self, issues=None, comments=None):
        self.issues_by_repo = issues or {}
        self.comments = comments or {}
        self.created = []

    def issues(self, full):
        return self.issues_by_repo.get(full, [])

    def issue_comments(self, full, number):
        return self.comments.get((full, number), [])

    def create_comment(self, full, number, body):
        self.created.append((full, number, body))
        return {"html_url": f"https://github.com/{full}/issues/{number}#issuecomment-{len(self.created)}"}


class PredicateTests(unittest.TestCase):
    def test_bounty_label_variants(self):
        for name in ("bounty", "bounty:$100", "BOUNTY: 250", " Bounty:50 "):
            self.assertTrue(is_bounty_label({"name": name}))
        for name in ("agent-proposal", "help wanted", "", "not-a-bounty"):
            self.assertFalse(is_bounty_label({"name": name}))

    def test_assignment_variants(self):
        self.assertTrue(is_assigned({"assignee": {"login": "a"}}))
        self.assertTrue(is_assigned({"assignees": [{"login": "a"}]}))
        self.assertFalse(is_assigned({"assignee": None, "assignees": []}))
        self.assertFalse(is_assigned({}))

    def test_stalled_boundaries_and_bad_timestamps(self):
        self.assertTrue(is_stalled({"updated_at": "2026-06-05T12:00:00Z"}, NOW))
        self.assertFalse(is_stalled({"updated_at": "2026-06-06T12:00:00Z"}, NOW))
        self.assertTrue(is_stalled({"updated_at": "2026-06-01T14:00:00+02:00"}, NOW))
        self.assertFalse(is_stalled({"updated_at": "garbage"}, NOW))
        self.assertFalse(is_stalled({}, NOW))

    def test_marker_detection(self):
        self.assertTrue(has_stall_comment([{"body": f"hi\n{COMMENT_MARKER}\nbye"}]))
        self.assertFalse(has_stall_comment([{"body": "just a normal reply"}]))
        self.assertFalse(has_stall_comment([]))

    def test_comment_is_polite_and_marked(self):
        body = stall_comment(5)
        self.assertIn(COMMENT_MARKER, body)
        self.assertIn("5 days", body)
        self.assertIn("update", body.lower())


class FindStalledTests(unittest.TestCase):
    def test_finds_stalled_issue(self):
        gh = FakeGitHub({REPO: [make_issue(7)]})
        found = find_stalled(gh, [REPO], now=NOW)
        self.assertEqual([i["number"] for i in found], [7])
        self.assertEqual(found[0]["repo"], REPO)
        self.assertEqual(found[0]["days_idle"], 9)
        self.assertEqual(found[0]["title"], "Task 7")

    def test_ignores_ineligible_issues(self):
        issues = [
            make_issue(1, state="closed"),
            make_issue(2, labels=[{"name": "agent-proposal"}]),
            make_issue(3, assignee=None),
            make_issue(4, updated_at=RECENT),
            make_issue(5, pull_request={"url": "x"}),
            make_issue(6, updated_at="not-a-date"),
        ]
        gh = FakeGitHub({REPO: issues})
        self.assertEqual(find_stalled(gh, [REPO], now=NOW), [])

    def test_finds_across_repos(self):
        other = "ADT-community/other"
        gh = FakeGitHub({REPO: [make_issue(1)], other: [make_issue(2)]})
        found = find_stalled(gh, [REPO, other], now=NOW)
        self.assertEqual([(i["repo"], i["number"]) for i in found],
                         [(REPO, 1), (other, 2)])


class RunTests(unittest.TestCase):
    def test_posts_one_comment_per_stalled_issue(self):
        gh = FakeGitHub({REPO: [make_issue(1), make_issue(2)]})
        result = run(gh, [REPO], now=NOW)
        self.assertEqual(len(result["commented"]), 2)
        self.assertEqual(len(gh.created), 2)
        self.assertEqual([n for _, n, _ in gh.created], [1, 2])
        self.assertTrue(all(COMMENT_MARKER in body for _, _, body in gh.created))
        self.assertEqual(result["skipped"], [])

    def test_never_comments_twice_on_same_issue(self):
        comment = {"body": f"Already handled\n{COMMENT_MARKER}"}
        gh = FakeGitHub({REPO: [make_issue(1), make_issue(2)]},
                        comments={(REPO, 1): [comment]})
        result = run(gh, [REPO], now=NOW)
        self.assertEqual([i["number"] for i in result["commented"]], [2])
        self.assertEqual([i["number"] for i in result["skipped"]], [1])
        self.assertEqual([n for _, n, _ in gh.created], [2])

    def test_dry_run_posts_nothing(self):
        gh = FakeGitHub({REPO: [make_issue(1)]})
        result = run(gh, [REPO], dry_run=True, now=NOW)
        self.assertEqual(gh.created, [])
        self.assertEqual(len(result["commented"]), 1)
        self.assertIsNone(result["commented"][0]["comment_url"])

    def test_custom_days_threshold(self):
        recent = make_issue(1, updated_at="2026-06-08T00:00:00Z")
        gh = FakeGitHub({REPO: [recent]})
        self.assertEqual(run(gh, [REPO], days=5, now=NOW)["commented"], [])
        self.assertEqual(len(run(gh, [REPO], days=1, now=NOW)["commented"]), 1)

    def test_summary_markdown(self):
        gh = FakeGitHub({REPO: [make_issue(1)]})
        result = run(gh, [REPO], dry_run=True, now=NOW)
        md = stalled.summary_md(result, dry_run=True)
        self.assertIn("Would comment on 1 issue", md)
        self.assertIn("ADT-community/demo#1", md)


if __name__ == "__main__":
    unittest.main()
