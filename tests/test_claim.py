"""Tests for the claim comment logic."""
import unittest
from unittest.mock import MagicMock

from agent.claim import post_claim_comment, CLAIM_MARKER


class TestPostClaimComment(unittest.TestCase):
    def setUp(self):
        self.gh = MagicMock()
        self.repo = "ADT-community/test-repo"
        self.issue_number = 1

    def test_posts_comment_if_not_exists(self):
        self.gh.get_issue_comments.return_value = []
        
        result = post_claim_comment(self.gh, self.repo, self.issue_number)
        
        self.assertTrue(result)
        self.gh.create_issue_comment.assert_called_once()
        args, kwargs = self.gh.create_issue_comment.call_args
        self.assertEqual(args[0], self.repo)
        self.assertEqual(args[1], self.issue_number)
        self.assertIn(CLAIM_MARKER, args[2])

    def test_does_not_post_if_exists(self):
        self.gh.get_issue_comments.return_value = [
            {"body": "Some other comment"},
            {"body": f"{CLAIM_MARKER}\nHow to claim..."}
        ]
        
        result = post_claim_comment(self.gh, self.repo, self.issue_number)
        
        self.assertFalse(result)
        self.gh.create_issue_comment.assert_not_called()

    def test_does_not_post_if_exists_in_first_comment(self):
        self.gh.get_issue_comments.return_value = [
            {"body": f"{CLAIM_MARKER}\nHow to claim..."}
        ]
        
        result = post_claim_comment(self.gh, self.repo, self.issue_number)
        
        self.assertFalse(result)
        self.gh.create_issue_comment.assert_not_called()


if __name__ == "__main__":
    unittest.main()
