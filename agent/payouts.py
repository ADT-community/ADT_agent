"""Payout ledger: what is owed to whom, and what has already been paid.

Reads approved deliveries from ADT Work's records (the form service's export of claims
and deliveries) and prices from the website's `tasks.json`, then works out a weekly
ledger: week, repo, issue, contributor, amount, status.

The rules come from ADT Work: work approved Monday to Sunday is paid by the following
Wednesday, and the first payout run is Wednesday 6 January 2027 - anything approved
before then is paid in that first run.

Money is never sent here. Every Monday the agent posts a summary of what to pay on
Wednesday; a maintainer pays, then adds the `paid` label to the bounty issue. From then
on that item is never listed as owed again.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import requests

from .github import GitHub

PAID_LABEL = "paid"
PAID_LABEL_COLOR = "0e8a16"
SUMMARY_LABEL = "payout-summary"
SUMMARY_LABEL_COLOR = "5319e7"

SUMMARY_REPO = "ADT-community/ADT_agent"
DEFAULT_OWNER = "ADT-community"
TASKS_URL = "https://adt-community.github.io/tasks.json"

FIRST_PAYOUT = date(2027, 1, 6)   # the first payout Wednesday; nothing is paid before it
PAYOUT_WEEKDAY = 2                # Wednesday, as date.weekday() counts

# Field names the form service's export may use for the same thing.
APPROVED_FIELDS = ("approved", "is_approved", "approval")
APPROVED_DATE_FIELDS = ("approved_at", "approved_on", "approved_date", "approval_date")
SENT_DATE_FIELDS = ("submitted_at", "submission_date", "created_at", "sent_at", "timestamp", "date")


class PayoutError(RuntimeError):
    pass


# ---------- reading the sources ----------

def read_source(src: str) -> str:
    """The text of a local file or an http(s) URL."""
    if str(src).startswith(("http://", "https://")):
        r = requests.get(src, timeout=30, headers={"User-Agent": "adt-agent"})
        if r.status_code != 200:
            raise PayoutError(f"GET {src} -> {r.status_code}")
        return r.text
    try:
        return Path(src).read_text(encoding="utf-8")
    except OSError as e:
        raise PayoutError(f"Could not read {src}: {e}") from e


def _norm_key(k: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(k).strip().lower()).strip("_")


def _norm(rec: dict) -> dict:
    return {_norm_key(k): v for k, v in rec.items()}


def parse_records(text: str) -> list[dict]:
    """Claims and deliveries from a JSON or CSV export. Keys are normalised to snake_case."""
    stripped = text.lstrip()
    if stripped.startswith(("[", "{")):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise PayoutError(f"Export is not valid JSON: {e}") from e
        if isinstance(data, dict):
            for key in ("submissions", "records", "data", "results", "entries"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
            else:
                raise PayoutError("Export JSON has no list of submissions")
        if not isinstance(data, list):
            raise PayoutError("Export JSON is not a list of submissions")
        return [_norm(r) for r in data if isinstance(r, dict)]
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise PayoutError("Export is empty")
    return [_norm({k: v for k, v in r.items() if k is not None}) for r in rows]


def load_tasks(text: str) -> dict[str, dict]:
    """The website's tasks.json, keyed by task id."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise PayoutError(f"tasks.json is not valid JSON: {e}") from e
    tasks = data.get("tasks") if isinstance(data, dict) else data
    if not isinstance(tasks, list):
        raise PayoutError("tasks.json has no list of tasks")
    return {str(t["id"]): t for t in tasks if isinstance(t, dict) and t.get("id")}


# ---------- small helpers ----------

def _text(v) -> str:
    return "" if v is None else str(v).strip()


def _truthy(v) -> bool:
    return _text(v).lower() in {"yes", "y", "true", "1", "approved", "accepted", "ok"}


def parse_date(v) -> date | None:
    """A date from an ISO-8601 date or timestamp. None if it can't be read."""
    if isinstance(v, date):
        return v
    m = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})", _text(v))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def normalize_phone(v) -> str:
    """+234 801 234 5678 becomes +2348012345678."""
    digits = re.sub(r"[^\d]", "", _text(v))
    return "+" + digits if digits else ""


