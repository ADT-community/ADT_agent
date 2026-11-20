"""ADT agent command line.

  python -m agent improve              # all projects in projects.json
  python -m agent improve --project trans_sport --dry-run
  python -m agent stalled              # comment on stalled bounty tasks
  python -m agent stalled --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import stalled
from .github import GitHub, GitHubError
from .improve import Project, run_project
from .llm import LLMError

ROOT = Path(__file__).resolve().parent.parent


def load_projects(path: Path) -> list[Project]:
    data = json.loads(path.read_text())
    return [Project(name=p["name"], repo=p["repo"], focus=p.get("focus", ""),
                    max_new=int(p.get("max_new", data.get("max_new", 3))))
            for p in data["projects"] if p.get("enabled", True)]


def write_summary(md: str) -> None:
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(md + "\n")


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


def run_improve(gh: GitHub, args: argparse.Namespace) -> int:
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
    write_summary(md)
    if args.dry_run:
        for r in results:
            for c in r.get("created", []):
                print(f"\n---\n### {c['title']}\n\n{c.get('body', '')}")
    return 1 if results and all(r.get("error") for r in results) else 0


def run_stalled(gh: GitHub, args: argparse.Namespace) -> int:
    repos = [p.repo for p in load_projects(Path(args.config))]
    result = stalled.run(gh, repos, days=args.days, dry_run=args.dry_run)
    md = stalled.summary_md(result, args.dry_run)
    print(md)
    write_summary(md)
    for c in result["commented"]:
        print(f"---\n{c['repo']}#{c['number']}\n\n{c['body']}")
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
    sta = sub.add_parser("stalled", help="Comment once on stalled, assigned bounty tasks")
    sta.add_argument("--days", type=int, default=stalled.DEFAULT_DAYS,
                     help="Days without activity before a task is stalled (default: 5)")
    sta.add_argument("--dry-run", action="store_true", help="Show comments without posting them")
    sta.add_argument("--config", default=str(ROOT / "projects.json"))
    args = ap.parse_args(argv)

    token = os.environ.get("ADT_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("ADT_GITHUB_TOKEN is not set", file=sys.stderr)
        return 2

    gh = GitHub(token)
    if args.cmd == "stalled":
        return run_stalled(gh, args)
    return run_improve(gh, args)


if __name__ == "__main__":
    sys.exit(main())
