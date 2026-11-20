## Summary

Implements automatic detection of stalled bounty tasks (issue #2).

- `agent/stalled.py` finds **open** issues that have a `bounty:$…` label and an assignee, and whose last activity is older than 5 days (`--days` configures the threshold).
- Posts **one** polite comment asking the assignee for an update. A hidden marker in that comment is detected on later runs, so the agent **never comments twice on the same issue**.
- `--dry-run` shows the stalled issues and the exact comments without posting anything.
- New CLI command: `python -m agent stalled [--days N] [--dry-run] [--config PATH]`.
- `GitHub.issue_comments()` / `GitHub.create_comment()` added to the REST client.
- Daily GitHub Actions workflow (`.github/workflows/stalled.yml`) enforces the 5-day rule automatically; it needs only the built-in `GITHUB_TOKEN` with `issues: write`.
- README documents the new command; the starter-task checklist is updated.
- Unassigning is intentionally out of scope.

## Tests

`python -m unittest discover -s tests -v` — **25 tests, all passing** (13 new tests in `tests/test_stalled.py` with fake issue/comment data; 12 existing tests unchanged and green).

Coverage:
- stalled issues found correctly (bounty prefix variants, assignee / `assignees`, timezone-aware `Z` timestamps, 5-day boundary);
- ignored: closed issues, PRs, unassigned, no bounty label, recent activity, malformed timestamps;
- never comments twice: marked comment from a previous run is skipped;
- `--dry-run` posts nothing;
- multiple repos and custom `--days` threshold.

Also verified: `python -m py_compile` on all changed Python files and `python -m agent stalled --help`.

Closes #2
