"""Daily bounty report: turn each project's issues into one readable Markdown summary."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .github import GitHub, GitHubError
from .improve import PROPOSAL_LABEL

BOUNTY_PREFIX = "bounty"
REVIEW_LABELS = {"in review", "in-review", "in_review", "review", "needs review"}
STALLED_DAYS = 7

SECTIONS = (
    ("open", "Open bounties"),
    ("claimed", "Claimed"),
    ("in_review", "In review"),
    ("stalled", "Stalled"),
    ("proposals", "Proposals waiting"),
)


@dataclass
class Item:
    title: str
    url: str = ""
    amount: str = ""


@dataclass
class RepoStats:
    name: str
    repo: str
    open: list[Item] = field(default_factory=list)
    claimed: list[Item] = field(default_factory=list)
    in_review: list[Item] = field(default_factory=list)
    stalled: list[Item] = field(default_factory=list)
    proposals: list[Item] = field(default_factory=list)
    error: str = ""


def label_names(issue: dict) -> list[str]:
    names = []
    for label in issue.get("labels") or []:
        if isinstance(label, dict):
            names.append(str(label.get("name", "")))
        else:
            names.append(str(label))
    return [n.strip() for n in names if n.strip()]


def is_bounty(labels: list[str]) -> bool:
    return any(n.lower().startswith(BOUNTY_PREFIX) for n in labels)


def is_proposal(labels: list[str]) -> bool:
    return any(n.lower() == PROPOSAL_LABEL.lower() for n in labels)


def is_in_review(labels: list[str]) -> bool:
    return any(n.lower() in REVIEW_LABELS for n in labels)


def bounty_amount(labels: list[str]) -> str:
    for name in labels:
        match = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", name)
        if not match:
            match = re.search(r"bounty\s*:\s*([\d,]+(?:\.\d+)?)", name, re.I)
        if match:
            return "$" + match.group(1)
    return ""


def has_assignee(issue: dict) -> bool:
    if issue.get("assignee"):
        return True
    assignees = issue.get("assignees")
    return isinstance(assignees, list) and bool(assignees)


def parse_time(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def is_stalled(issue: dict, now: datetime, days: int = STALLED_DAYS) -> bool:
    if not has_assignee(issue):
        return False
    updated = parse_time(issue.get("updated_at"))
    return updated is not None and now - updated >= timedelta(days=days)


def classify(issue: dict, *, now: datetime, stalled_days: int = STALLED_DAYS) -> str | None:
    """Put an open issue in exactly one section, or ignore it with None."""
    if issue.get("pull_request") is not None or str(issue.get("state", "open")).lower() != "open":
        return None
    labels = label_names(issue)
    if is_proposal(labels):
        return "proposals"
    if not is_bounty(labels) and not is_in_review(labels):
        return None
    if is_in_review(labels):
        return "in_review"
    if is_stalled(issue, now, stalled_days):
        return "stalled"
    if has_assignee(issue):
        return "claimed"
    return "open"


def _item(issue: dict, labels: list[str]) -> Item:
    return Item(
        title=str(issue.get("title", "")).strip() or "(untitled)",
        url=str(issue.get("html_url") or ""),
        amount=bounty_amount(labels),
    )


def collect(gh: GitHub, project, *, now: datetime | None = None,
            stalled_days: int = STALLED_DAYS) -> RepoStats:
    now = now or datetime.now(timezone.utc)
    stats = RepoStats(name=project.name, repo=project.repo)
    try:
        issues = gh.issues(project.repo)
    except GitHubError as e:
        stats.error = str(e)
        return stats
    for issue in issues:
        key = classify(issue, now=now, stalled_days=stalled_days)
        if key:
            getattr(stats, key).append(_item(issue, label_names(issue)))
    return stats


def build_markdown(stats: list[RepoStats]) -> str:
    lines = ["# ADT network: daily bounty report", ""]
    lines.append("| Project | " + " | ".join(title for _, title in SECTIONS) + " |")
    lines.append("|---|" + "---:|" * len(SECTIONS))
    for s in stats:
        counts = [str(len(getattr(s, key))) for key, _ in SECTIONS]
        lines.append(f"| {s.name} ({s.repo}) | " + " | ".join(counts) + " |")
    lines.append("")

    for s in stats:
        lines.append(f"## {s.name} ({s.repo})")
        if s.error:
            lines.append(f"⚠️ Could not read issues: {s.error}")
            lines.append("")
            continue
        for key, title in SECTIONS:
            items = getattr(s, key)
            lines.append(f"### {title} ({len(items)})")
            for item in items:
                link = f"[{item.title}]({item.url})" if item.url else item.title
                suffix = f" — {item.amount}" if item.amount else ""
                lines.append(f"- {link}{suffix}")
            if not items:
                lines.append("- none")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def generate(gh: GitHub, projects: list, *, stalled_days: int = STALLED_DAYS,
             now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    stats = [collect(gh, p, now=now, stalled_days=stalled_days) for p in projects]
    return build_markdown(stats)