def mask_phone(phone: str) -> str:
    """Enough to recognise a contributor, not enough to publish their number."""
    return f"{phone[:4]}...{phone[-3:]}" if len(phone) >= 9 else phone


def fmt_day(d: date) -> str:
    return f"{d.strftime('%A')} {d.day} {d.strftime('%B %Y')}"


def payout_week(approved_on: date) -> tuple[date, date]:
    """The Monday-to-Sunday week that an approval falls in."""
    start = approved_on - timedelta(days=approved_on.weekday())
    return start, start + timedelta(days=6)


def pay_date_for(week_end: date) -> date:
    """Work approved Monday to Sunday is paid by the following Wednesday."""
    return max(week_end + timedelta(days=3), FIRST_PAYOUT)


def next_payout_date(today: date) -> date:
    """The next payout Wednesday on or after today."""
    ahead = (PAYOUT_WEEKDAY - today.weekday()) % 7
    return max(today + timedelta(days=ahead), FIRST_PAYOUT)


# ---------- reading the export ----------

def record_type(rec: dict) -> str:
    return _text(rec.get("type")).lower()


def is_approved(rec: dict) -> bool:
    """True when a reviewer marked this delivery approved."""
    for f in APPROVED_FIELDS:
        if _text(rec.get(f)):
            return _truthy(rec.get(f))
    if any(_text(rec.get(f)) for f in APPROVED_DATE_FIELDS):
        return True
    return _text(rec.get("status")).lower() in {"approved", "accepted"}


def approval_date(rec: dict) -> date | None:
    """When the work was approved; falls back to when it was delivered."""
    for f in APPROVED_DATE_FIELDS + SENT_DATE_FIELDS:
        d = parse_date(rec.get(f))
        if d:
            return d
    return None


def index_claims(records: list[dict]) -> dict[tuple[str, str], dict]:
    """Claims by (task id, phone) - how a delivery finds out who made it."""
    return {(_text(r.get("task")), normalize_phone(r.get("phone"))): r
            for r in records if record_type(r) == "claim"}


def contributor_of(rec: dict, claims: dict) -> tuple[str, str, str]:
    """(display name, github username, phone) for a delivery."""
    phone = normalize_phone(rec.get("phone"))
    claim = claims.get((_text(rec.get("task")), phone)) or {}
    github = _text(rec.get("github") or claim.get("github")).lstrip("@")
    name = _text(rec.get("name") or claim.get("name"))
    phone = phone or normalize_phone(claim.get("phone"))
    display = name or (f"@{github}" if github else "") or mask_phone(phone) or "unknown"
    return display, github, phone


# ---------- the ledger ----------

@dataclass
class Entry:
    task: str
    week: str
    week_start: date
    week_end: date
    pay_date: date
    repo: str
    issue: int
    contributor: str
    github: str
    phone: str
    amount: int
    status: str = "owed"
    title: str = ""
    approved_on: date | None = None
    work_link: str = ""

    @property
    def key(self) -> tuple[str, int]:
        return (self.repo, self.issue)

    @property
    def issue_url(self) -> str:
        return f"https://github.com/{self.repo}/issues/{self.issue}"

    def as_dict(self) -> dict:
        d = dict(vars(self))
        for k, v in d.items():
            if isinstance(v, date):
                d[k] = v.isoformat()
        d["issue_url"] = self.issue_url
        return d


@dataclass
class Ledger:
    entries: list[Entry] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def repos(self) -> list[str]:
        return sorted({e.repo for e in self.entries})

    def owed(self) -> list[Entry]:
        return [e for e in self.entries if e.status == "owed"]

    def due_by(self, pay_date: date) -> list[Entry]:
        """Everything still owed whose payout Wednesday has arrived - backlog included."""
        return [e for e in self.owed() if e.pay_date <= pay_date]


