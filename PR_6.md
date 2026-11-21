Closes #6

## What this adds

A weekly payout ledger that answers "who earned what" and never pays twice. It is a
read-only reporting feature — no money is moved anywhere.

- `agent/payout.py` — builds a ledger of `week, repo, issue, contributor, amount, status` from:
  - the ADT Work form service's claims/deliveries export (JSON or CSV, local file or URL). **Only approved deliveries count**, so a passing mention of an issue cannot create a debt;
  - prices from the website's `tasks.json` (keyed by `owner/repo#issue`, by task id, or from task objects with `bounty_usd`/`price_usd`);
  - GitHub issue labels — a maintainer's `paid` label is the human approval step and makes the item `paid` instead of `owed`.
- The same contributor claiming the same issue twice is paid once. Two different contributors claiming the same issue are **flagged for a human** and excluded from the total. Deliveries with no price are flagged, never guessed.
- `agent payout` CLI: offline via `--issues sample_data/issues.json`, or live via `ADT_GITHUB_TOKEN` (reads labels from GitHub and creates the `paid` label). `--week`/`--all-weeks`, `--format summary|ledger`, and the output also goes to `GITHUB_STEP_SUMMARY`.
- `.github/workflows/payout.yml` — runs every Monday 06:17 UTC, the summary for what to pay on Wednesday. Set `ADT_WORK_EXPORT_URL` to use live data.
- `sample_data/` — a worked example (two repos both with issue #7, one already paid, one rejected delivery, one unpriced).
- README documents formats, the approval step and the schedule.

## Done when

- [x] Ledger correct for sample data — 3 entries, weeks from ISO dates, $20 paid + $20 + $75 owed
- [x] Paid items never listed as owed again — `ledger_summary()` filters on `owed`; paid entries are only shown in a separate "Already paid (not due)" table
- [x] Tests — 19 new tests, 30 total

## Verification

```
$ python -m unittest discover tests -v
Ran 30 tests in 0.037s

OK
$ python -m py_compile agent/payout.py agent/__main__.py
```

Acceptance case from the discussion: two repos each with a $20 issue #7 produce a
$40 Monday total; after a maintainer marks the first issue `paid`, the rerun shows
$20 outstanding and keeps the first payment in the ledger. Covered by
`test_two_repos_with_issue_7_total_40_then_20_when_one_paid`.

Offline sample run:

```
$ python -m agent payout --prices sample_data/tasks.json --issues sample_data/issues.json --week 2027-W01
## Owed — pay on Wednesday ($95.00)
| 2027-W01 | ADT-community/content_feed | 9 | @carol | $75.00 |
| 2027-W01 | ADT-community/locallity_ai | 7 | @bob | $20.00 |
## Already paid (1 item, not due)
| 2027-W01 | ADT-community/trans_sport | 7 | @alice | $20.00 |
```
