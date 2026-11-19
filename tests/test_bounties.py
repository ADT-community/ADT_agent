"""Tests for bounty housekeeping. No network or keys needed."""
import unittest
from datetime import datetime, timedelta, timezone

from agent.bounties import (CLAIM_MARKER, STALL_MARKER, bounty_amount, process_repo, report_md)

NOW = datetime(2026, 12, 1, 12, tzinfo=timezone.utc)


def ts(days_ago):
    return (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def issue(n, labels, assignee=None):
    return {"number": n, "title": f"Task {n}", "state": "open", "html_url": f"https://x/{n}",
            "labels": [{"name": l} for l in labels],
            "assignees": [{"login": assignee}] if assignee else []}


class FakeGH:
    def __init__(self, issues, comments=None, events=None, pulls=None):
        self._issues, self._comments = issues, comments or {}
        self._events, self._pulls = events or {}, pulls or []
        self.posted = []

    def issues(self, repo):
        return self._issues

    def pulls(self, repo):
        return self._pulls

    def issue_comments(self, repo, n):
        return self._comments.get(n, [])

    def issue_events(self, repo, n):
        return self._events.get(n, [])

    def comment(self, repo, n, body):
        self.posted.append((n, body))


class BountyTests(unittest.TestCase):
    def test_amount(self):
        self.assertEqual(bounty_amount(issue(1, ["bounty", "bounty:$20"])), 20)
        self.assertIsNone(bounty_amount(issue(1, ["next-wave"])))

    def test_claim_instructions_posted_once(self):
        gh = FakeGH([issue(1, ["bounty:$20"]), issue(2, ["bounty:$20"])],
                    comments={2: [{"body": CLAIM_MARKER + " old", "user": {"login": "bot"}, "created_at": ts(3)}]})
        st = process_repo(gh, "o/r", now=NOW)
        self.assertEqual([n for n, _ in gh.posted], [1])
        self.assertEqual(len(st.paid_open), 2)

    def test_next_wave_gets_no_claim_comment(self):
        gh = FakeGH([issue(1, ["next-wave"])])
        st = process_repo(gh, "o/r", now=NOW)
        self.assertEqual(gh.posted, [])
        self.assertEqual(st.next_wave, 1)

    def test_stalled_after_five_quiet_days(self):
        claim = [{"body": CLAIM_MARKER, "user": {"login": "bot"}, "created_at": ts(9)}]
        gh = FakeGH([issue(1, ["bounty:$20"], "ama")], comments={1: claim},
                    events={1: [{"event": "assigned", "assignee": {"login": "ama"}, "created_at": ts(6)}]})
        st = process_repo(gh, "o/r", now=NOW)
        self.assertEqual(len(st.stalled), 1)
        self.assertEqual(st.stalled[0]["days"], 6)
        self.assertIn("@ama", gh.posted[0][1])
        self.assertIn(STALL_MARKER, gh.posted[0][1])

    def test_recent_assignee_comment_is_not_stalled(self):
        comments = [{"body": CLAIM_MARKER, "user": {"login": "bot"}, "created_at": ts(9)},
                    {"body": "Halfway there", "user": {"login": "ama"}, "created_at": ts(2)}]
        gh = FakeGH([issue(1, ["bounty:$20"], "ama")], comments={1: comments},
                    events={1: [{"event": "assigned", "assignee": {"login": "ama"}, "created_at": ts(8)}]})
        st = process_repo(gh, "o/r", now=NOW)
        self.assertEqual(st.stalled, [])
        self.assertEqual(len(st.claimed), 1)

    def test_open_pr_counts_as_in_review(self):
        gh = FakeGH([issue(4, ["bounty:$20"], "ama")],
                    comments={4: [{"body": CLAIM_MARKER, "user": {"login": "bot"}, "created_at": ts(9)}]},
                    pulls=[{"state": "open", "title": "Fix", "body": "Closes #4", "user": {"login": "ama"},
                            "updated_at": ts(1)}])
        st = process_repo(gh, "o/r", now=NOW)
        self.assertEqual(len(st.in_review), 1)
        self.assertEqual(gh.posted, [])

    def test_stall_comment_not_repeated(self):
        comments = [{"body": CLAIM_MARKER, "user": {"login": "bot"}, "created_at": ts(20)},
                    {"body": STALL_MARKER, "user": {"login": "bot"}, "created_at": ts(3)}]
        gh = FakeGH([issue(1, ["bounty:$20"], "ama")], comments={1: comments},
                    events={1: [{"event": "assigned", "assignee": {"login": "ama"}, "created_at": ts(15)}]})
        process_repo(gh, "o/r", now=NOW)
        self.assertEqual(gh.posted, [])

    def test_dry_run_posts_nothing(self):
        gh = FakeGH([issue(1, ["bounty:$20"])])
        st = process_repo(gh, "o/r", now=NOW, dry_run=True)
        self.assertEqual(gh.posted, [])
        self.assertEqual(len(st.actions), 1)

    def test_report(self):
        gh = FakeGH([issue(1, ["bounty:$20"]), issue(2, ["next-wave"]), issue(3, ["agent-proposal"])])
        md = report_md([process_repo(gh, "o/r", now=NOW, dry_run=True)], when=NOW)
        self.assertIn("1 paid tasks open ($20)", md)
        self.assertIn("| o/r | 1 | 0 | 0 | 0 | 1 | 1 |", md)


if __name__ == "__main__":
    unittest.main()