def build_ledger(records: list[dict], tasks: dict[str, dict]) -> Ledger:
    """One line per task with an approved delivery, priced from tasks.json."""
    ledger, claims = Ledger(), index_claims(records)

    by_task: dict[str, list[tuple[date, int, dict]]] = {}
    for i, rec in enumerate(records):
        if record_type(rec) != "delivery" or not is_approved(rec):
            continue
        if _text(rec.get("stage")).lower().startswith("progress"):
            continue                                    # a progress update is not a delivery
        task_id = _text(rec.get("task"))
        if not task_id:
            ledger.warnings.append("An approved delivery has no task id; skipped.")
            continue
        when = approval_date(rec)
        if when is None:
            ledger.warnings.append(f"{task_id}: approved, but no readable approval date; skipped.")
            continue
        by_task.setdefault(task_id, []).append((when, i, rec))

    for task_id, rows in by_task.items():
        rows.sort(key=lambda r: (r[0], r[1]))           # the approval that earned the money
        when, _, rec = rows[0]
        display, github, phone = contributor_of(rec, claims)
        people = {(lambda c: c[1] or c[2])(contributor_of(r[2], claims)) for r in rows}
        if len(people) > 1:
            others = ", ".join(sorted(mask_phone(p) if p.startswith("+") else p for p in people))
            ledger.warnings.append(
                f"{task_id}: approved deliveries from more than one contributor ({others}). "
                f"Listing {display} only - check this before paying.")

        task = tasks.get(task_id)
        if task is None:
            ledger.skipped.append({"task": task_id, "contributor": display,
                                   "reason": "not in tasks.json, so there is no price"})
            continue
        try:
            amount = int(float(task.get("reward") or 0))
        except (TypeError, ValueError):
            amount = 0
        if amount <= 0 or not task.get("paid", True):
            ledger.skipped.append({"task": task_id, "contributor": display,
                                   "reason": "no reward on this task (next wave)"})
            continue
        repo = _text(task.get("repo"))
        try:
            issue = int(task.get("issue") or 0)
        except (TypeError, ValueError):
            issue = 0
        if not repo or issue <= 0:
            ledger.skipped.append({"task": task_id, "contributor": display,
                                   "reason": "tasks.json has no repo or issue number"})
            continue

        week_start, week_end = payout_week(when)
        iso_year, iso_week, _ = week_start.isocalendar()
        ledger.entries.append(Entry(
            task=task_id,
            week=f"{iso_year}-W{iso_week:02d}",
            week_start=week_start, week_end=week_end, pay_date=pay_date_for(week_end),
            repo=repo if "/" in repo else f"{DEFAULT_OWNER}/{repo}",
            issue=issue, contributor=display, github=github, phone=phone,
            amount=amount, title=_text(task.get("title")), approved_on=when,
            work_link=_text(rec.get("work_link")),
        ))

    ledger.entries.sort(key=lambda e: (e.pay_date, e.repo, e.issue))
    return ledger


def paid_from_github(gh: GitHub, repos: list[str]) -> set[tuple[str, int]]:
    """(repo, issue) for every issue a maintainer has labelled `paid`."""
    out: set[tuple[str, int]] = set()
    for full in repos:
        for issue in gh.issues(full):
            names = [(lbl.get("name") if isinstance(lbl, dict) else lbl) or ""
                     for lbl in issue.get("labels") or []]
            if any(str(n).strip().lower() == PAID_LABEL for n in names) and issue.get("number"):
                out.add((full, int(issue["number"])))
    return out


def mark_paid(ledger: Ledger, paid: set[tuple[str, int]]) -> None:
    """A `paid` label is final: that item is never listed as owed again."""
    for e in ledger.entries:
        e.status = "paid" if e.key in paid else "owed"


# ---------- rendering ----------

def _who(e: Entry) -> str:
    who = e.contributor + (f" (@{e.github})" if e.github and not e.contributor.startswith("@") else "")
    return who + (f" - {mask_phone(e.phone)}" if e.phone else "")


def _table(entries: list[Entry]) -> str:
    head = ("| Week | Pay on | Repo | Issue | Contributor | Amount | Status |\n"
            "|---|---|---|---|---|---|---|")
    rows = [f"| {e.week} | {e.pay_date.isoformat()} | {e.repo} | [#{e.issue}]({e.issue_url}) "
            f"| {_who(e)} | ${e.amount} | {e.status} |" for e in entries]
    return "\n".join([head] + rows)


def _not_on_ledger(skipped: list[dict]) -> list[str]:
    return [f"- `{s['task']}` - {s['contributor']}: {s['reason']}" for s in skipped]


