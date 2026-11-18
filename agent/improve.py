"""Improvement pipeline: read a project, find what to build next, open proposals as issues."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import llm
from .github import GitHub

PROPOSAL_LABEL = "agent-proposal"
PROPOSAL_LABEL_COLOR = "e8a33d"

# What to read from a repo, and how much.
MAX_TREE_PATHS = 300
MAX_FILE_CHARS = 6000
MAX_CODE_CHARS = 40000
CODE_EXT = (".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".kt", ".dart", ".go", ".rs",
            ".java", ".swift", ".json", ".yml", ".yaml", ".toml", ".md", ".sql")
SKIP_DIRS = ("node_modules/", "dist/", "build/", ".next/", "vendor/", "venv/", "__pycache__/")
PRIORITY = ("readme", "package.json", "requirements", "pyproject", "config", "main", "app", "index")

SYSTEM = """You are the ADT agent, acting as product lead and tech lead for open source projects
built by developers across Africa. Every project is a business that must reach profit, and
contributors are paid through bounties for well-scoped tasks.

Your job: study a project and propose the most valuable next pieces of work, each one a task a
single contributor could finish in about 1-2 weeks.

Think about:
- The project's stated goals and current milestone (from its README)
- What users need that's missing (features, channels, integrations such as WhatsApp, SMS,
  USSD, mobile money)
- African realities: low-cost Android phones, expensive data, patchy connectivity, local
  languages, mobile money
- Quality gaps: bugs, missing tests, security, accessibility, performance, docs
- What moves the project toward revenue

Rules:
- Be concrete and specific to THIS codebase. Name files or components where relevant.
- Do NOT propose anything already covered by an existing issue (open or closed).
- Prefer work that unblocks the current milestone.
- Each proposal must be self-contained with clear acceptance criteria.

Suggested bounty guide (USD): S = 25-75, M = 75-250, L = 250-600. Maintainers make the final call.

