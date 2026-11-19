from __future__ import annotations

import datetime
from typing import List, Dict

from .github import GitHub, GitHubError

STALE_DAYS = 5
# Hidden marker so we can recognise our own comment and avoid duplicates
COMMENT_MARKER = "<!-- ADT stalled reminder -->"
COMMENT_BODY = f"{COMMENT_MARKER}\nHi, just checking in on this bounty issue. Could you provide an update?\nThanks!"


def _is_stale(updated_at: str) -> bool:
    """Return ``True`` if the ISO‑8601 timestamp is older than ``STALE_DAYS``.

    GitHub timestamps are UTC and may end with a trailing ``Z`` – ``datetime``
    can parse both ``2023-01-01T12:34:56Z`` and ``2023-01-01T12:34:56+00:00``.
    """
    # Strip trailing Z because ``fromisoformat`` does not understand it directly
    iso = updated_at.rstrip("Z")
    dt = datetime.datetime.fromisoformat(iso)
    # Ensure we are working in UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
    return (now - dt).days >= STALE_DAYS


def _already_commented(comments: List[Dict]) -> bool:
    """Detect whether we have already left a reminder on the issue.

    The hidden ``COMMENT_MARKER`` makes the check reliable even if the user
    edits the visible part of the comment.
    """
    return any(COMMENT_MARKER in (c.get("body") or "") for c in comments)


def run_stalled(gh: GitHub, *, dry_run: bool = False) -> None:
    """Find stalled bounty issues and comment on them.

    Parameters
    ----------
    gh: GitHub
        Authenticated client wrapper.
    dry_run: bool, optional
        When ``True`` only prints what would happen; no API calls that mutate
        state are performed.
    """
    query = "is:issue is:open label:bounty assignee:*"
    try:
        issues = gh.search_issues(query)
    except GitHubError as exc:
        print(f"Error while searching for stalled issues: {exc}")
        return

    for issue in issues:
        # ``updated_at`` is the timestamp of the last activity (comments, edits, etc.)
        if not _is_stale(issue.get("updated_at", "")):
            continue

        repo_full_name = issue["repository"]["full_name"]
        number = issue["number"]

        try:
            comments = gh.list_issue_comments(repo_full_name, number)
        except GitHubError as exc:
            print(f"Failed to fetch comments for {repo_full_name}#{number}: {exc}")
            continue

        if _already_commented(comments):
            # We have already reminded the assignee – skip to avoid spamming.
            continue

        if dry_run:
            print(f"[dry-run] Would comment on {issue.get('html_url')}")
            continue

        try:
            gh.create_issue_comment(repo_full_name, number, COMMENT_BODY)
            print(f"Commented on {issue.get('html_url')}")
        except GitHubError as exc:
            print(f"Failed to comment on {repo_full_name}#{number}: {exc}")
