## Summary

Adds a read-only daily bounty report for the whole ADT network (issue #3).

- `agent/report.py` classifies every open issue of each repo in `projects.json` into five mutually exclusive buckets — **open bounties** (`bounty:$…`, unassigned), **claimed** (assigned), **in review** (`in review`), **stalled** (assigned with no activity for 7 days, configurable) and **proposals waiting** (`agent-proposal`) — and renders a summary table plus per-repo linked lists as Markdown. Closed issues, pull requests and unrelated issues are ignored; a repo whose issues cannot be read is shown with an error instead of being skipped.
- New CLI command `python -m agent report [--config PATH] [--project NAME] [--stalled-days N]` prints the Markdown (and appends it to `GITHUB_STEP_SUMMARY` when set). No Claude call and no writes to GitHub.
- `tests/test_report.py`: 11 tests with fake issues cover every category, the stalled boundary, bounty amounts, all projects in `projects.json`, per-repo errors and the CLI. The command is documented in the README.

Posting or scheduling the report is intentionally out of scope (separate task).

## Tests

- `python -m unittest discover -s tests -v` — **23 tests, all passing** (12 existing + 11 new).
- `python -m py_compile` on all `agent/` and `tests/` modules — OK.
- `python -m agent --help` and `python -m agent report --help` — OK.
- Live read-only `python -m agent report` with a GitHub token — produced a report for all 4 repos (all zero counts: no bounty/proposal labels are currently set on those repos).

Closes #3
