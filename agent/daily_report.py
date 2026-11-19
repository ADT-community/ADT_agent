import argparse
import os
import sys
import requests
from datetime import datetime, timezone

GITHUB_API_BASE = "https://api.github.com"


def get_github_token():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise EnvironmentError("GITHUB_TOKEN environment variable is not set")
    return token


def get_headers(token):
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }


def fetch_open_bounties(repo, token):
    headers = get_headers(token)
    url = f"{GITHUB_API_BASE}/repos/{repo}/issues"
    params = {
        "state": "open",
        "labels": "bounty",
        "per_page": 100,
    }
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    return response.json()


def find_network_status_issue(repo, token):
    headers = get_headers(token)
    url = f"{GITHUB_API_BASE}/repos/{repo}/issues"
    params = {
        "state": "open",
        "per_page": 100,
    }
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    issues = response.json()
    for issue in issues:
        if issue["title"].lower() == "network status": 
            return issue
    return None


def format_report(bounties):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"## Daily Network Status Report",
        f"*Generated: {now}*",
        "",
        f"### Open Bounties ({len(bounties)})",
        "",
    ]
    if not bounties:
        lines.append("No open bounties at this time.")
    else:
        for bounty in bounties:
            lines.append(f"- [{bounty['title']}]({bounty['html_url']})")
    lines.append("")
    lines.append("---")
    lines.append("_This report is generated automatically by the ADT agent._")
    return "\n".join(lines)


def post_comment(repo, issue_number, body, token):
    headers = get_headers(token)
    url = f"{GITHUB_API_BASE}/repos/{repo}/issues/{issue_number}/comments"
    data = {"body": body}
    response = requests.post(url, headers=headers, json=data)
    response.raise_for_status()
    return response.json()


def main():
    parser = argparse.ArgumentParser(description="Post daily network status report")
    parser.add_argument("--repo", required=True, help="Repository in format owner/repo")
    parser.add_argument("--dry-run", action="store_true", help="Print report without posting")
    args = parser.parse_args()

    token = get_github_token()
    bounties = fetch_open_bounties(args.repo, token)
    report = format_report(bounties)

    if args.dry_run:
        print(report)
        return

    issue = find_network_status_issue(args.repo, token)
    if not issue:
        print("Error: 'Network status' issue not found.", file=sys.stderr)
        sys.exit(1)

    post_comment(args.repo, issue["number"], report, token)
    print(f"Report posted to issue #{issue['number']}")


if __name__ == "__main__":
    main()
