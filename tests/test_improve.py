"""Tests for the improvement pipeline. No network or keys needed."""
import json
import unittest

from agent import llm
from agent.improve import (PROPOSAL_LABEL, Project, Proposal, build_prompt, gather,
                           is_duplicate, run_project, _pick_files)


class FakeGitHub:
    """Stands in for the GitHub API with an in-memory repo."""

    def __init__(self, files=None, issues=None):
        self.files = files or {}
        self._issues = issues or []
        self.created = []
        self.labels = []

    def repo(self, full):
        return {"default_branch": "main", "description": "Test project"}

    def tree(self, full, branch):
        return list(self.files)

    def file(self, full, path, ref):
        return self.files.get(path)

    def commits(self, full, n=15):
        return ["Add voice-first chat web app"]

    def issues(self, full):
        return self._issues

    def ensure_label(self, full, name, color, description=""):
        self.labels.append(name)

    def create_issue(self, full, title, body, labels):
        self.created.append({"title": title, "body": body, "labels": labels})
        return {"html_url": f"https://github.com/{full}/issues/{len(self.created)}"}


def fake_ask(proposals):
    def ask(system, user, **kw):
        fake_ask.last_user = user
        return "Here you go:\n```json\n" + json.dumps({"proposals": proposals}) + "\n```"
    return ask


WHATSAPP = {
    "title": "Add a WhatsApp channel on top of the core API",
    "type": "feature",
    "why": "Most users already live in WhatsApp.",
    "what": "Receive WhatsApp messages and voice notes, send them to the core API, reply with text and audio.",
    "acceptance_criteria": ["Text messages get answers", "Voice notes get spoken replies"],
    "size": "M",
    "suggested_bounty_usd": 200,
    "skills": ["backend", "WhatsApp Cloud API"],
}
TESTS = {"title": "Add tests for the chat turn logic", "type": "tests",
         "why": "Regressions are easy.", "what": "Unit tests for handleUserText.", "size": "S",
         "suggested_bounty_usd": 50}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.project = Project(name="Local-language AI", repo="ADT-community/locallity_ai", focus="voice first")
        self.gh = FakeGitHub(files={"README.md": "# Locality", "web/app.js": "console.log(1)",
                                    "node_modules/x.js": "junk"})

    def test_opens_issues_with_label(self):
        r = run_project(self.gh, self.project, ask=fake_ask([WHATSAPP, TESTS]))
        self.assertEqual(len(self.gh.created), 2)
        self.assertEqual(self.gh.created[0]["labels"], [PROPOSAL_LABEL])
        self.assertIn(PROPOSAL_LABEL, self.gh.labels)
        self.assertIn("WhatsApp", self.gh.created[0]["title"])
        self.assertIn("$200", self.gh.created[0]["body"])
        self.assertIn("- [ ] Voice notes get spoken replies", self.gh.created[0]["body"])
        self.assertTrue(r["created"][0]["url"].endswith("/issues/1"))

    def test_dry_run_opens_nothing(self):
        r = run_project(self.gh, self.project, dry_run=True, ask=fake_ask([WHATSAPP]))
        self.assertEqual(self.gh.created, [])
        self.assertEqual(self.gh.labels, [])
        self.assertIn("What to build", r["created"][0]["body"])

    def test_skips_duplicates_of_existing_issues(self):
        self.gh._issues = [{"title": "WhatsApp channel on top of core API", "state": "closed", "labels": []}]
        r = run_project(self.gh, self.project, ask=fake_ask([WHATSAPP, TESTS]))
        self.assertEqual([c["title"] for c in self.gh.created], [TESTS["title"]])
        self.assertEqual(r["skipped"][0]["reason"], "duplicate")

    def test_respects_cap_of_waiting_proposals(self):
        self.gh._issues = [{"title": f"Old {i}", "state": "open", "labels": [{"name": PROPOSAL_LABEL}]}
                           for i in range(10)]
        called = []
        r = run_project(self.gh, self.project, max_open=10, ask=lambda *a, **k: called.append(1) or "{}")
        self.assertEqual(called, [])  # doesn't even ask Claude
        self.assertIn("already waiting", r["note"])

    def test_limits_new_proposals_per_run(self):
        self.project.max_new = 1
        run_project(self.gh, self.project, ask=fake_ask([WHATSAPP, TESTS]))
        self.assertEqual(len(self.gh.created), 1)

    def test_prompt_includes_context_and_skips_vendor_dirs(self):
        ctx = gather(self.gh, self.project)
        prompt = build_prompt(self.project, ctx, 3)
        self.assertIn("voice first", prompt)
        self.assertIn("### web/app.js", prompt)
        self.assertNotIn("### node_modules/x.js", prompt)
        self.assertIn("Add voice-first chat web app", prompt)

    def test_bad_proposals_are_ignored(self):
        r = run_project(self.gh, self.project,
                        ask=fake_ask([{"title": ""}, "nonsense", {"title": "X", "what": ""}, TESTS]))
        self.assertEqual([c["title"] for c in r["created"]], [TESTS["title"]])


class UnitTests(unittest.TestCase):
    def test_parse_json_variants(self):
        self.assertEqual(llm.parse_json('{"a": 1}'), {"a": 1})
        self.assertEqual(llm.parse_json('Sure!\n```json\n{"a": 2}\n```\nDone'), {"a": 2})
        self.assertEqual(llm.parse_json('prefix {"a": {"b": 3}} suffix'), {"a": {"b": 3}})
        with self.assertRaises(llm.LLMError):
            llm.parse_json("no json here")

    def test_duplicate_detection(self):
        self.assertTrue(is_duplicate("Add WhatsApp channel", ["WhatsApp channel"]))
        self.assertFalse(is_duplicate("Add SMS channel", ["WhatsApp channel for farmers"]))
        self.assertFalse(is_duplicate("", ["anything"]))

    def test_proposal_cleaning(self):
        p = Proposal.from_dict({"title": "T", "what": "W", "size": "medium", "suggested_bounty_usd": "120.5"})
        self.assertEqual(p.size, "M")
        self.assertEqual(p.suggested_bounty_usd, 120)
        self.assertIsNone(Proposal.from_dict({"title": "T"}))

    def test_file_priority(self):
        order = _pick_files(["src/deep/util.js", "README.md", "web/app.js", "package-lock.json", "logo.png"])
        self.assertEqual(order[0], "README.md")
        self.assertNotIn("package-lock.json", order)
        self.assertNotIn("logo.png", order)

    def test_llm_requires_key(self):
        import os
        old = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            with self.assertRaises(llm.LLMError):
                llm.ask("s", "u")
        finally:
            if old is not None:
                os.environ["ANTHROPIC_API_KEY"] = old


if __name__ == "__main__":
    unittest.main()
