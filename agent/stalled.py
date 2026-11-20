"""Detect stalled bounty tasks and leave one polite comment asking for an update."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

DEFAULT_DAYS = 5
COMMENT_MARKER = "<!-- adt-agent-stalled-check -->"


def is_bounty_label(label: dict) -> bool:
    return str(label.get("name", "")).strip().lower().startswith("bounty")


def is_assigned(issue: dict) -> bool:
    return bool(issue.get("assignee")) or bool(issue.get("assignees"))


def parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_stalled(issue: dict, now: datetime, days: int = DEFAULT_DAYS) -> bool:
    updated = issue.get("updated_at")
    if not updated:
        return False
    try:
        updated_at = parse_time(updated)
    except (ValueError, TypeError):
        return False
    return updated_at <= now - timedelta(days=days)


def has_stall_comment(comments: list[dict]) -> bool:
    return any(COMMENT_MARKER in (c.get("body") or "") for c in comments or [])


def stall_comment(days: int) -> str:
    return (
        f"{COMMENT_MARKER}\n"
        f"Hello — this bounty task is assigned, but there has been no activity for {days} days.\n\n"
        "If you are still working on it, please share a short update so we know where things stand. "
        "If you can no longer continue, please let a maintainer know so the task can be made "
        "available to another contributor.\n\n"
        "Thanks!\n— The ADT agent (automated check)"
    )


def find_stalled(gh, repos: list[str], *, days: int = DEFAULT_DAYS,
                 now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    found: list[dict] = []
    for repo in repos:
        for issue in gh.issues(repo):
            if issue.get("state") != "open" or "pull_request" in issue:
                continue
            if not any(is_bounty_label(l) for l in issue.get("labels") or []):
                continue
            if not is_assigned(issue):
                continue
            if not is_stalled(issue, now, days):
                continue
            updated_at = parse_time(issue["updated_at"])
            number = issue["number"]
            found.append({
                "repo": repo,
                "number": number,
                "title": issue.get("title", ""),
                "url": issue.get("html_url") or f"https://github.com/{repo}/issues/{number}",
                "days_idle": (now - updated_at).days,
            })
    return found


def run(gh, repos: list[str], *, days: int = DEFAULT_DAYS, dry_run: bool = False,
        now: datetime | None = None) -> dict:
    result: dict = {"commented": [], "skipped": []}
    for item in find_stalled(gh, repos, days=days, now=now):
        if has_stall_comment(gh.issue_comments(item["repo"], item["number"])):
            result["skipped"].append({**item, "reason": "already commented"})
            continue
        body = stall_comment(days)
        entry = {**item, "body": body}
        if dry_run:
            entry["comment_url"] = None
        else:
            comment = gh.create_comment(item["repo"], item["number"], body) or {}
            entry["comment_url"] = comment.get("html_url")
        result["commented"].append(entry)
    return result


def summary_md(result: dict, dry_run: bool) -> str:
    lines = [f"# ADT agent: stalled bounty check{' (dry run)' if dry_run else ''}", ""]
    commented = result["commented"]
    if commented:
        action = "Would comment on" if dry_run else "Commented on"
        lines.append(f"{action} {len(commented)} issue(s):")
        for c in commented:
            lines.append(f"- [{c['repo']}#{c['number']}]({c['url']}) — {c['title']} "
                         f"({c['days_idle']} days idle)")
    else:
        lines.append("No stalled bounty tasks found.")
    if result["skipped"]:
        lines += ["", "Skipped (already contacted):"]
        for s in result["skipped"]:
            lines.append(f"- {s['repo']}#{s['number']} — {s['title']}")
    return "\n".join(lines)
