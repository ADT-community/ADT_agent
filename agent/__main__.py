"""ADT agent command line.

  python -m agent improve              # all projects in projects.json
  python -m agent improve --project trans_sport --dry-run
  python -m agent payouts --export samples/adt-work-export.sample.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from . import payouts
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


def write_step_summary(md: str) -> None:
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(md + "\n")


def payouts_cmd(gh: GitHub, args) -> int:
    today = None
    if args.today:
        try:
            today = date.fromisoformat(args.today)
        except ValueError:
            print(f"--today must be YYYY-MM-DD, not {args.today!r}", file=sys.stderr)
            return 2
    try:
        r = payouts.run_payouts(gh, export=args.export, tasks_src=args.tasks, today=today,
                                post=args.post, summary_repo=args.summary_repo)
    except (payouts.PayoutError, GitHubError) as e:
        print(f"Payout ledger failed: {e}", file=sys.stderr)
        return 1

    md = f"{r['ledger_markdown']}\n# {r['title']}\n\n{r['summary_markdown']}"
    print(md)
    write_step_summary(md)
    if args.json_out:
        keep = ("pay_date", "title", "total_due", "due", "entries", "skipped", "warnings")
        Path(args.json_out).write_text(json.dumps({k: r[k] for k in keep}, indent=2),
                                       encoding="utf-8")
        print(f"Ledger written to {args.json_out}", file=sys.stderr)
    for line in (r["note"], f"Summary issue: {r['posted']}" if r["posted"] else ""):
        if line:
            print(line, file=sys.stderr)
    return 0


def improve_cmd(gh: GitHub, args) -> int:
    projects = load_projects(Path(args.config))
    if args.project:
        projects = [p for p in projects if p.name == args.project]
        if not projects:
            print(f"No project named {args.project!r} in {args.config}", file=sys.stderr)
            return 2

    results = []
    for p in projects:
        print(f"→ {p.name} ({p.repo})", file=sys.stderr)
        try:
            results.append(run_project(gh, p, dry_run=args.dry_run, max_open=args.max_open))
        except (GitHubError, LLMError) as e:
            results.append({"project": p.name, "repo": p.repo, "created": [], "skipped": [], "error": str(e)})

    md = summary_md(results, args.dry_run)
    print(md)
    write_step_summary(md)
    if args.dry_run:
        for r in results:
            for c in r.get("created", []):
                print(f"\n---\n### {c['title']}\n\n{c.get('body', '')}")
    return 1 if results and all(r.get("error") for r in results) else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agent")
    sub = ap.add_subparsers(dest="cmd", required=True)

    imp = sub.add_parser("improve", help="Find improvements and open them as issues")
    imp.add_argument("--project", help="Only run for this project name")
    imp.add_argument("--dry-run", action="store_true", help="Print proposals, don't open issues")
    imp.add_argument("--config", default=str(ROOT / "projects.json"))
    imp.add_argument("--max-open", type=int, default=int(os.environ.get("ADT_MAX_OPEN", 10)),
                     help="Skip a project when this many proposals are already waiting")

    pay = sub.add_parser("payouts", help="Weekly payout ledger: what's owed and what's paid")
    pay.add_argument("--export", required=True,
                     help="ADT Work's records: path or URL of the claims and deliveries export")
    pay.add_argument("--tasks", default=payouts.TASKS_URL,
                     help="The website's tasks.json, for prices: path or URL")
    pay.add_argument("--post", action="store_true",
                     help="Open this Monday's payout summary issue (off by default)")
    pay.add_argument("--summary-repo", default=payouts.SUMMARY_REPO,
                     help="Where the weekly summary issue is opened")
    pay.add_argument("--json", dest="json_out", help="Also write the ledger as JSON to this path")
    pay.add_argument("--today", help="Treat this date as today (YYYY-MM-DD), for testing")

    args = ap.parse_args(argv)

    token = os.environ.get("ADT_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("ADT_GITHUB_TOKEN is not set", file=sys.stderr)
        return 2

    gh = GitHub(token)
    return payouts_cmd(gh, args) if args.cmd == "payouts" else improve_cmd(gh, args)


if __name__ == "__main__":
    sys.exit(main())
