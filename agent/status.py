"""Network status: a daily snapshot of the whole ADT network.

Every day the agent:

1. reads the website's `tasks.json` (the task list behind ADT Work) and, for every repo in the
   network, its open issues, open pull requests and last push;
2. posts the report as a comment on the pinned "Network status" issue in this repo;
3. writes the same data as `status.json` to the website repo, where `status.html` shows it.

Read-only apart from that one comment and that one file. No pay information is posted anywhere:
the report counts tasks, it does not price them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from .github import GitHubError

ORG = "ADT-community"
WEBSITE_REPO = f"{ORG}/adt-community.github.io"
STATUS_REPO = f"{ORG}/ADT_agent"
STATUS_ISSUE_TITLE = "Network status"
STATUS_PATH = "status.json"
TASK_STATES = ("open", "taken", "done")


@dataclass
class StatusResult:
    status: dict
    markdown: str
    issue_url: str | None = None
    comment_url: str | None = None
    file_url: str | None = None


def _full(repo: str) -> str:
    return repo if "/" in repo else f"{ORG}/{repo}"


def load_tasks(gh, website_repo: str = WEBSITE_REPO, ref: str = "main") -> list[dict]:
    text = gh.file(website_repo, "tasks.json", ref)
    if text is None:
        raise GitHubError(f"Could not read tasks.json from {website_repo}")
    data = json.loads(text)
    return data.get("tasks", data) if isinstance(data, dict) else data


def network_repos(tasks: list[dict], project_repos: list[str] = ()) -> list[str]:
    """Every repo named by a task or in projects.json, once, case-insensitively, in first-seen order."""
    seen: dict[str, str] = {}
    for r in [t.get("repo", "") for t in tasks] + list(project_repos):
        if r:
            full = _full(r)
            seen.setdefault(full.lower(), full)
    return list(seen.values())


def build_status(gh, tasks: list[dict], repos: list[str], now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)

    projects: dict[str, dict] = {}
    for t in tasks:
        name = t.get("project") or t.get("repo") or "Other"
        p = projects.setdefault(name, {"name": name, "repo": _full(t.get("repo", "")) if t.get("repo") else None,
                                       **{s: 0 for s in TASK_STATES}, "total": 0})
        state = t.get("status", "open")
        if state in TASK_STATES:
            p[state] += 1
        p["total"] += 1

    repo_rows = []
    for full in repos:
        row = {"repo": full, "url": f"https://github.com/{full}"}
        try:
            info = gh.repo(full)
            row["last_push"] = info.get("pushed_at")
            row["open_issues"] = len(gh.open_issues(full))
            pulls = gh.pulls(full)
            row["open_pull_requests"] = len(pulls)
            row["contributors_with_open_prs"] = len({(p.get("user") or {}).get("login") for p in pulls} - {None})
        except GitHubError as e:
            row["error"] = str(e)[:200]
        repo_rows.append(row)

    ok = [r for r in repo_rows if "error" not in r]
    totals = {s: sum(p[s] for p in projects.values()) for s in TASK_STATES}
    totals["tasks"] = sum(p["total"] for p in projects.values())
    totals["open_pull_requests"] = sum(r["open_pull_requests"] for r in ok)
    totals["open_issues"] = sum(r["open_issues"] for r in ok)
    totals["repos"] = len(repo_rows)

    return {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "totals": totals,
        "projects": sorted(projects.values(), key=lambda p: p["name"]),
        "repos": repo_rows,
    }


def render_md(status: dict) -> str:
    t = status["totals"]
    day = status["generated_at"][:10]
    lines = [
        f"## Network status, {day}",
        "",
        f"**{t['open']}** tasks open · **{t['taken']}** taken · **{t['done']}** done · "
        f"**{t['open_pull_requests']}** open pull requests across {t['repos']} repos",
        "",
        "| Project | Open | Taken | Done |",
        "|---|---:|---:|---:|",
    ]
    for p in status["projects"]:
        lines.append(f"| {p['name']} | {p['open']} | {p['taken']} | {p['done']} |")
    lines += ["", "| Repo | Open issues | Open PRs | Last push |", "|---|---:|---:|---|"]
    for r in status["repos"]:
        name = r["repo"].split("/", 1)[-1]
        if "error" in r:
            lines.append(f"| [{name}]({r['url']}) | ⚠️ couldn't read | | |")
        else:
            lines.append(f"| [{name}]({r['url']}) | {r['open_issues']} | {r['open_pull_requests']} | "
                         f"{(r.get('last_push') or '')[:10]} |")
    lines += ["", "Tasks are listed and claimed on [ADT Work](https://adt-community.github.io/work.html). "
              "Live page: [adt-community.github.io/status.html](https://adt-community.github.io/status.html)."]
    return "\n".join(lines)


def find_status_issue(gh, repo: str = STATUS_REPO, title: str = STATUS_ISSUE_TITLE) -> dict | None:
    for i in gh.open_issues(repo):
        if i.get("title", "").strip().lower() == title.lower():
            return i
    return None


def run_status(gh, project_repos: list[str] = (), dry_run: bool = False,
               website_repo: str = WEBSITE_REPO, status_repo: str = STATUS_REPO,
               now: datetime | None = None) -> StatusResult:
    tasks = load_tasks(gh, website_repo)
    status = build_status(gh, tasks, network_repos(tasks, project_repos), now=now)
    md = render_md(status)
    res = StatusResult(status=status, markdown=md)
    if dry_run:
        return res

    issue = find_status_issue(gh, status_repo)
    if issue is None:
        issue = gh.create_issue(status_repo, STATUS_ISSUE_TITLE,
                                "The ADT agent posts the daily network report here. "
                                "Maintainers: pin this issue so it stays at the top.", [])
    res.issue_url = issue.get("html_url")
    res.comment_url = gh.comment(status_repo, issue["number"], md).get("html_url")

    text = json.dumps(status, indent=2, ensure_ascii=False) + "\n"
    put = gh.put_file(website_repo, STATUS_PATH, text, f"Network status {status['generated_at'][:10]}")
    res.file_url = (put.get("content") or {}).get("html_url")
    return res
