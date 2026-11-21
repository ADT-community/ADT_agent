"""ADT agent command line.

  python -m agent improve              # all projects in projects.json
  python -m agent improve --project trans_sport --dry-run
  python -m agent payout               # this week's ledger and who to pay
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import requests

from . import payout
from .github import GitHub, GitHubError
from .improve import Project, run_project
from .llm import LLMError

ROOT = Path(__file__).resolve().parent.parent


def load_projects(path: Path) -> list[Project]:
    data = json.loads(path.read_text())
    return [Project(name=p["name"], repo=p["repo"], focus=p.get("focus", ""),
                    max_new=int(p.get("max_new", data.get("max_new", 3))))
            for p in data["projects"] if p.get("enabled", True)]


def summary_md(results: list[dict], dry_run: bool) -> str:
    lines = [f"# ADT agent: improvement run{' (dry run)' if dry_run else ''}", ""]
    for r in results:
        lines.append(f"## {r['project']}")
        if r.get("error"):
            lines.append(f"⚠️ {r['error']}")
        if r.get("note"):
            lines.append(r["note"])
        for c in r.get("created", []):
            link = f"[{c['title']}]({c['url']})" if c.get("url") else c["title"]
            bounty = f"${c['bounty']}" if c.get("bounty") else "no bounty suggested"
            lines.append(f"- {link} ({c['type']}, size {c['size']}, {bounty})")
        for s in r.get("skipped", []):
            lines.append(f"- ~~{s['title']}~~ ({s['reason']})")
        if not (r.get("created") or r.get("skipped") or r.get("note") or r.get("error")):
            lines.append("No proposals this time.")
        lines.append("")
    return "\n".join(lines)


def cmd_payout(args) -> int:
    """Build the weekly ledger from approved deliveries and print the summary."""
    sample = ROOT / "sample_data"
    if str(args.deliveries).startswith(str(sample)):
        print("⚠️ Using sample deliveries: pass --deliveries with the ADT Work export for real payouts.",
              file=sys.stderr)

    try:
        deliveries = payout.parse_deliveries(payout.load_export(args.deliveries))
        prices = payout.load_prices(args.prices) if args.prices else {}
    except (OSError, ValueError, requests.RequestException) as e:
        print(f"Could not read payout inputs: {e}", file=sys.stderr)
        return 2

    token = os.environ.get("ADT_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    labels = None
    if args.issues:
        try:
            labels = payout.load_issue_labels(args.issues)
        except (OSError, ValueError) as e:
            print(f"Could not read the issues snapshot: {e}", file=sys.stderr)
            return 2
    elif token and deliveries:
        gh = GitHub(token)
        if not prices:
            text = gh.file(args.tasks_repo, "tasks.json", None)
            if text:
                prices = json.loads(text)
            else:
                print(f"⚠️ No tasks.json found in {args.tasks_repo}", file=sys.stderr)
        labels = {}
        for repo in sorted({d.repo for d in deliveries}):
            try:
                issues = gh.issues(repo)
            except GitHubError as e:
                print(f"⚠️ Could not read labels for {repo}: {e}", file=sys.stderr)
                continue
            for issue in issues:
                if "pull_request" in issue or issue.get("number") is None:
                    continue
                names = [l.get("name", "") if isinstance(l, dict) else l
                         for l in issue.get("labels", [])]
                labels[(repo.lower(), int(issue["number"]))] = [n for n in names if n]
            try:
                payout.ensure_paid_label(gh, repo)
            except GitHubError:
                pass

    ledger = payout.build_ledger(deliveries, prices, issue_labels=labels)
    week = args.week or (None if args.all_weeks else payout.iso_week(date.today()))
    text = payout.ledger_summary(ledger, week, detailed=(args.format == "ledger"))
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("improve", help="Find improvements and open them as issues")
    imp.add_argument("--project", help="Only run for this project name")
    imp.add_argument("--dry-run", action="store_true", help="Print proposals, don't open issues")
    imp.add_argument("--config", default=str(ROOT / "projects.json"))
    imp.add_argument("--max-open", type=int, default=int(os.environ.get("ADT_MAX_OPEN", 10)),
                     help="Skip a project when this many proposals are already waiting")

    pay = sub.add_parser("payout", help="Weekly ledger of what is owed, and what is paid")
    pay.add_argument("--deliveries", default=str(ROOT / "sample_data" / "deliveries.json"),
                     help="ADT Work claims/deliveries export: JSON, CSV or URL")
    pay.add_argument("--prices", help="Website tasks.json: local path or URL")
    pay.add_argument("--issues", help="Optional GitHub issues snapshot (JSON) with labels")
    pay.add_argument("--tasks-repo", default="ADT-community/ADT-community.github.io",
                     help="Repo to read tasks.json from when --prices is not given")
    pay.add_argument("--week", help="ISO week, e.g. 2027-W01 (default: current week)")
    pay.add_argument("--all-weeks", action="store_true", help="Include every week in the report")
    pay.add_argument("--format", choices=("summary", "ledger"), default="summary")
    args = ap.parse_args(argv)

    if args.cmd == "payout":
        return cmd_payout(args)

    token = os.environ.get("ADT_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("ADT_GITHUB_TOKEN is not set", file=sys.stderr)
        return 2

    projects = load_projects(Path(args.config))
    if args.project:
        projects = [p for p in projects if p.name == args.project]
        if not projects:
            print(f"No project named {args.project!r} in {args.config}", file=sys.stderr)
            return 2

    gh = GitHub(token)
    results = []
    for p in projects:
        print(f"→ {p.name} ({p.repo})", file=sys.stderr)
        try:
            results.append(run_project(gh, p, dry_run=args.dry_run, max_open=args.max_open))
        except (GitHubError, LLMError) as e:
            results.append({"project": p.name, "repo": p.repo, "created": [], "skipped": [], "error": str(e)})

    md = summary_md(results, args.dry_run)
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(md + "\n")
    if args.dry_run:
        for r in results:
            for c in r.get("created", []):
                print(f"\n---\n### {c['title']}\n\n{c.get('body', '')}")
    return 1 if results and all(r.get("error") for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
