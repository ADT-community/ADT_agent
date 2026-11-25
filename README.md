# ADT_agent

An AI agent that helps run the whole ADT network of projects.

Part of the [ADT community](https://adt-community.github.io).

## The problem

As the number of projects grows, someone has to work out what each app needs next, turn that into tasks, keep track of bounties and notice stalled work. That doesn't scale by hand.

## What it does today: the improvement pipeline

Every Monday, on its own, the agent:

1. **Reads each project** listed in [`projects.json`](projects.json): README, key code files, file list, recent commits and every existing issue.
2. **Works out what would help most next**, using Claude: missing features (for example *"add a WhatsApp channel"*), bugs, tests, security, docs, with African realities in mind (low-cost phones, data costs, mobile money, local languages) and each project's current goal.
3. **Skips duplicates** of anything already in the project's issues.
4. **Opens each idea as a GitHub issue** labelled `agent-proposal`, with why, what to build, acceptance criteria, size and a suggested bounty.

```
projects.json ──▶ read repo ──▶ Claude: what next? ──▶ drop duplicates ──▶ open "agent-proposal" issues
                                                                                    │
                                     maintainer adds bounty:$… label ◀──────────────┘
                                                    │
                                     contributor claims it, builds it, gets paid
```

**A human stays in charge of money.** Proposals are not bounties. A maintainer approves one by adding a `bounty:$…` label and removing `agent-proposal`, or closes it.

**It won't flood a repo.** At most 3 new proposals per project per run, and none when 10 are already waiting for review (`--max-open`).

## Network status, every day

Every morning the agent takes a snapshot of the whole network (`python -m agent status`, [`.github/workflows/status.yml`](.github/workflows/status.yml)):

- tasks **open, taken and done** per project, read from the website's [`tasks.json`](https://github.com/ADT-community/adt-community.github.io/blob/main/tasks.json);
- **open issues, open pull requests and last push** for every repo in the network.

It posts the report as a comment on the **Network status** issue in this repo (it opens that issue the first time; a maintainer pins it once by hand), and writes the same data as `status.json` to the website repo, shown at [adt-community.github.io/status.html](https://adt-community.github.io/status.html). It only counts tasks; it never posts prices or pay.

## One-time setup

In this repo's **Settings → Secrets and variables → Actions**, add two secrets:

| Secret | What it is |
|---|---|
| `ANTHROPIC_API_KEY` | An API key from [console.anthropic.com](https://console.anthropic.com) |
| `ADT_GITHUB_TOKEN` | A **fine-grained personal access token** (GitHub → Settings → Developer settings) with resource owner **ADT-community**, access to the project repos, and permissions **Contents: Read** and **Issues: Read and write**. For the daily network status it also needs **Contents: Read and write** on `adt-community.github.io` |

Optional: add a variable `ADT_MODEL` to choose a different Claude model.

Then go to **Actions → Find improvements → Run workflow**. Leave **Dry run** ticked the first time to see the proposals without opening issues. After that it runs every Monday by itself.

**Add a project:** add an entry to `projects.json` with its `name`, `repo` and a `focus` sentence telling the agent what matters most right now.

## Run it locally

```bash
pip install -r requirements.txt
export ADT_GITHUB_TOKEN=...  ANTHROPIC_API_KEY=...
python -m agent improve --dry-run                       # all projects, no issues opened
python -m agent improve --project "Minibus app"         # one project, opens issues
python -m agent status --dry-run                        # network status, posts and writes nothing
python -m unittest discover tests                       # tests (no keys needed)
```

## How it earns

Powers ADT first. Once it works, sell it to other open source and remote teams that pay contributors.

## Goals

| Goal | Done when |
|---|---|
| **G1: Improvement pipeline** ✅ | Finds next improvements per project and opens them as proposals every week |
| **G2: Daily report** | Daily summary of open bounties, stalled tasks and proposals waiting for review |
| **G3: Status page** ✅ | Live network status on the ADT website (`status.json` in the website repo) |
| **G4: Payout tracking** | Tracks merged bounties and what's owed to whom |

## Starter tasks (bounty candidates)

- [x] Improvement pipeline with weekly GitHub Actions schedule
- [ ] Detect stalled tasks (claimed, no activity for 7 days) and comment on them
- [ ] Daily report of open bounties across all repos
- [x] Write `status.json` to the website repo, and a status page that reads it
- [ ] When a maintainer approves a proposal, post a "how to claim this bounty" comment
- [ ] Payout tracking: list merged PRs that closed bounty issues, per contributor

## Contribute

See the [website](https://adt-community.github.io) for how bounties and goals work, then pick an open issue in this repo.

## License

[GNU AGPL-3.0](LICENSE). You can use, study, change and share this code. If you run a modified version as a service for others, you must publish your changes under the same license.

By contributing, you agree your contributions are licensed under the same terms.
