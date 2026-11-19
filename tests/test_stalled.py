import datetime
from unittest import mock

import pytest

from agent.stalled import run_stalled, COMMENT_MARKER

# Helper to build a fake issue dict similar to what GitHub.search_issues would return
def make_issue(number, repo_full_name, updated_at, html_url="http://example.com/issue"):
    return {
        "number": number,
        "repository": {"full_name": repo_full_name},
        "updated_at": updated_at,
        "html_url": html_url,
    }

# Helper to build a comment dict
def make_comment(body):
    return {"body": body}

@pytest.fixture
def gh_mock():
    return mock.Mock()

def test_stale_issue_commented(gh_mock):
    # Issue older than 5 days, no prior comment
    old_date = (datetime.datetime.utcnow() - datetime.timedelta(days=6)).isoformat() + "Z"
    issue = make_issue(1, "owner/repo", old_date)
    gh_mock.search_issues.return_value = [issue]
    gh_mock.list_issue_comments.return_value = []

    run_stalled(gh_mock, dry_run=False)

    gh_mock.create_issue_comment.assert_called_once_with("owner/repo", 1, mock.ANY)
    # Ensure our marker is present in the comment body
    args, _ = gh_mock.create_issue_comment.call_args
    assert COMMENT_MARKER in args[2]

def test_recent_issue_not_commented(gh_mock):
    recent_date = (datetime.datetime.utcnow() - datetime.timedelta(days=2)).isoformat() + "Z"
    issue = make_issue(2, "owner/repo", recent_date)
    gh_mock.search_issues.return_value = [issue]
    gh_mock.list_issue_comments.return_value = []

    run_stalled(gh_mock, dry_run=False)

    gh_mock.create_issue_comment.assert_not_called()

def test_already_commented_issue_skipped(gh_mock):
    old_date = (datetime.datetime.utcnow() - datetime.timedelta(days=7)).isoformat() + "Z"
    issue = make_issue(3, "owner/repo", old_date)
    gh_mock.search_issues.return_value = [issue]
    gh_mock.list_issue_comments.return_value = [make_comment("some other comment"), make_comment(COMMENT_MARKER + "\nprevious reminder")]

    run_stalled(gh_mock, dry_run=False)

    gh_mock.create_issue_comment.assert_not_called()

def test_dry_run_prints(gh_mock, capsys):
    old_date = (datetime.datetime.utcnow() - datetime.timedelta(days=10)).isoformat() + "Z"
    issue = make_issue(4, "owner/repo", old_date)
    gh_mock.search_issues.return_value = [issue]
    gh_mock.list_issue_comments.return_value = []

    run_stalled(gh_mock, dry_run=True)

    captured = capsys.readouterr()
    assert "[dry-run] Would comment on" in captured.out
    gh_mock.create_issue_comment.assert_not_called()
