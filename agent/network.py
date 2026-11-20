"""Daily network report for ADT Work.

Tasks are listed, claimed and paid on the website (ADT Work), not on GitHub,
so this report reads the website's `tasks.json` for task status and GitHub
only for open pull requests. It never posts anything.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone

SITE_REPO = "ADT-community/adt-community.github.io"
TASKS_PATH = "tasks.json"


def load_tasks(gh, ref: str = "main") -> list[dict]:
    raw = gh.file(SITE_REPO, TASKS_PATH, ref)
    if not raw:
        raise ValueError(f"{TASKS_PATH} not found in {SITE_REPO}")
    return json.loads(raw)["tasks"]


def task_summary(tasks: list[dict]) -> list[dict]:
    """One row per project: paid open (count and $), next wave open, taken, done."""
    rows: dict[str, dict] = {}
    for t in tasks:
        r = rows.setdefault(t["project"], {"project": t["project"], "paid_open": 0, "paid_open_usd": 0,
                                           "next_open": 0, "taken": 0, "done": 0})
        status = t.get("status", "open")
        if status == "open":
            if t.get("paid"):
                r["paid_open"] += 1
                r["paid_open_usd"] += int(t.get("reward", 0))
            else:
                r["next_open"] += 1
        elif status == "done":
            r["done"] += 1
        else:
            r["taken"] += 1
    return sorted(rows.values(), key=lambda r: (-r["paid_open_usd"], r["project"]))


def open_prs(gh, repos: list[str]) -> dict[str, Counter]:
    """Open pull requests per repo, counted by author."""
    out = {}
    for repo in repos:
        out[repo] = Counter(p["user"]["login"] for p in gh.pulls(repo) if p.get("state") == "open")
    return out


def report_md(rows: list[dict], prs: dict[str, Counter], when: datetime | None = None) -> str:
    when = when or datetime.now(timezone.utc)
    paid = sum(r["paid_open"] for r in rows)
    usd = sum(r["paid_open_usd"] for r in rows)
    lines = [f"# ADT network report, {when:%A %d %B %Y}", "",
             f"**{paid} paid tasks open (${usd})**, {sum(r['next_open'] for r in rows)} next wave open, "
             f"{sum(r['taken'] for r in rows)} taken, {sum(r['done'] for r in rows)} done. "
             "Claims and deliveries are handled on ADT Work.", "",
             "| Project | Paid open | $ open | Next wave | Taken | Done |", "|---|---|---|---|---|---|"]
    lines += [f"| {r['project']} | {r['paid_open']} | ${r['paid_open_usd']} | {r['next_open']} | {r['taken']} | {r['done']} |"
              for r in rows]
    lines += ["", "## Open pull requests", "", "| Repo | Open PRs | By |", "|---|---|---|"]
    for repo, c in prs.items():
        by = ", ".join(f"{u} ({n})" for u, n in c.most_common()) or "none"
        lines.append(f"| {repo.split('/')[-1]} | {sum(c.values())} | {by} |")
    lines += ["", "Pull requests are only paid when their author claimed the task on ADT Work and the delivery was approved."]
    return "\n".join(lines) + "\n"