def ledger_markdown(ledger: Ledger) -> str:
    """The whole ledger, paid lines included."""
    owed = ledger.owed()
    lines = ["# Payout ledger", ""]
    if ledger.entries:
        lines += [_table(ledger.entries), "",
                  f"**Owed: ${sum(e.amount for e in owed)} over {len(owed)} item(s).** "
                  f"Already paid: {len(ledger.entries) - len(owed)} item(s)."]
    else:
        lines.append("No approved deliveries yet.")
    if ledger.skipped:
        lines += ["", "## Not on the ledger"] + _not_on_ledger(ledger.skipped)
    if ledger.warnings:
        lines += ["", "## Check these"] + [f"- WARNING: {w}" for w in ledger.warnings]
    return "\n".join(lines) + "\n"


def summary_markdown(ledger: Ledger, pay_date: date) -> str:
    """The Monday summary: what to pay on Wednesday, and how to mark it paid."""
    due = ledger.due_by(pay_date)
    lines = [f"Pay on **{fmt_day(pay_date)}**: **${sum(e.amount for e in due)}** "
             f"for {len(due)} task(s).", ""]
    lines += [_table(due) if due else "Nothing is due this week.", ""]
    later = [e for e in ledger.owed() if e.pay_date > pay_date]
    if later:
        lines += [f"_Not due yet: {len(later)} item(s), ${sum(e.amount for e in later)}, "
                  f"from the week that is still running._", ""]
    if ledger.skipped:
        lines += ["## Not on the ledger"] + _not_on_ledger(ledger.skipped) + [""]
    if ledger.warnings:
        lines += ["## Check these before paying"] + [f"- WARNING: {w}" for w in ledger.warnings] + [""]
    lines += [
        "## How to pay",
        "1. Check each line above against the delivery in ADT Work.",
        "2. Send the money to the contributor's mobile money or bank account.",
        f"3. Add the **`{PAID_LABEL}`** label to the issue you just paid.",
        "",
        f"Only lines labelled `{PAID_LABEL}` stop being owed, so nobody is paid twice and nobody "
        "is missed: anything left unlabelled turns up again next Monday.",
        "",
        "---",
        "*Posted by the [ADT agent](https://github.com/ADT-community/ADT_agent). "
        "It never sends money.*",
    ]
    return "\n".join(lines) + "\n"


# ---------- pipeline ----------

def run_payouts(gh: GitHub, *, export: str, tasks_src: str = TASKS_URL, today: date | None = None,
                post: bool = False, summary_repo: str = SUMMARY_REPO, read=read_source) -> dict:
    """Build the ledger and, when post is set, open this Monday's summary issue."""
    today = today or date.today()
    ledger = build_ledger(parse_records(read(export)), load_tasks(read(tasks_src)))
    mark_paid(ledger, paid_from_github(gh, ledger.repos))

    pay_date = next_payout_date(today)
    due = ledger.due_by(pay_date)
    title = f"Payouts for {fmt_day(pay_date)}"
    result = {"pay_date": pay_date.isoformat(), "title": title, "due": [e.as_dict() for e in due],
              "total_due": sum(e.amount for e in due),
              "entries": [e.as_dict() for e in ledger.entries],
              "skipped": ledger.skipped, "warnings": ledger.warnings,
              "ledger_markdown": ledger_markdown(ledger),
              "summary_markdown": summary_markdown(ledger, pay_date),
              "posted": None, "note": ""}

    if not post:
        return result
    if not due:
        result["note"] = "Nothing due; no summary issue opened."
        return result
    if any(i.get("title") == title for i in gh.issues(summary_repo)):
        result["note"] = f"{title!r} is already open; not posting it twice."
        return result

    gh.ensure_label(summary_repo, SUMMARY_LABEL, SUMMARY_LABEL_COLOR, "This week's payout run")
    for repo in sorted({e.repo for e in due}):
        gh.ensure_label(repo, PAID_LABEL, PAID_LABEL_COLOR, "The bounty for this issue has been paid")
    issue = gh.create_issue(summary_repo, title, result["summary_markdown"], [SUMMARY_LABEL])
    result["posted"] = issue.get("html_url")
    return result
