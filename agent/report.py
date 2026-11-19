'''Report generation for ADT agent.

Provides a CLI entry point `python -m agent.report` and a function
`generate_markdown(projects_path: Path) -> str` that returns a markdown
summary of bounty status for each repository listed in `projects.json`.

The implementation uses the :class:`~agent.github.GitHub` helper to query
GitHub issues.  For each project we count issues with the following
labels:

* ``bounty`` – open bounties
* ``claimed`` – bounty claimed by a contributor
* ``in review`` – PR under review
* ``stalled`` – no activity for >7 days
* ``proposal`` – proposals waiting for maintainer response

If the GitHub token is not available the function falls back to returning
zero counts – this makes the function safe to import in environments
without network access (e.g. unit tests).
'''"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

from .github import GitHub, GitHubError


def _count_issues(gh: GitHub, repo: str, label: str) -> int:
    """Return the number of open issues in *repo* that have *label*.

    The GitHub helper exposes a ``search_issues`` method that returns a
    dictionary with a ``total_count`` field, mirroring the GitHub Search API.
    """
    try:
        query = f"repo:{repo} is:issue is:open label:{label}"
        result = gh.search_issues(query)
        return int(result.get("total_count", 0))
    except GitHubError:
        return 0


def get_bounty_stats(repo: str) -> Dict[str, int]:
    """Collect statistics for a single repository.

    Returns a mapping with keys ``open``, ``claimed``, ``in_review``,
    ``stalled`` and ``proposals``.
    """
    token = os.getenv("ADT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        # No token – return zeros so that callers can still render a report.
        return {k: 0 for k in ("open", "claimed", "in_review", "stalled", "proposals")}

    gh = GitHub(token)
    stats = {
        "open": _count_issues(gh, repo, "bounty"),
        "claimed": _count_issues(gh, repo, "claimed"),
        "in_review": _count_issues(gh, repo, "in review"),
        "stalled": _count_issues(gh, repo, "stalled"),
        "proposals": _count_issues(gh, repo, "proposal"),
    }
    return stats


def generate_markdown(projects_path: Path) -> str:
    """Build a markdown table summarising bounty status for every project.

    ``projects_path`` points to the ``projects.json`` file used by the
    agent.  The function reads the file, extracts the ``repo`` field for each
    enabled project and calls :func:`get_bounty_stats` to obtain the numbers.
    """
    import json

    data = json.loads(projects_path.read_text())
    lines = [
        "# Daily bounty report",
        "",
        "| Repository | Open | Claimed | In review | Stalled | Proposals waiting |",
        "|------------|------|---------|-----------|---------|-------------------|",
    ]

    for proj in data.get("projects", []):
        if not proj.get("enabled", True):
            continue
        repo = proj["repo"]
        stats = get_bounty_stats(repo)
        lines.append(
            f"| {repo} | {stats['open']} | {stats['claimed']} | "
            f"{stats['in_review']} | {stats['stalled']} | {stats['proposals']} |"
        )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    projects_file = root / "projects.json"
    print(generate_markdown(projects_file))
