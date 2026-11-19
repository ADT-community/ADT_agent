import json
from pathlib import Path

import pytest

from agent import report


def test_generate_markdown(tmp_path: Path, monkeypatch):
    # Create a minimal projects.json with two enabled projects.
    projects_data = {
        "projects": [
            {"name": "proj1", "repo": "org/repo1", "enabled": True},
            {"name": "proj2", "repo": "org/repo2", "enabled": True},
        ]
    }
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(json.dumps(projects_data))

    # Stub out the GitHub‑dependent function to return deterministic stats.
    def fake_stats(repo: str):
        return {
            "open": 1,
            "claimed": 2,
            "in_review": 3,
            "stalled": 4,
            "proposals": 5,
        }

    monkeypatch.setattr(report, "get_bounty_stats", fake_stats)

    md = report.generate_markdown(projects_file)
    # Verify that the markdown contains a row for each repository with the fake numbers.
    assert "| org/repo1 | 1 | 2 | 3 | 4 | 5 |" in md
    assert "| org/repo2 | 1 | 2 | 3 | 4 | 5 |" in md
