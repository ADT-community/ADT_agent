"""Bounty housekeeping across ADT repos.

* Stalled tasks: a claimed bounty with no word from its assignee for 5 days
  gets one polite comment asking for an update (issue #2).
* Claim instructions: every issue with a `bounty:$` label gets one comment
  explaining how to claim it (issue #5).
* Daily report: open, claimed, in review, stalled and next-wave tasks per
  repo, as Markdown (issue #3).

Every comment carries a hidden marker so the agent never posts it twice.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

STALL_DAYS = 5
STALL_MARKER = "<!-- adt-agent:stalled -->"
CLAIM_MARKER = "<!-- adt-agent:claim -->"
BOUNTY_RE = re.compile(r"^bounty:\$(\d+)$")

CLAIM_TEXT = CLAIM_MARKER + """
**This task is open for claiming.**

1. Comment "I'd like to take this".
2. Wait to be assigned before you start, so nobody duplicates work.
3. Open a pull request within 5 days of being assigned, and link this issue in it.
4. Once it's merged, you're paid in the next weekly payout run (Wednesday, for work merged Monday to Sunday). Payouts start on 6 January 2027.

Only the assigned contributor is paid. Never post payment details here."""

STALL_TEXT = STALL_MARKER + """
Hi @{who}, checking in: there's been no update on this task for {days} days. \
Could you share where it stands, or open a draft pull request? If you can't continue, \
just say so and we'll put it back in the pool. No problem either way."""


def parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def bounty_amount(issue: dict) -> int | None:
    for label in issue.get("labels", []):
        m = BOUNTY_RE.match(label["name"] if isinstance(label, dict) else label)
        if m:
            return int(m.group(1))
    return None


def label_names(issue: dict) -> set[str]:
    return {l["name"] if isinstance(l, dict) else l for l in issue.get("labels", [])}


def has_marker(comments: list[dict], marker: str) -> bool:
    return any(marker in (c.get("body") or "") for c in comments)


def linked_prs(issue_number: int, pulls: list[dict]) -> list[dict]:
    pat = re.compile(rf"#{issue_number}\b")
    return [p for p in pulls if pat.search((p.get("body") or "") + " " + (p.get("title") or ""))]


def last_activity(issue: dict, comments: list[dict], events: list[dict], prs: list[dict]) -> datetime | None:
    """Latest sign of life from the assignee(s): being assigned, commenting, or a linked PR."""
    who = {a["login"] for a in issue.get("assignees", [])}
    times = [parse_time(e["created_at"]) for e in events
             if e.get("event") == "assigned" and (e.get("assignee") or {}).get("login") in who]
    times += [parse_time(c["created_at"]) for c in comments if (c.get("user") or {}).get("login") in who]
    times += [parse_time(p["updated_at"]) for p in prs if (p.get("user") or {}).get("login") in who]
    return max(times) if times else None


@dataclass
class RepoStatus:
    repo: str
    paid_open: list[dict] = field(default_factory=list)
    claimed: list[dict] = field(default_factory=list)
    in_review: list[dict] = field(default_factory=list)
    stalled: list[dict] = field(default_factory=list)
    next_wave: int = 0
    proposals: int = 0
    actions: list[str] = field(default_factory=list)


def process_repo(gh, repo: str, *, now: datetime | None = None, dry_run: bool = False,
                 stall_days: int = STALL_DAYS) -> RepoStatus:
    now = now or datetime.now(timezone.utc)
    st = RepoStatus(repo)
    issues = [i for i in gh.issues(repo) if i.get("state") == "open"]
    pulls = gh.pulls(repo)

    for i in issues:
        names = label_names(i)
        if "agent-proposal" in names:
            st.proposals += 1
        if "next-wave" in names:
            st.next_wave += 1
        amount = bounty_amount(i)
        if amount is None:
            continue
        item = {"number": i["number"], "title": i["title"], "url": i.get("html_url", ""), "amount": amount}
        comments = gh.issue_comments(repo, i["number"])

        if not has_marker(comments, CLAIM_MARKER):
            st.actions.append(f"claim instructions on #{i['number']}")
            if not dry_run:
                gh.comment(repo, i["number"], CLAIM_TEXT)

        if not i.get("assignees"):
            st.paid_open.append(item)
            continue

        prs = linked_prs(i["number"], pulls)
        if any(p.get("state") == "open" for p in prs):
            st.in_review.append(item)
            continue

        st.claimed.append(item)
        seen = last_activity(i, comments, gh.issue_events(repo, i["number"]), prs)
        if seen and now - seen >= timedelta(days=stall_days):
            item["days"] = (now - seen).days
            st.stalled.append(item)
            if not has_marker(comments, STALL_MARKER):
                who = i["assignees"][0]["login"]
                st.actions.append(f"stall check-in on #{i['number']} (@{who}, {item['days']} days)")
                if not dry_run:
                    gh.comment(repo, i["number"], STALL_TEXT.format(who=who, days=item["days"]))
    return st


def report_md(statuses: list[RepoStatus], when: datetime | None = None) -> str:
    when = when or datetime.now(timezone.utc)
    paid_open = sum(len(s.paid_open) for s in statuses)
    money_open = sum(x["amount"] for s in statuses for x in s.paid_open)
    lines = [f"# ADT network report, {when:%A %d %B %Y}", "",
             f"**{paid_open} paid tasks open (${money_open})**, "
             f"{sum(len(s.claimed) for s in statuses)} claimed, "
             f"{sum(len(s.in_review) for s in statuses)} in review, "
             f"{sum(len(s.stalled) for s in statuses)} stalled, "
             f"{sum(s.next_wave for s in statuses)} next wave.", "",
             "| Repo | Paid open | Claimed | In review | Stalled | Next wave | Proposals |",
             "|---|---|---|---|---|---|---|"]
    for s in statuses:
        lines.append(f"| {s.repo} | {len(s.paid_open)} | {len(s.claimed)} | {len(s.in_review)} | "
                     f"{len(s.stalled)} | {s.next_wave} | {s.proposals} |")
    stalled = [(s.repo, x) for s in statuses for x in s.stalled]
    if stalled:
        lines += ["", "## Stalled"]
        lines += [f"- [{r} #{x['number']}]({x['url']}) {x['title']} ({x['days']} days quiet)" for r, x in stalled]
    actions = [(s.repo, a) for s in statuses for a in s.actions]
    if actions:
        lines += ["", "## Agent actions"]
        lines += [f"- {r}: {a}" for r, a in actions]
    return "\n".join(lines) + "\n"
