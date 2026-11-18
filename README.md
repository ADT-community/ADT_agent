# ADT_agent

An AI agent that helps run the whole ADT network of projects.

Part of the [ADT community](https://adt-community.github.io).

## The problem

As the number of projects grows, someone has to turn plans into tasks, keep track of bounties, notice stalled work and keep everyone informed. That doesn't scale by hand.

## What we're building

- **Turns plans into tasks and bounties:** reads a project's goals and drafts issues with clear scope and a suggested bounty
- **Monitors everything:** open bounties, claimed tasks, pull requests waiting for review, payouts due, across every ADT repo
- **Flags problems:** stalled tasks (no activity for 7 days), unclaimed bounties, projects falling behind
- **Reports:** a daily summary, plus a live status page on the ADT website

## How it earns

Powers ADT first. Once it works, sell it to other open source and remote teams that pay contributors.

## Suggested stack

- **Runs as:** a GitHub App, scheduled with GitHub Actions (cron)
- **Language:** Python or TypeScript
- **AI:** an LLM API for drafting tasks and summaries
- **Output:** writes `status.json` to the website repo, which the site displays
- **Secrets:** stored in GitHub Actions secrets, never in code

## Goals

| Goal | Done when |
|---|---|
| **G1: Daily report** | Posts a daily summary of open bounties and stalled tasks across all ADT repos |
| **G2: Status page** | Live network status on the ADT website |
| **G3: Task drafting** | Drafts bounty issues from a project's goals for a maintainer to approve |
| **G4: Payout tracking** | Tracks merged bounties and what's owed to whom |

## Starter tasks (bounty candidates)

- [ ] GitHub App setup with read access to the org's repos
- [ ] Script: list open issues with `bounty:` labels across all repos
- [ ] Detect stalled tasks (claimed, no activity for 7 days)
- [ ] GitHub Actions workflow that runs the agent daily
- [ ] Write `status.json` to the website repo
- [ ] Status page on the website that reads `status.json`

## Contribute

See the [website](https://adt-community.github.io) for how bounties and goals work, then pick an open issue in this repo.
