"""Weekly payout ledger: who earned what, and marking it paid.

Inputs are deliberately plain files so the data can come from the ADT Work form
service export, from the website's ``tasks.json`` or from a GitHub snapshot:

* an export of claims and deliveries, of which only approved deliveries count;
* prices, keyed by ``owner/repo#issue`` or by task id;
* the labels of the matching GitHub issues, where a maintainer's ``paid`` label
  is the human approval step that removes an item from the payout list.

Nothing here moves money: the output is a ledger and a Monday summary.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

PAID_LABEL = "paid"
PAID_LABEL_COLOR = "0e8a16"
OWED = "owed"
PAID = "paid"
APPROVED_STATUSES = frozenset({"approved", "accepted"})
DATE_FIELDS = ("delivered_at", "merged_at", "completed_at", "approved_at", "updated_at",
               "date", "timestamp", "created_at")
PRICE_FIELDS = ("bounty_usd", "price_usd", "amount_usd", "usd", "bounty", "price", "amount", "reward")
CONTRIBUTOR_FIELDS = ("contributor", "github_login", "contributor_login", "login", "github",
                     "username", "claimed_by", "who")
TASK_ID_FIELDS = ("task_id", "task", "adt_id", "external_id")
ISSUE_FIELDS = ("issue", "issue_number", "number", "issue_no")
WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")
REF_RE = re.compile(r"^(.*?)[#:/](\d+)$")
ISSUE_URL_RE = re.compile(r"/issues/(\d+)")

LabelLookup = Callable[[str, int], Iterable[str]] | Mapping[tuple[str, int], Iterable[str]]


@dataclass(frozen=True)
class Delivery:
    """An approved delivery, reduced to the fields the ledger needs."""

    repo: str
    issue: int
    contributor: str
    week: str
    task_id: str = ""

    @property
    def key(self) -> tuple[str, int]:
        return (self.repo.lower(), self.issue)


@dataclass(frozen=True)
class LedgerEntry:
    """One line of the ledger: week, repo, issue, contributor, amount, status."""

    week: str
    repo: str
    issue: int
    contributor: str
    amount: float
    status: str
    task_id: str = ""

    @property
    def key(self) -> tuple[str, int]:
        return (self.repo.lower(), self.issue)


@dataclass
class Ledger:
    entries: list[LedgerEntry] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    unpriced: list[dict] = field(default_factory=list)

    @property
    def owed(self) -> list[LedgerEntry]:
        return [e for e in self.entries if e.status == OWED]

    @property
    def paid(self) -> list[LedgerEntry]:
        return [e for e in self.entries if e.status == PAID]

    def for_week(self, week: str | None) -> list[LedgerEntry]:
        if week is None:
            return list(self.entries)
        return [e for e in self.entries if e.week == week]

    def total_owed(self, week: str | None = None) -> float:
        return round(sum(e.amount for e in self.for_week(week) if e.status == OWED), 2)


# ---------- small parsing helpers ----------

def iso_week(value: str | date | datetime | None) -> str:
    """ISO week (``2027-W02``), using the ISO year so early January is correct."""
    if isinstance(value, str) and WEEK_RE.match(value.strip()):
        return value.strip()
    d = _as_date(value)
    if d is None:
        return ""
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _first(rec: Mapping, fields: Iterable[str]) -> Any:
    for f in fields:
        value = rec.get(f)
        if value not in (None, "", [], {}):
            return value
    return None


def _text(value: Any) -> str:
    if isinstance(value, Mapping):
        return _text(_first(value, ("login", "name", "username", "value")) or "")
    return str(value or "").strip()


def _issue_number(rec: Mapping) -> int | None:
    raw = _first(rec, ISSUE_FIELDS)
    if raw is not None:
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            pass
    url = _text(rec.get("issue_url") or rec.get("html_url") or rec.get("url"))
    m = ISSUE_URL_RE.search(url)
    return int(m.group(1)) if m else None


def _repo_name(value: Any) -> str:
    name = _text(value)
    if name.endswith(".git"):
        name = name[:-4]
    return name.strip("/ ")


def _parse_repo_issue(text: str) -> tuple[str, int] | None:
    m = REF_RE.match(text.strip())
    if not m:
        return None
    return _repo_name(m.group(1)), int(m.group(2))


def _amount(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    text = _text(value).replace(",", "").replace("$", "").replace("USD", "").strip()
    try:
        return round(float(text), 2)
    except ValueError:
        return None


def _label_names(labels: Any) -> set[str]:
    if labels is None:
        return set()
    if isinstance(labels, (str, Mapping)):
        labels = [labels]
    names = set()
    for item in labels:
        name = item.get("name") if isinstance(item, Mapping) else item
        if name:
            names.add(_text(name).lower())
    return names


# ---------- loading sources ----------

def _read_text(source: str | Path) -> str:
    text = str(source)
    if text.startswith(("http://", "https://")):
        import requests
        r = requests.get(text, timeout=30, headers={"User-Agent": "adt-agent"})
        r.raise_for_status()
        return r.text
    return Path(source).read_text(encoding="utf-8")


def load_json(source: str | Path) -> Any:
    """Parse a local JSON file or an HTTP(S) URL."""
    return json.loads(_read_text(source))


def load_export(source: str | Path) -> list[dict]:
    """Return delivery records from an ADT Work export (JSON or CSV)."""
    text = _read_text(source)
    is_csv = str(source).lower().endswith(".csv") or text.lstrip().startswith(("repo,", "issue,"))
    data: Any = {} if is_csv else json.loads(text)
    if is_csv:
        records: Any = list(csv.DictReader(io.StringIO(text)))
    else:
        records = (data.get("deliveries") or data.get("records") or []) if isinstance(data, dict) else data

    claims: dict[str, dict] = {}
    if isinstance(data, dict):
        for claim in data.get("claims") or []:
            if isinstance(claim, dict):
                for key in ("id", "claim_id", "delivery_id"):
                    if claim.get(key):
                        claims[str(claim[key])] = claim

    out = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        row = dict(rec)
        parent = None
        for key in ("claim_id", "claim"):
            if row.get(key):
                parent = claims.get(str(row[key]))
                if parent:
                    break
        if parent:
            for field_ in ("contributor", "github_login", "login", "status", "task_id", "repo", "issue"):
                if not row.get(field_) and parent.get(field_):
                    row[field_] = parent[field_]
        out.append(row)
    return out


def load_prices(source: str | Path) -> Any:
    """Load ``tasks.json`` prices from a file or URL."""
    return load_json(source)


def load_issue_labels(source: str | Path) -> dict[tuple[str, int], set[str]]:
    """Load a GitHub issues snapshot: ``{repo: [issues]}`` or ``{"repo#n": labels}``."""
    return normalize_issue_labels(load_json(source))


# ---------- deliveries and prices ----------

def _is_approved(rec: Mapping) -> bool:
    if rec.get("approved") is True:
        return True
    status = _text(_first(rec, ("status", "state", "delivery_status"))).lower()
    return status in APPROVED_STATUSES


def parse_deliveries(records: Iterable[Mapping]) -> list[Delivery]:
    """Keep approved deliveries only; collapse repeated claims by the same person."""
    out: dict[tuple[str, int, str], Delivery] = {}
    for rec in records:
        if not isinstance(rec, Mapping) or not _is_approved(rec):
            continue
        repo = _repo_name(_first(rec, ("repo", "repository", "repo_full_name")))
        issue = _issue_number(rec)
        contributor = _text(_first(rec, CONTRIBUTOR_FIELDS)).lstrip("@")
        if not repo or issue is None or not contributor:
            continue
        week = iso_week(_first(rec, ("week", "iso_week")))
        if not week:
            for field_ in DATE_FIELDS:
                if rec.get(field_):
                    week = iso_week(rec[field_])
                    break
        task_id = _text(_first(rec, TASK_ID_FIELDS))
        key = (repo.lower(), issue, contributor.lower())
        out[key] = Delivery(repo=repo, issue=issue, contributor=contributor, week=week, task_id=task_id)
    return list(out.values())


def build_price_index(prices: Any) -> dict[tuple[str, Any], float]:
    """Index prices by ``("repo", repo_lower, issue)`` and ``("id", key)``."""
    index: dict[tuple[str, Any], float] = {}
    rows: list[tuple[str, Any]] = []

    if isinstance(prices, Mapping):
        for key, value in prices.items():
            if key in ("tasks", "items", "bounties", "data") and isinstance(value, (list, dict)):
                rows.extend(_price_rows(value))
            else:
                rows.append((str(key), value))
    else:
        rows.extend(_price_rows(prices))

    for key, value in rows:
        amount = None
        if isinstance(value, Mapping):
            amount = _amount(_first(value, PRICE_FIELDS))
            if amount is None:
                continue
            repo = _repo_name(_first(value, ("repo", "repository", "repo_full_name")))
            issue = _issue_number(value)
            if repo and issue is not None:
                index[("repo", repo.lower(), issue)] = amount
            for field_ in ("id", "task_id", "task", "slug"):
                if value.get(field_):
                    index[("id", _text(value[field_]).lower())] = amount
        else:
            amount = _amount(value)
        parsed = _parse_repo_issue(key)
        if parsed:
            if amount is not None:
                index[("repo", parsed[0].lower(), parsed[1])] = amount
        elif amount is not None:
            index[("id", key.strip().lower())] = amount
    return index


def _price_rows(prices: Any) -> list[tuple[str, Any]]:
    if isinstance(prices, Mapping):
        return [(str(k), v) for k, v in prices.items()]
    if isinstance(prices, list):
        rows = []
        for item in prices:
            if not isinstance(item, Mapping):
                continue
            key = _text(_first(item, ("id", "task_id", "task", "slug")))
            if not key:
                repo = _repo_name(_first(item, ("repo", "repository", "repo_full_name")))
                issue = _issue_number(item)
                key = f"{repo}#{issue}" if repo and issue is not None else ""
            rows.append((key, item))
        return rows
    return []


def find_price(index: Mapping[tuple[str, Any], float], repo: str, issue: int,
               task_id: str = "") -> float | None:
    for key in (("repo", repo.lower(), issue), ("id", task_id.lower()),
                ("id", f"{repo.lower()}#{issue}")):
        if key in index:
            return float(index[key])
    return None


# ---------- paid detection and ledger ----------

def normalize_issue_labels(source: LabelLookup | None) -> LabelLookup:
    """Turn a GitHub issues snapshot into a ``(repo, number) -> labels`` lookup."""
    if source is None or callable(source):
        return source or (lambda repo, issue: set())
    out: dict[tuple[str, int], set[str]] = {}
    issues: list[Mapping] = []

    if isinstance(source, Mapping):
        for key, value in source.items():
            if isinstance(key, tuple) and len(key) == 2 and isinstance(key[1], int):
                out[(str(key[0]).lower(), key[1])] = _label_names(value)
            elif isinstance(value, list) and all(isinstance(x, Mapping) for x in value):
                for issue in value:
                    row = dict(issue)
                    row.setdefault("repo", key)
                    issues.append(row)
            else:
                parsed = _parse_repo_issue(str(key))
                if parsed:
                    out[(parsed[0].lower(), parsed[1])] = _label_names(value)
    elif isinstance(source, list):
        issues = [x for x in source if isinstance(x, Mapping)]

    for issue in issues:
        repo = _repo_name(_first(issue, ("repo", "repository", "repo_full_name")))
        number = _issue_number(issue)
        if repo and number is not None:
            out.setdefault((repo.lower(), number), set()).update(
                _label_names(issue.get("labels", [])))
    return out


def _labels_for(lookup: LabelLookup, repo: str, issue: int) -> set[str]:
    result = lookup(repo, issue) if callable(lookup) else lookup.get((repo.lower(), issue), set())
    return _label_names(result)


def is_paid(labels: Iterable[str], paid_label: str = PAID_LABEL) -> bool:
    return paid_label.strip().lower() in _label_names(labels)


def build_ledger(deliveries: Iterable[Delivery], prices: Any, *,
                 issue_labels: LabelLookup | None = None,
                 paid_label: str = PAID_LABEL) -> Ledger:
    """Build the ledger and flag conflicts / deliveries with no known price."""
    index = build_price_index(prices)
    labels = normalize_issue_labels(issue_labels)
    ledger = Ledger()

    groups: dict[tuple[str, int], list[Delivery]] = {}
    for d in deliveries:
        groups.setdefault(d.key, []).append(d)

    for key, group in groups.items():
        contributors = {d.contributor.lstrip("@").lower() for d in group if d.contributor}
        if len(contributors) > 1:
            ledger.conflicts.append({
                "repo": group[0].repo,
                "issue": group[0].issue,
                "week": group[0].week,
                "contributors": sorted(d.contributor for d in group),
                "reason": "conflicting claims: a human must decide who gets paid",
            })
            continue
        d = group[0]
        amount = find_price(index, d.repo, d.issue, d.task_id)
        if amount is None:
            ledger.unpriced.append({
                "repo": d.repo, "issue": d.issue, "week": d.week,
                "contributor": d.contributor, "task_id": d.task_id,
            })
            continue
        status = PAID if is_paid(_labels_for(labels, d.repo, d.issue), paid_label) else OWED
        ledger.entries.append(LedgerEntry(
            week=d.week, repo=d.repo, issue=d.issue, contributor=d.contributor,
            amount=amount, status=status, task_id=d.task_id,
        ))

    ledger.entries.sort(key=lambda e: (e.week, e.repo.lower(), e.issue, e.contributor.lower()))
    return ledger


def ensure_paid_label(gh: Any, repo: str) -> None:
    """Create the maintainer's ``paid`` label if the repo does not have it yet."""
    gh.ensure_label(repo, PAID_LABEL, PAID_LABEL_COLOR,
                    "Bounty paid: keep it out of the weekly payout list")


