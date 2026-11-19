'''ADT agent command line.

  python -m agent improve              # all projects in projects.json
  python -m agent improve --project trans_sport --dry-run
'''
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("improve", help="Find improvements and open them as issues")
    imp.add_argument("--project", help="Only run for this project name")
    imp.add_argument("--dry-run", action="store_true", help="Print proposals, don't open issues")
    imp.add_argument("--config", default=str(ROOT / "projects.json"))
    imp.add_argument("--max-open", type=int, default=int(os.environ.get("ADT_MAX_OPEN", 10)),
                     help="Skip a project when this many proposals are already waiting")

    stalled = sub.add_parser("stalled", help="Detect stalled bounty issues and comment on them")
    stalled.add_argument("--dry-run", action="store_true", help="Show what would be commented without posting")

    args = ap.parse_args(argv)

    token = os.environ.get("ADT_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("ADT_GITHUB_TOKEN is not set")
        return 1

    gh = GitHub(token)

    if args.cmd == "improve":
        # Existing improve workflow (unchanged)
        projects = load_projects(Path(args.config))
        selected = [p for p in projects if (args.project is None or p.name == args.project)]
        results = []
        for proj in selected:
            try:
                created, skipped, note = run_project(gh, proj, dry_run=args.dry_run,
                                                    max_open=args.max_open)
                results.append({"project": proj.name, "created": created,
                                "skipped": skipped, "note": note})
            except (GitHubError, LLMError) as exc:
                results.append({"project": proj.name, "error": str(exc)})
        print(summary_md(results, dry_run=args.dry_run))
        return 0

    if args.cmd == "stalled":
        # New stalled detection command
        from .stalled import run_stalled
        run_stalled(gh, dry_run=args.dry_run)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())