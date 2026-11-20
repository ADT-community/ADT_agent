# ADT_agent

An AI agent that helps run the whole ADT network of projects.

Part of the [ADT community](https://adt-community.github.io).

## The problem

As the number of projects grows, someone has to work out what each app needs next, turn that into tasks, keep track of tasks and notice stalled work. That doesn't scale by hand.

## What it does today: the improvement pipeline

Every Monday, on its own, the agent:

1. **Reads each project** listed in [`projects.json`](projects.json): README, key code files, file list, recent commits and every existing issue.
2. **Works out what would help most next**, using Claude: missing features (for example *"add a WhatsApp channel"*), bugs, tests, security, docs, with African realities in mind (low-cost phones, data costs, mobile money, local languages) and each project's current goal.
3. **Skips duplicates** of anything already in the project's issues.
4. **Opens each idea as a GitHub issue** labelled `agent-proposal`, with why, what to build, acceptance criteria, and size.

```
projects.json ──▶ read repo ──▶ Claude: what next? ──▶ drop duplicates ──▶ open "agent-proposal" issues
                                                                                    │
                                     maintainer adds it to ADT Work  ◀──────────────┘
                                                    │
                                     contributor claims it, builds it, gets paid
```

**A human stays in charge of money.** Proposals are not paid tasks. A maintainer approves one by adding it to ADT Work (`tasks.json` on the website) and removing `agent-proposal`, or closes it.

**It won't flood a repo.** At most 3 new proposals per project per run, and none when 10 are already waiting for review (`--max-open`).

## Network report (daily)

Every day at 07:43 UTC, `python -m agent report` builds a read-only report in the Actions run summary:

- paid tasks open and their total in dollars, next-wave tasks, taken and done, per project, read from the website's [`tasks.json`](https://github.com/ADT-community/adt-community.github.io/blob/main/tasks.json)
- open pull requests per repo and who opened them

Tasks are listed, claimed and paid on [ADT Work](https://adt-community.github.io/work.html), not on GitHub, so the agent never posts claim or payment comments. Uses the `ADT_GITHUB_TOKEN` secret (needed to read private repos).

## One-time setup

In this repo's **Settings → Secrets and variables → Actions**, add two secrets:

| Secret | What it is |
|---|---|
| `ANTHROPIC_API_KEY` | An API key from [console.anthropic.com](https://console.anthropic.com) |
| `ADT_GITHUB_TOKEN` | A **fine-grained personal access token** (GitHub → Settings → Developer settings) with resource owner **ADT-community**, access to the project repos, and permissions **Contents: Read** and **Issues: Read and write** |

Optional: add a variable `ADT_MODEL` to choose a different Claude model.

Then go to **Actions → Find improvements → Run workflow**. Leave **Dry run** ticked the first time to see the proposals without opening issues. After that it runs every Monday by itself.

**Add a project:** add an entry to `projects.json` with its `name`, `repo` and a `focus` sentence telling the agent what matters most right now.

## Run it locally

```bash
pip install -r requirements.txt
export ADT_GITHUB_TOKEN=...  ANTHROPIC_API_KEY=...
python -m agent improve --dry-run                       # all projects, no issues opened
python -m agent improve --project "Minibus app"         # one project, opens issues
python -m unittest discover tests                       # tests (no keys needed)
```

## How it earns

Powers ADT first. Once it works, sell it to other open source and remote teams that pay contributors.

## Goals

| Goal | Done when |
|---|---|
| **G1: Improvement pipeline** ✅ | Finds next improvements per project and opens them as proposals every week |
| **G2: Daily report** | Daily summary of open ADT Work tasks and pull requests |
| **G3: Status page** | Live network status on the ADT website (`status.json` in the website repo) |
| **G4: Payout tracking** | Tracks approved ADT Work deliveries and what's owed to whom |

## Starter tasks

- [x] Improvement pipeline with weekly GitHub Actions schedule
- [x] Daily network report of open tasks (from ADT Work) and pull requests
- [ ] Write `status.json` to the website repo, and a status page that reads it
- [ ] Payout tracking: list approved ADT Work deliveries, per contributor

## Contribute

Paid tasks are listed, claimed and paid on [ADT Work](https://adt-community.github.io/work.html), open to people living in Africa.

## License

[GNU AGPL-3.0](LICENSE). You can use, study, change and share this code. If you run a modified version as a service for others, you must publish your changes under the same license.

By contributing, you agree your contributions are licensed under the same terms.