# ---------- report ----------

def _money(amount: float) -> str:
    return f"${amount:,.2f}"


def _table(entries: list[LedgerEntry], *, with_status: bool = False) -> list[str]:
    header = "| Week | Repo | Issue | Contributor | Amount |"
    sep = "|---|---|---:|---|---:|"
    if with_status:
        header = header[:-2] + " | Status |"
        sep = "|---|---|---:|---|---:|---|"
    lines = [header, sep]
    for e in entries:
        row = (f"| {e.week or '?'} | {e.repo} | {e.issue} | @{e.contributor} | {_money(e.amount)} |")
        if with_status:
            row = row[:-2] + f" | {e.status} |"
        lines.append(row)
    return lines


def ledger_summary(ledger: Ledger, week: str | None = None, *, detailed: bool = False,
                   title: str | None = None) -> str:
    """Markdown summary. Only ``owed`` items are due; paid items never appear as owed."""
    scope = ledger.for_week(week)
    owed = [e for e in scope if e.status == OWED]
    paid = [e for e in scope if e.status == PAID]
    label = f" — {week}" if week else " — all weeks"
    lines = [title or f"# Payout summary{label}", ""]

    if detailed:
        lines += [f"## Ledger{label}", ""]
        lines += _table(scope, with_status=True) if scope else ["_No ledger entries._"]
        lines.append("")

    lines += [f"## Owed — pay on Wednesday ({_money(round(sum(e.amount for e in owed), 2))})", ""]
    lines += _table(owed) if owed else ["_Nothing owed._"]
    lines.append("")

    if paid:
        lines += [f"## Already paid ({len(paid)} item{'s' if len(paid) != 1 else ''}, not due)", ""]
        lines += _table(paid)
        lines.append("")

    conflicts = [c for c in ledger.conflicts if week is None or c["week"] == week]
    if conflicts:
        lines += ["## Needs a human: conflicting claims", ""]
        for c in conflicts:
            lines.append(f"- {c['repo']}#{c['issue']} ({c['week']}): "
                         f"{', '.join('@' + x for x in c['contributors'])} — {c['reason']}")
        lines.append("")

    unpriced = [u for u in ledger.unpriced if week is None or u["week"] == week]
    if unpriced:
        lines += ["## Needs a price in tasks.json", ""]
        for u in unpriced:
            lines.append(f"- {u['repo']}#{u['issue']} ({u['week']}): @{u['contributor']}"
                         + (f" [task {u['task_id']}]" if u["task_id"] else ""))
        lines.append("")

    lines += ["---", "", "_Generated by the ADT agent. A maintainer marks an issue with the "
              f"`{PAID_LABEL}` label once it is paid; paying is a human step._"]
    return "\n".join(lines)
