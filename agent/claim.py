"""Logic to post claim rules when a bounty label is added."""
from __future__ import annotations

from .github import GitHub

CLAIM_MARKER = "<!-- ADT-CLAIM-RULES -->"

CLAIM_COMMENT = f"""{CLAIM_MARKER}
**How to claim:** comment "I'd like to take this". Start once a maintainer assigns you. Open a pull request within **5 days** of being assigned, or the task goes back to the pool.
**Payment:** $20 in the **weekly payout run**. Work merged Monday to Sunday is paid by the following Wednesday to mobile money or bank. After your first merged task you add payout details once, privately (never in an issue). [How bounties work](https://adt-community.github.io)
**About the reward:** ADT projects don't earn money yet. $20 is a thank-you, and the real value is shipping work on a live product that you can show employers. Contributors who keep shipping get first say in revenue sharing once projects earn."""


def post_claim_comment(gh: GitHub, repo: str, issue_number: int) -> bool:
    """
    Post the claim rules comment if it doesn't already exist.
    
    Returns True if a comment was posted, False if it already existed.
    """
    # Check existing comments
    comments = gh.get_issue_comments(repo, issue_number)
    
    for comment in comments:
        if CLAIM_MARKER in comment.get("body", ""):
            return False

    # Post the comment
    gh.create_issue_comment(repo, issue_number, CLAIM_COMMENT)
    return True
