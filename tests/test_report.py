"""Tests for the daily bounty report. No network, keys or paid model calls needed."""
import contextlib
import io
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from agent import report
from agent.__main__ import ROOT, load_projects, main
from agent.github import GitHubError
from agent.improve import PROPOSAL_LABEL
from agent.report import (RepoStats, bounty_amount, build_markdown, classify, collect,
                          generate, is_stalled, label_names)

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


def issue(title, labels=(), state="open", assignee=None, updated=None, number=1, pr=False):
    data = {
        "title": title,
        "state": state,
        "labels": [{"name": name} for name in labels],
        "html_url": f"https://github.com/ADT-community/test/issues/{number}",
    }
    if assignee is not None:
        data["assignee"] = {"login": assignee}
    if updated is not None:
        data["updated_at"] = updated
    if pr:
        data["pull_request"] = {"url": "https://api.github.com/pulls/1"}
    return data


class FakeGitHub:
    def __init__(self, per_repo, errors=None):
        self.per_repo = per_repo
        self.errors = errors or {}

    def issues(self, full):
        if full in self.errors:
            raise GitHubError(self.errors[full])
        return self.per_repo.get(full, [])


class ClassificationTests(unittest.TestCase):
    def test_each_category_gets_its_own_section(self):
        cases = {
            "open": issue("Fix GPS drift", ["bounty:$50"]),
            "claimed": issue("Add USSD menu", ["bounty:$100"], assignee="alice",
                             updated="2026-11-20T12:00:00Z"),
            "stalled": issue("Add SMS replies", ["bounty:$75"], assignee="bob",
                             updated="2026-11-12T12:00:00Z"),
            "in_review": issue("Add voice replies", ["bounty:$120", "in review"], assignee="carol",
                               updated="2026-11-01T12:00:00Z"),
            "proposals": issue("Add WhatsApp channel", [PROPOSAL_LABEL]),
        }
        for key, item in cases.items():
            self.assertEqual(classify(item, now=NOW), key, item["title"])

    def test_ignores_closed_prs_and_unrelated_issues(self):
        ignored = [
            issue("Closed bounty", ["bounty:$10"], state="closed"),
            issue("A pull request", ["bounty:$10"], pr=True),
            issue("Just a question", ["question"]),
            issue("Closed proposal", [PROPOSAL_LABEL], state="closed"),
        ]
        for item in ignored:
            self.assertIsNone(classify(item, now=NOW), item["title"])

    def test_stalled_boundary(self):
        exactly = issue("Edge", ["bounty:$10"], assignee="alice",
                        updated=(NOW - timedelta(days=7)).isoformat())
        recent = issue("Recent", ["bounty:$10"], assignee="alice",
                       updated=(NOW - timedelta(days=6, hours=23)).isoformat())
        self.assertTrue(is_stalled(exactly, NOW))
        self.assertFalse(is_stalled(recent, NOW))
        self.assertEqual(classify(exactly, now=NOW), "stalled")
        self.assertEqual(classify(recent, now=NOW), "claimed")

    def test_stalled_needs_assignee_and_valid_timestamp(self):
        self.assertFalse(is_stalled(issue("No owner", ["bounty:$10"]), NOW))
        self.assertFalse(is_stalled(issue("No date", ["bounty:$10"], assignee="alice"), NOW))
        self.assertFalse(is_stalled(issue("Bad date", ["bounty:$10"], assignee="alice",
                                          updated="yesterday"), NOW))

    def test_fake_data_shapes(self):
        self.assertEqual(label_names({"labels": ["bounty:$5", {"name": PROPOSAL_LABEL}]}),
                         ["bounty:$5", PROPOSAL_LABEL])
        self.assertEqual(classify({"title": "List", "labels": ["bounty:$5"],
                                   "assignees": [{"login": "dave"}]}, now=NOW), "claimed")
        self.assertEqual(classify({"title": "Naive", "labels": ["bounty:$5"],
                                   "updated_at": "2026-11-21T10:00:00"}, now=NOW), "open")

    def test_bounty_amounts(self):
        self.assertEqual(bounty_amount(["bounty:$1,000.50"]), "$1,000.50")
        self.assertEqual(bounty_amount(["bounty:250"]), "$250")
        self.assertEqual(bounty_amount(["bounty", "help wanted"]), "")


class MarkdownTests(unittest.TestCase):
    def test_table_and_sections_are_readable(self):
        stats = RepoStats(
            name="Minibus app", repo="ADT-community/trans_sport",
            open=[report.Item("Fix GPS drift", "https://github.com/x/issues/4", "$50")],
            claimed=[report.Item("Add USSD menu", "https://github.com/x/issues/5")],
            in_review=[],
            stalled=[report.Item("Add SMS replies", "", "$75")],
            proposals=[report.Item("Add WhatsApp", "https://github.com/x/issues/9")],
        )
        md = build_markdown([stats])
        self.assertIn("# ADT network: daily bounty report", md)
        self.assertIn("| Project | Open bounties | Claimed | In review | Stalled | Proposals waiting |", md)
        self.assertIn("| Minibus app (ADT-community/trans_sport) | 1 | 1 | 0 | 1 | 1 |", md)
        self.assertIn("### Open bounties (1)", md)
        self.assertIn("- [Fix GPS drift](https://github.com/x/issues/4) — $50", md)
        self.assertIn("- Add SMS replies — $75", md)
        self.assertIn("### In review (0)\n- none", md)

    def test_generate_covers_every_project_in_projects_json(self):
        projects = load_projects(ROOT / "projects.json")
        gh = FakeGitHub({p.repo: [issue("A bounty", ["bounty:$20"])] for p in projects})
        md = generate(gh, projects, now=NOW)
        for project in projects:
            self.assertIn(f"## {project.name} ({project.repo})", md)
        self.assertTrue(all(f"| {p.name} ({p.repo}) | 1 |" in md for p in projects))

    def test_repo_error_does_not_hide_other_repos(self):
        projects = load_projects(ROOT / "projects.json")
        broken = projects[0].repo
        gh = FakeGitHub({p.repo: [] for p in projects}, errors={broken: "GET /issues -> 403"})
        md = generate(gh, projects, now=NOW)
        self.assertIn(f"## {projects[0].name} ({broken})", md)
        self.assertIn("Could not read issues: GET /issues -> 403", md)
        self.assertIn(f"## {projects[1].name} ({projects[1].repo})", md)

    def test_cli_command_prints_markdown(self):
        project = load_projects(ROOT / "projects.json")[0]
        gh = FakeGitHub({project.repo: [issue("Fix GPS drift", ["bounty:$50"])]})
        out = io.StringIO()
        with patch.dict(os.environ, {"ADT_GITHUB_TOKEN": "fake"}), \
             patch("agent.__main__.GitHub", return_value=gh), \
             contextlib.redirect_stdout(out):
            code = main(["report", "--config", str(ROOT / "projects.json")])
        self.assertEqual(code, 0)
        self.assertIn("# ADT network: daily bounty report", out.getvalue())
        self.assertIn("Fix GPS drift", out.getvalue())

    def test_collect_respects_custom_stalled_days(self):
        project = load_projects(ROOT / "projects.json")[0]
        old = issue("Old", ["bounty:$10"], assignee="alice",
                    updated=(NOW - timedelta(days=10)).isoformat())
        stats = collect(FakeGitHub({project.repo: [old]}), project, now=NOW, stalled_days=14)
        self.assertEqual(len(stats.claimed), 1)
        self.assertEqual(len(stats.stalled), 0)


if __name__ == "__main__":
    unittest.main()