Reply with ONLY a JSON object, no other text:
{"proposals": [{
  "title": "short imperative title",
  "type": "feature | improvement | bug | tests | docs | research",
  "why": "the problem or opportunity, 1-3 sentences",
  "what": "what to build, 2-5 sentences",
  "acceptance_criteria": ["...", "..."],
  "size": "S | M | L",
  "suggested_bounty_usd": 100,
  "skills": ["..."]
}]}"""


@dataclass
class Project:
    name: str
    repo: str
    focus: str = ""
    max_new: int = 3


@dataclass
class Proposal:
    title: str
    type: str
    why: str
    what: str
    acceptance_criteria: list[str] = field(default_factory=list)
    size: str = "M"
    suggested_bounty_usd: int = 0
    skills: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Proposal | None":
        title = str(d.get("title", "")).strip()
        what = str(d.get("what", "")).strip()
        if not title or not what:
            return None
        try:
            bounty = int(float(d.get("suggested_bounty_usd") or 0))
        except (TypeError, ValueError):
            bounty = 0
        return cls(
            title=title[:120],
            type=str(d.get("type", "improvement")).strip().lower() or "improvement",
            why=str(d.get("why", "")).strip(),
            what=what,
            acceptance_criteria=[str(x).strip() for x in d.get("acceptance_criteria", []) if str(x).strip()],
            size=str(d.get("size", "M")).strip().upper()[:1] or "M",
            suggested_bounty_usd=max(0, bounty),
            skills=[str(x).strip() for x in d.get("skills", []) if str(x).strip()],
        )

    def issue_body(self, project: Project) -> str:
        ac = "\n".join(f"- [ ] {c}" for c in self.acceptance_criteria) or "- [ ] To be defined by a maintainer"
        skills = ", ".join(self.skills) or "Not specified"
        bounty = f"${self.suggested_bounty_usd}" if self.suggested_bounty_usd else "To be set"
        return (
            f"## Why\n{self.why or 'See below.'}\n\n"
            f"## What to build\n{self.what}\n\n"
            f"## Acceptance criteria\n{ac}\n\n"
            f"## Details\n"
            f"| Type | Size | Suggested bounty | Skills |\n|---|---|---|---|\n"
            f"| {self.type} | {self.size} | {bounty} | {skills} |\n\n"
            f"---\n"
            f"🤖 *Proposed by the [ADT agent](https://github.com/ADT-community/ADT_agent) for **{project.name}**. "
            f"Not a bounty yet: a maintainer approves it by adding a `bounty:$…` label and removing "
            f"`{PROPOSAL_LABEL}`, or closes it if it's not wanted.*"
        )


# ---------- gathering context ----------

def _pick_files(paths: list[str]) -> list[str]:
    candidates = [p for p in paths if p.lower().endswith(CODE_EXT) and not any(d in p for d in SKIP_DIRS)
                  and not p.lower().endswith(("package-lock.json", "yarn.lock", "pnpm-lock.yaml"))]

    def score(p: str) -> tuple:
        low = p.lower()
        pri = next((i for i, k in enumerate(PRIORITY) if k in low.rsplit("/", 1)[-1]), len(PRIORITY))
        return (pri, p.count("/"), len(p))

    return sorted(candidates, key=score)


def gather(gh: GitHub, project: Project) -> dict:
    info = gh.repo(project.repo)
    branch = info.get("default_branch") or "main"
    paths = gh.tree(project.repo, branch)

    code_parts, used = [], 0
    for p in _pick_files(paths):
        if used >= MAX_CODE_CHARS:
            break
        text = gh.file(project.repo, p, branch)
        if not text:
            continue
        chunk = text[:MAX_FILE_CHARS]
        if len(text) > MAX_FILE_CHARS:
            chunk += "\n…(truncated)"
        code_parts.append(f"### {p}\n```\n{chunk}\n```")
        used += len(chunk)

    issues = gh.issues(project.repo)
    return {
        "description": info.get("description") or "",
        "branch": branch,
        "paths": paths[:MAX_TREE_PATHS],
        "code": "\n\n".join(code_parts),
        "commits": gh.commits(project.repo),
        "issues": [{"title": i["title"], "state": i["state"]} for i in issues],
        "open_proposals": sum(1 for i in issues if i["state"] == "open"
                              and any(l.get("name") == PROPOSAL_LABEL for l in i.get("labels", []))),
    }


def build_prompt(project: Project, ctx: dict, n: int) -> str:
    issues = "\n".join(f"- [{i['state']}] {i['title']}" for i in ctx["issues"]) or "(none)"
    commits = "\n".join(f"- {c}" for c in ctx["commits"]) or "(none)"
    tree = "\n".join(ctx["paths"]) or "(empty repository)"
    return (
        f"# Project: {project.name} ({project.repo})\n"
        f"{ctx['description']}\n\n"
        f"## Maintainers' focus\n{project.focus or '(none given)'}\n\n"
        f"## Existing issues (do not duplicate)\n{issues}\n\n"
        f"## Recent commits\n{commits}\n\n"
        f"## Files\n{tree}\n\n"
        f"## Key file contents\n{ctx['code'] or '(none)'}\n\n"
        f"Propose up to {n} pieces of work, most valuable first."
    )


# ---------- de-duplication ----------

_STOP = {"a", "an", "the", "to", "for", "and", "of", "in", "on", "with", "add", "support", "implement", "create"}


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in _STOP}


def is_duplicate(title: str, existing: list[str], threshold: float = 0.6) -> bool:
    t = _tokens(title)
    if not t:
        return False
    for e in existing:
        o = _tokens(e)
        if o and len(t & o) / len(t | o) >= threshold:
            return True
    return False


# ---------- pipeline ----------

def run_project(gh: GitHub, project: Project, *, dry_run: bool = False, max_open: int = 10,
                ask=llm.ask) -> dict:
    """Returns a result dict: {project, created: [...], skipped: [...], note}."""
    result = {"project": project.name, "repo": project.repo, "created": [], "skipped": [], "note": ""}
    ctx = gather(gh, project)

    room = min(project.max_new, max_open - ctx["open_proposals"])
    if room <= 0:
        result["note"] = f"{ctx['open_proposals']} proposals already waiting for review; not adding more."
        return result

    reply = ask(SYSTEM, build_prompt(project, ctx, room))
    raw = llm.parse_json(reply).get("proposals", [])

    existing = [i["title"] for i in ctx["issues"]]
    proposals: list[Proposal] = []
    for d in raw if isinstance(raw, list) else []:
        p = Proposal.from_dict(d) if isinstance(d, dict) else None
        if not p:
            continue
        if is_duplicate(p.title, existing + [q.title for q in proposals]):
            result["skipped"].append({"title": p.title, "reason": "duplicate"})
            continue
        proposals.append(p)
        if len(proposals) >= room:
            break

    if proposals and not dry_run:
        gh.ensure_label(project.repo, PROPOSAL_LABEL, PROPOSAL_LABEL_COLOR,
                        "Suggested by the ADT agent, waiting for a maintainer")

    for p in proposals:
        entry = {"title": p.title, "size": p.size, "bounty": p.suggested_bounty_usd, "type": p.type}
        if dry_run:
            entry["url"] = None
            entry["body"] = p.issue_body(project)
        else:
            issue = gh.create_issue(project.repo, p.title, p.issue_body(project), [PROPOSAL_LABEL])
            entry["url"] = issue.get("html_url")
        result["created"].append(entry)
    return result
