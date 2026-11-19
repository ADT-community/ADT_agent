"""Minimal GitHub REST client for the ADT agent."""
from __future__ import annotations

import base64
import time

import requests

API = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


class GitHub:
    def __init__(self, token: str, session: requests.Session | None = None):
        self.s = session or requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "adt-agent",
        })

    def _req(self, method: str, path: str, ok=(200, 201), **kw):
        for attempt in range(3):
            r = self.s.request(method, API + path, timeout=30, **kw)
            if r.status_code in (502, 503, 504) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            break
        if r.status_code not in ok:
            raise GitHubError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r

    # ---- reading ----
    def repo(self, full: str) -> dict:
        return self._req("GET", f"/repos/{full}").json()

    def tree(self, full: str, branch: str) -> list[str]:
        """All file paths in the repo. Empty list for an empty repo."""
        try:
            data = self._req("GET", f"/repos/{full}/git/trees/{branch}", params={"recursive": "1"}).json()
        except GitHubError as e:
            if "409" in str(e) or "404" in str(e):  # empty repository / no branch yet
                return []
            raise
        return [t["path"] for t in data.get("tree", []) if t.get("type") == "blob"]

    def file(self, full: str, path: str, ref: str) -> str | None:
        try:
            data = self._req("GET", f"/repos/{full}/contents/{path}", params={"ref": ref}).json()
        except GitHubError:
            return None
        if data.get("encoding") != "base64":
            return None
        try:
            return base64.b64decode(data["content"]).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return None

    def commits(self, full: str, n: int = 15) -> list[str]:
        try:
            data = self._req("GET", f"/repos/{full}/commits", params={"per_page": n}).json()
        except GitHubError:
            return []
        return [c["commit"]["message"].split("\n")[0] for c in data]

    def issues(self, full: str) -> list[dict]:
        """Open and recently closed issues (not pull requests)."""
        out: list[dict] = []
        for page in (1, 2, 3):
            data = self._req("GET", f"/repos/{full}/issues",
                             params={"state": "all", "per_page": 100, "page": page}).json()
            out += [i for i in data if "pull_request" not in i]
            if len(data) < 100:
                break
        return out

    def pulls(self, full: str) -> list[dict]:
        """Open and recently closed pull requests."""
        return self._req("GET", f"/repos/{full}/pulls", params={"state": "all", "per_page": 100}).json()

    def issue_comments(self, full: str, number: int) -> list[dict]:
        return self._req("GET", f"/repos/{full}/issues/{number}/comments", params={"per_page": 100}).json()

    def issue_events(self, full: str, number: int) -> list[dict]:
        return self._req("GET", f"/repos/{full}/issues/{number}/events", params={"per_page": 100}).json()

    # ---- writing ----
    def comment(self, full: str, number: int, body: str) -> dict:
        return self._req("POST", f"/repos/{full}/issues/{number}/comments", json={"body": body}).json()

    def ensure_label(self, full: str, name: str, color: str, description: str = "") -> None:
        self._req("POST", f"/repos/{full}/labels", ok=(201, 422),
                  json={"name": name, "color": color, "description": description})

    def create_issue(self, full: str, title: str, body: str, labels: list[str]) -> dict:
        return self._req("POST", f"/repos/{full}/issues",
                         json={"title": title, "body": body, "labels": labels}).json()
